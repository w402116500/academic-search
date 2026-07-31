"""认证配置、密码哈希与 JWT 的最小安全边界。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.env import load_env


class AuthenticationConfigurationError(RuntimeError):
    """认证环境变量不完整，当前请求无法安全处理。"""


class InvalidAccessTokenError(ValueError):
    """访问令牌无法验证、过期或不符合本服务签发格式。"""


class AuthenticationSettings(BaseSettings):
    """认证模块的运行时配置。

    JWT 密钥不设置开发默认值，避免开发配置被意外带入生产环境。应用启动时
    不读取密钥，因此健康检查仍可使用；只有认证接口被调用时才会明确报错。
    """

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    auth_jwt_secret_key: SecretStr | None = None
    auth_jwt_issuer: str = "academic-search"
    auth_jwt_access_token_minutes: int = Field(default=60, ge=5, le=1440)

    def signing_secret(self) -> str:
        """取得非空 JWT 签名密钥，拒绝使用空值或示例值。"""
        secret = (
            self.auth_jwt_secret_key.get_secret_value().strip() if self.auth_jwt_secret_key else ""
        )
        if len(secret) < 32 or secret.startswith("change-me-"):
            raise AuthenticationConfigurationError("认证服务尚未配置安全的 AUTH_JWT_SECRET_KEY。")
        return secret


# Argon2id 是当前推荐的密码哈希算法；默认参数由 argon2-cffi 随版本维护。
_PASSWORD_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    """返回不可逆的 Argon2id 哈希，调用方绝不持久化密码明文。"""
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """安全验证密码；不将错误种类暴露给登录接口。"""
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def create_access_token(*, user_id: UUID, settings: AuthenticationSettings) -> str:
    """签发仅可用于本服务 API 的短生命周期访问令牌。"""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iss": settings.auth_jwt_issuer,
        "iat": now,
        "exp": now + timedelta(minutes=settings.auth_jwt_access_token_minutes),
    }
    return jwt.encode(payload, settings.signing_secret(), algorithm="HS256")


def read_access_token(*, token: str, settings: AuthenticationSettings) -> UUID:
    """验证令牌签名和声明，并返回经过校验的用户标识。"""
    try:
        payload = jwt.decode(
            token,
            settings.signing_secret(),
            algorithms=["HS256"],
            issuer=settings.auth_jwt_issuer,
            options={"require": ["sub", "type", "iss", "iat", "exp"]},
        )
        if payload.get("type") != "access":
            raise InvalidAccessTokenError("令牌用途不正确")
        return UUID(str(payload["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        if isinstance(exc, InvalidAccessTokenError):
            raise
        raise InvalidAccessTokenError("访问令牌无效或已过期") from exc


def get_authentication_settings() -> AuthenticationSettings:
    """加载一次请求可共享的认证环境配置。"""
    load_env()
    return AuthenticationSettings()
