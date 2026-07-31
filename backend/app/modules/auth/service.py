"""本地账号认证的数据库事务和密码校验。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.security import hash_password, verify_password
from app.db.models.user import User
from app.modules.auth.contracts import AuthError, AuthErrorCode, LoginRequest, RegisterRequest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


class AuthenticationService:
    """封装本地账号查询与写入，路由层不直接操作密码字段。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register(self, request: RegisterRequest) -> User:
        """创建可登录账号，并将密码明文立即转换为 Argon2id 哈希。"""
        try:
            async with self._session.begin():
                existing = await self._user_by_email(str(request.email))
                if existing is not None:
                    raise AuthError(AuthErrorCode.EMAIL_ALREADY_REGISTERED, "该邮箱已注册。")

                user = User(
                    email=str(request.email),
                    password_hash=hash_password(request.password),
                    password_updated_at=datetime.now(UTC),
                    display_name=request.display_name,
                    status="active",
                )
                self._session.add(user)
                await self._session.flush()
        except IntegrityError as exc:
            # 并发注册时仍由数据库的大小写无关唯一索引担任最终防线。
            raise AuthError(AuthErrorCode.EMAIL_ALREADY_REGISTERED, "该邮箱已注册。") from exc
        return user

    async def authenticate(self, request: LoginRequest) -> User:
        """验证账号和密码；账号不存在与密码错误使用相同提示。"""
        user = await self._user_by_email(str(request.email))
        if (
            user is None
            or not user.password_hash
            or not verify_password(request.password, user.password_hash)
        ):
            raise AuthError(AuthErrorCode.INVALID_CREDENTIALS, "邮箱或密码不正确。")
        if user.status != "active":
            raise AuthError(AuthErrorCode.ACCOUNT_DISABLED, "当前账号已被停用。")
        return user

    async def _user_by_email(self, email: str) -> User | None:
        """按大小写无关规则查询本地账号。"""
        statement = select(User).where(func.lower(User.email) == email.lower())
        return await self._session.scalar(statement)
