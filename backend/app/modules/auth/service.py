"""本地账号认证的数据库事务和密码校验。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.security import hash_password, verify_password
from app.modules.auth.contracts import AuthError, AuthErrorCode, LoginRequest, RegisterRequest
from app.modules.auth.models import UserAccount
from app.modules.auth.repository import (
    CreateLocalUser,
    UserEmailConflictError,
    UserRepository,
)


class AuthenticationService:
    """封装本地账号查询与写入，路由层不直接操作密码字段。"""

    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def register(self, request: RegisterRequest) -> UserAccount:
        """创建可登录账号，并将密码明文立即转换为 Argon2id 哈希。"""
        try:
            return await self._users.create_local_user(
                CreateLocalUser(
                    email=str(request.email),
                    password_hash=hash_password(request.password),
                    password_updated_at=datetime.now(UTC),
                    display_name=request.display_name,
                )
            )
        except UserEmailConflictError as exc:
            raise AuthError(AuthErrorCode.EMAIL_ALREADY_REGISTERED, "该邮箱已注册。") from exc

    async def authenticate(self, request: LoginRequest) -> UserAccount:
        """验证账号和密码；账号不存在与密码错误使用相同提示。"""
        user = await self._users.find_by_email(str(request.email))
        if (
            user is None
            or not user.password_hash
            or not verify_password(request.password, user.password_hash)
        ):
            raise AuthError(AuthErrorCode.INVALID_CREDENTIALS, "邮箱或密码不正确。")
        if user.status != "active":
            raise AuthError(AuthErrorCode.ACCOUNT_DISABLED, "当前账号已被停用。")
        return user
