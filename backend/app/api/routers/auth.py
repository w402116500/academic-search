"""本地账号认证路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps.auth import get_current_user
from app.api.deps.services import get_authentication_service
from app.core.security import (
    AuthenticationConfigurationError,
    AuthenticationSettings,
    create_access_token,
    get_authentication_settings,
)
from app.modules.auth.contracts import (
    AuthenticationResponse,
    AuthError,
    AuthErrorCode,
    CurrentUserResponse,
    LoginRequest,
    RegisterRequest,
)
from app.modules.auth.models import UserAccount
from app.modules.auth.service import AuthenticationService

router = APIRouter(prefix="/auth", tags=["认证"])


def _auth_error_response(error: AuthError) -> HTTPException:
    """将领域错误映射为稳定的 HTTP 响应，不泄漏密码或哈希细节。"""
    status_code = (
        status.HTTP_409_CONFLICT
        if error.code is AuthErrorCode.EMAIL_ALREADY_REGISTERED
        else status.HTTP_403_FORBIDDEN
        if error.code is AuthErrorCode.ACCOUNT_DISABLED
        else status.HTTP_401_UNAUTHORIZED
    )
    headers = (
        {"WWW-Authenticate": "Bearer"} if status_code == status.HTTP_401_UNAUTHORIZED else None
    )
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
        headers=headers,
    )


def _authentication_settings_or_unavailable() -> AuthenticationSettings:
    """在执行认证副作用前确认 JWT 配置可安全签名。"""
    try:
        settings = get_authentication_settings()
        settings.signing_secret()
    except AuthenticationConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "authentication_unavailable", "message": "认证服务暂不可用。"},
        ) from exc
    return settings


def _authentication_response(
    user: UserAccount, settings: AuthenticationSettings
) -> AuthenticationResponse:
    """以同一格式返回注册和登录成功结果。"""
    token = create_access_token(user_id=user.id, settings=settings)
    return AuthenticationResponse(access_token=token, user=CurrentUserResponse.model_validate(user))


@router.post(
    "/register",
    response_model=AuthenticationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="注册本地账号",
)
async def register(
    request: RegisterRequest,
    service: Annotated[AuthenticationService, Depends(get_authentication_service)],
) -> AuthenticationResponse:
    """创建账号并立即返回访问令牌，邮箱验证将在后续独立接入。"""
    # 注册会写入用户表，因此必须先确认能够签发令牌，避免账号半成功。
    settings = _authentication_settings_or_unavailable()
    try:
        user = await service.register(request)
    except AuthError as exc:
        raise _auth_error_response(exc) from exc
    return _authentication_response(user, settings)


@router.post("/login", response_model=AuthenticationResponse, summary="使用邮箱密码登录")
async def login(
    request: LoginRequest,
    service: Annotated[AuthenticationService, Depends(get_authentication_service)],
) -> AuthenticationResponse:
    """验证本地账号密码并签发新的短生命周期访问令牌。"""
    settings = _authentication_settings_or_unavailable()
    try:
        user = await service.authenticate(request)
    except AuthError as exc:
        raise _auth_error_response(exc) from exc
    return _authentication_response(user, settings)


@router.get("/me", response_model=CurrentUserResponse, summary="获取当前登录账号")
async def get_me(
    current_user: Annotated[UserAccount, Depends(get_current_user)],
) -> CurrentUserResponse:
    """让前端在刷新后确认 Bearer Token 对应的登录用户。"""
    return CurrentUserResponse.model_validate(current_user)
