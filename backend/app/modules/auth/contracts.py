"""认证领域的稳定错误码与 API 输入输出模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class AuthErrorCode(StrEnum):
    """认证接口可安全返回给客户端的业务失败类型。"""

    EMAIL_ALREADY_REGISTERED = "email_already_registered"
    INVALID_CREDENTIALS = "invalid_credentials"
    ACCOUNT_DISABLED = "account_disabled"


class AuthError(RuntimeError):
    """认证领域可预期的业务错误。"""

    def __init__(self, code: AuthErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class RegisterRequest(BaseModel):
    """创建本地账号所需的最小信息。"""

    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=100)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        """删除误输入的空白并由 EmailStr 完成语法校验。"""
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        """拒绝全空白展示名，避免创建不可区分的账号。"""
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("展示名称不能为空白")
        return normalized


class LoginRequest(BaseModel):
    """本地账号密码登录输入。"""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class CurrentUserResponse(BaseModel):
    """可安全返回给前端的当前账号信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr | None
    display_name: str
    created_at: datetime


class AuthenticationResponse(BaseModel):
    """登录或注册成功后返回的 Bearer Token 与账号信息。"""

    access_token: str
    token_type: str = "bearer"
    user: CurrentUserResponse
