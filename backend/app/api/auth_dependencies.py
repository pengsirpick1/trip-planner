"""FastAPI 身份依赖：开发可匿名、生产可强制 Bearer Token。"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..identity import AuthError, AuthenticatedUser
from ..errors import AuthenticationError

bearer = HTTPBearer(auto_error=False)


async def current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> AuthenticatedUser | None:
    settings = request.app.state.settings
    if credentials is None:
        if settings.auth_required:
            raise AuthenticationError()
        return None
    if credentials.scheme.lower() != "bearer":
        raise AuthenticationError()
    try:
        identity = request.app.state.auth_service.decode_access_token(credentials.credentials)
    except AuthError as error:
        raise AuthenticationError() from error
    user = await request.app.state.user_repository.get_by_id(identity.id)
    if user is None:
        raise AuthenticationError() 
    return AuthenticatedUser(id=user.id, username=user.username)
