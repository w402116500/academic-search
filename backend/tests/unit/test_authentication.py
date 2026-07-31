"""本地账号认证和 JWT 安全边界的离线测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from app.core.security import (
    AuthenticationSettings,
    InvalidAccessTokenError,
    create_access_token,
    read_access_token,
    verify_password,
)
from app.db.models.user import User
from app.modules.auth.contracts import AuthError, AuthErrorCode, LoginRequest, RegisterRequest
from app.modules.auth.service import AuthenticationService
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

_USER_ID = UUID("00000000-0000-0000-0000-000000000101")
_SETTINGS = AuthenticationSettings(auth_jwt_secret_key=SecretStr("x" * 48))


class FakeSession:
    """认证服务所需的最小异步 SQLAlchemy 会话替身。"""

    def __init__(self, scalar_values: list[object | None]) -> None:
        self._scalar_values = iter(scalar_values)
        self.added: list[object] = []

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        yield self

    async def scalar(self, _statement: object) -> object | None:
        return next(self._scalar_values)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None


def _user(*, password: str = "a secure test password", status: str = "active") -> User:
    """创建内存中的本地账号，不需要 PostgreSQL。"""
    from app.core.security import hash_password

    return User(
        id=_USER_ID,
        email="researcher@example.com",
        password_hash=hash_password(password),
        password_updated_at=datetime.now(UTC),
        display_name="Researcher",
        status=status,
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
    session = FakeSession([None])
    service = AuthenticationService(cast(AsyncSession, session))

    user = await service.register(
        RegisterRequest(
            email="  Researcher@Example.Com  ",
            password="a secure test password",
            display_name="  Ada   Lovelace ",
        )
    )

    assert session.added == [user]
    assert user.email == "researcher@example.com"
    assert user.display_name == "Ada Lovelace"
    assert user.password_hash != "a secure test password"
    assert verify_password("a secure test password", str(user.password_hash))


@pytest.mark.asyncio
async def test_register_rejects_an_existing_email() -> None:
    """服务层在数据库唯一索引前先返回清晰且稳定的冲突错误。"""
    session = FakeSession([_user()])
    service = AuthenticationService(cast(AsyncSession, session))

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
async def test_authenticate_hides_unknown_user_and_bad_password_behind_one_error() -> None:
    """登录失败不应帮助攻击者枚举已注册邮箱。"""
    missing_session = FakeSession([None])
    existing_session = FakeSession([_user(password="another secure password")])
    request = LoginRequest(email="researcher@example.com", password="wrong password")

    for session in (missing_session, existing_session):
        with pytest.raises(AuthError) as error:
            await AuthenticationService(cast(AsyncSession, session)).authenticate(request)
        assert error.value.code is AuthErrorCode.INVALID_CREDENTIALS
