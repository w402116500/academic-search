"""本地账号认证和 JWT 安全边界的离线测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.api.routers import auth as auth_router
from app.core.security import (
    AuthenticationSettings,
    InvalidAccessTokenError,
    create_access_token,
    read_access_token,
    verify_password,
)
from app.modules.auth.contracts import AuthError, AuthErrorCode, LoginRequest, RegisterRequest
from app.modules.auth.models import UserAccount
from app.modules.auth.repository import CreateLocalUser, UserEmailConflictError
from app.modules.auth.service import AuthenticationService
from fastapi import HTTPException
from pydantic import SecretStr

_USER_ID = UUID("00000000-0000-0000-0000-000000000101")
_SETTINGS = AuthenticationSettings(auth_jwt_secret_key=SecretStr("x" * 48))


class FakeUserRepository:
    """Authentication port replacement without SQLAlchemy or a database."""

    def __init__(self, users: list[UserAccount] | None = None) -> None:
        self.users = list(users or [])
        self.created_commands: list[CreateLocalUser] = []

    async def create_local_user(self, command: CreateLocalUser) -> UserAccount:
        if any(user.email == command.email for user in self.users):
            raise UserEmailConflictError
        self.created_commands.append(command)
        user = UserAccount(
            id=_USER_ID,
            email=command.email,
            password_hash=command.password_hash,
            password_updated_at=command.password_updated_at,
            display_name=command.display_name,
            status="active",
            created_at=datetime.now(UTC),
        )
        self.users.append(user)
        return user

    async def find_by_email(self, email: str) -> UserAccount | None:
        return next((user for user in self.users if user.email == email), None)

    async def find_active_by_id(self, user_id: UUID) -> UserAccount | None:
        return next(
            (user for user in self.users if user.id == user_id and user.status == "active"),
            None,
        )


def _user(*, password: str = "a secure test password", status: str = "active") -> UserAccount:
    """创建内存中的本地账号，不需要 PostgreSQL。"""
    from app.core.security import hash_password

    return UserAccount(
        id=_USER_ID,
        email="researcher@example.com",
        password_hash=hash_password(password),
        password_updated_at=datetime.now(UTC),
        display_name="Researcher",
        status=status,
        created_at=datetime.now(UTC),
    )


def test_access_token_round_trip_rejects_a_different_issuer() -> None:
    """签发令牌只应被同一服务配置和同一 issuer 接受。"""
    token = create_access_token(user_id=_USER_ID, settings=_SETTINGS)

    assert read_access_token(token=token, settings=_SETTINGS) == _USER_ID

    other_issuer = AuthenticationSettings(
        auth_jwt_secret_key=SecretStr("x" * 48),
        auth_jwt_issuer="other-service",
    )
    with pytest.raises(InvalidAccessTokenError):
        read_access_token(token=token, settings=other_issuer)


@pytest.mark.asyncio
async def test_register_hashes_the_password_and_normalizes_the_email() -> None:
    """注册服务不得将密码明文写入用户模型。"""
    users = FakeUserRepository()
    service = AuthenticationService(users)

    user = await service.register(
        RegisterRequest(
            email="  Researcher@Example.Com  ",
            password="a secure test password",
            display_name="  Ada   Lovelace ",
        )
    )

    assert len(users.created_commands) == 1
    assert user.email == "researcher@example.com"
    assert user.display_name == "Ada Lovelace"
    assert user.password_hash != "a secure test password"
    assert verify_password("a secure test password", str(user.password_hash))


@pytest.mark.asyncio
async def test_register_rejects_an_existing_email() -> None:
    """服务层在数据库唯一索引前先返回清晰且稳定的冲突错误。"""
    service = AuthenticationService(FakeUserRepository([_user()]))

    with pytest.raises(AuthError) as error:
        await service.register(
            RegisterRequest(
                email="researcher@example.com",
                password="a secure test password",
                display_name="Ada",
            )
        )

    assert error.value.code is AuthErrorCode.EMAIL_ALREADY_REGISTERED


@pytest.mark.asyncio
async def test_register_does_not_write_an_account_without_a_jwt_signing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """认证配置缺失时，路由必须在进入注册数据库事务前返回 503。"""
    users = FakeUserRepository()

    def invalid_settings() -> AuthenticationSettings:
        return AuthenticationSettings(auth_jwt_secret_key=SecretStr("change-me-" + "x" * 48))

    async def unexpected_register(
        _self: AuthenticationService, _request: RegisterRequest
    ) -> UserAccount:
        raise AssertionError("JWT 配置无效时不应创建用户")

    monkeypatch.setattr(auth_router, "get_authentication_settings", invalid_settings)
    monkeypatch.setattr(AuthenticationService, "register", unexpected_register)

    with pytest.raises(HTTPException) as error:
        await auth_router.register(
            RegisterRequest(
                email="researcher@example.com",
                password="a secure test password",
                display_name="Researcher",
            ),
            AuthenticationService(users),
        )

    assert error.value.status_code == 503
    assert users.created_commands == []


@pytest.mark.asyncio
async def test_authenticate_hides_unknown_user_and_bad_password_behind_one_error() -> None:
    """登录失败不应帮助攻击者枚举已注册邮箱。"""
    missing_users = FakeUserRepository()
    existing_users = FakeUserRepository([_user(password="another secure password")])
    request = LoginRequest(email="researcher@example.com", password="wrong password")

    for users in (missing_users, existing_users):
        with pytest.raises(AuthError) as error:
            await AuthenticationService(users).authenticate(request)
        assert error.value.code is AuthErrorCode.INVALID_CREDENTIALS
