"""Tenant-scoped virtual quota read APIs. Credits are not mutable via HTTP."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from ...identity import AuthenticatedUser
from ..auth_dependencies import current_user

router = APIRouter(prefix="/quota", tags=["quota"])

def _repo(request: Request):
    repo = getattr(request.app.state, "quota_repository", None) or getattr(getattr(request.app.state, "travel_app", None), "quota_repository", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="额度持久化当前不可用")
    return repo

@router.get("")
async def get_quota(request: Request, user: AuthenticatedUser | None = Depends(current_user)):
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    account = await _repo(request).get_account_for_user(user.id)
    if account is None:
        raise HTTPException(status_code=404, detail="额度账户不存在")
    return account

@router.get("/ledger")
async def get_ledger(request: Request, limit: int = Query(50, ge=1, le=100), user: AuthenticatedUser | None = Depends(current_user)):
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return {"items": await _repo(request).get_ledger_for_user(user.id, limit)}
