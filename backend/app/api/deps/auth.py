"""FastAPI 路由使用的当前用户身份依赖。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps.services import get_user_repository
from app.core.security import (
    AuthenticationConfigurationError,
    InvalidAccessTokenError,
    get_authentication_settings,
    read_access_token,
)
from app.modules.auth.models import UserAccount
from app.modules.auth.repository import UserRepository

_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    """构造不泄漏令牌具体失败原因的统一 401 响应。"""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_access_token", "message": "登录状态无效或已过期。"},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    users: Annotated[UserRepository, Depends(get_user_repository)],
) -> UserAccount:
    """从 Bearer JWT 解析并确认当前仍处于活动状态的用户。"""
    if credentials is None:
        raise _unauthorized()
    try:
        user_id = read_access_token(
            token=credentials.credentials, settings=get_authentication_settings()
        )
    except AuthenticationConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "authentication_unavailable", "message": "认证服务暂不可用。"},
        ) from exc
    except InvalidAccessTokenError as exc:
        raise _unauthorized() from exc

    user = await users.find_active_by_id(user_id)
    if user is None:
        raise _unauthorized()
    return user
