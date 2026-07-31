"""本地账号注册、登录与当前用户查询能力。"""

from app.modules.auth.contracts import AuthError, AuthErrorCode
from app.modules.auth.service import AuthenticationService

__all__ = ["AuthError", "AuthErrorCode", "AuthenticationService"]
