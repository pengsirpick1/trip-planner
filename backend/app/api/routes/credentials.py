"""Tenant-scoped BYOK management.  API keys are write-only."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from ...identity import AuthenticatedUser
from ..auth_dependencies import current_user

router=APIRouter(prefix="/provider-credentials",tags=["provider-credentials"])
class CredentialUpsert(BaseModel):
    provider: str = Field(pattern="^(openai|deepseek)$")
    api_key: str = Field(min_length=8,max_length=512)
    base_url: str | None = Field(default=None,max_length=512)
    model: str | None = Field(default=None,max_length=160)
def _repo(request):
    repo=getattr(getattr(request.app.state,"travel_app",None),"credential_repository",None)
    if repo is None: raise HTTPException(503,"BYOK 未配置：请设置 CREDENTIAL_ENCRYPTION_KEY 并使用 MySQL")
    return repo
@router.get("")
async def list_credentials(request:Request,user:AuthenticatedUser=Depends(current_user)):
    return {"items":await _repo(request).list_for_user(user.id)}
@router.put("/{provider}",status_code=status.HTTP_204_NO_CONTENT)
async def put_credential(provider:str,payload:CredentialUpsert,request:Request,user:AuthenticatedUser=Depends(current_user)):
    if provider != payload.provider: raise HTTPException(422,"路径 provider 与请求体不一致")
    try: await _repo(request).upsert(user.id,provider,payload.api_key,payload.base_url,payload.model)
    except ValueError as error: raise HTTPException(422,str(error)) from error
@router.delete("/{provider}",status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(provider:str,request:Request,user:AuthenticatedUser=Depends(current_user)):
    if provider not in {"openai","deepseek"}: raise HTTPException(404,"不支持的 provider")
    if not await _repo(request).delete(user.id,provider): raise HTTPException(404,"凭证不存在")
@router.post("/{provider}/activate",status_code=status.HTTP_204_NO_CONTENT)
async def activate_credential(provider:str,request:Request,user:AuthenticatedUser=Depends(current_user)):
    if provider not in {"openai","deepseek"} or not await _repo(request).activate(user.id,provider): raise HTTPException(404,"凭证不存在")
@router.post("/{provider}/verify")
async def verify_credential(provider:str,request:Request,user:AuthenticatedUser=Depends(current_user)):
    if provider not in {"openai","deepseek"}: raise HTTPException(404,"不支持的 provider")
    success, code=await _repo(request).verify(user.id,provider)
    if not success: raise HTTPException(422,code or "PROVIDER_VERIFY_FAILED")
    return {"provider":provider,"verified":True}
