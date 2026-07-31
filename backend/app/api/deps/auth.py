"""FastAPI 路由使用的当前用户身份依赖。"""

from __future__ import annotations

from typing import Annotated

from app.core.security import (
    AuthenticationConfigurationError,
    InvalidAccessTokenError,
    get_authentication_settings,
    read_access_token,
)
from app.db.models.user import User
from app.db.session import get_db_session
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
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

    statement = select(User).where(User.id == user_id, User.status == "active")
    user = await session.scalar(statement)
    if user is None:
        raise _unauthorized()
    return user
