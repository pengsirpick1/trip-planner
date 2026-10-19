"""注册、登录及用户长期记忆管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ...identity import AuthError, AuthenticatedUser
from ..auth_dependencies import current_user

router = APIRouter(prefix="/auth", tags=["auth"])
memory_router = APIRouter(prefix="/users/me", tags=["user-memory"])


class CredentialsRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: str
    username: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


def _auth(request: Request):
    return request.app.state.auth_service


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: CredentialsRequest, request: Request):
    try:
        username, password = _auth(request).validate_credentials(payload.username, payload.password)
        record = await request.app.state.user_repository.create_user(username, _auth(request).hash_password(password))
    except (AuthError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT if str(error) == "用户名已存在" else 422, detail=str(error)) from error
    user = AuthenticatedUser(id=record.id, username=record.username)
    return TokenResponse(access_token=_auth(request).issue_access_token(user), user=UserResponse(id=user.id, username=user.username))


@router.post("/login", response_model=TokenResponse)
async def login(payload: CredentialsRequest, request: Request):
    username = payload.username.strip()
    record = await request.app.state.user_repository.get_by_username(username)
    if record is None or not _auth(request).verify_password(payload.password, record.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误", headers={"WWW-Authenticate": "Bearer"})
    await request.app.state.user_repository.touch_login(record.id)
    user = AuthenticatedUser(id=record.id, username=record.username)
    return TokenResponse(access_token=_auth(request).issue_access_token(user), user=UserResponse(id=user.id, username=user.username))


@router.get("/me", response_model=UserResponse)
async def me(request: Request, user: AuthenticatedUser | None = Depends(current_user)):
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return UserResponse(id=user.id, username=user.username)


@memory_router.get("/memories")
async def list_memories(request: Request, user: AuthenticatedUser | None = Depends(current_user)):
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    records = await request.app.state.user_repository.list_memories(user.id)
    return {"items": [record.model_dump() for record in records]}


@memory_router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: str, request: Request, user: AuthenticatedUser | None = Depends(current_user)):
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    if not await request.app.state.user_repository.delete_memory(user.id, memory_id):
        raise HTTPException(status_code=404, detail="记忆不存在")
