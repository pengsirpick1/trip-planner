"""BYOK credentials: AES-256-GCM encryption and tenant-scoped resolution."""
from __future__ import annotations
import asyncio, base64, json, os, urllib.error, urllib.request, uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from .errors import ProviderAuthError
from .persistence.sqlalchemy_models import ProviderCredentialRow

SUPPORTED_PROVIDERS = {"openai", "deepseek"}
DEFAULTS = {
    "openai": ("https://api.openai.com/v1", None),
    "deepseek": ("https://api.deepseek.com/v1", None),
}

@dataclass(frozen=True)
class ResolvedCredential:
    provider: str; api_key: str; base_url: str; model: str | None; source: str = "BYOK"

class CredentialCipher:
    def __init__(self, encoded_key: str):
        try: key = base64.urlsafe_b64decode(encoded_key + "=" * (-len(encoded_key) % 4))
        except Exception as error: raise ValueError("CREDENTIAL_ENCRYPTION_KEY 必须是 base64 编码的 32 字节密钥") from error
        if len(key) != 32: raise ValueError("CREDENTIAL_ENCRYPTION_KEY 必须是 32 字节 AES-256 密钥")
        self.aes = AESGCM(key)
    def encrypt(self, plaintext: str) -> tuple[str, str]:
        nonce = os.urandom(12)
        ciphertext = self.aes.encrypt(nonce, plaintext.encode(), None)
        return base64.urlsafe_b64encode(ciphertext).decode(), base64.urlsafe_b64encode(nonce).decode()
    def decrypt(self, ciphertext: str, nonce: str) -> str:
        return self.aes.decrypt(base64.urlsafe_b64decode(nonce), base64.urlsafe_b64decode(ciphertext), None).decode()

class CredentialRepository:
    def __init__(self, sessions: async_sessionmaker, cipher: CredentialCipher): self.sessions, self.cipher = sessions, cipher
    async def upsert(self, user_id: str, provider: str, api_key: str, base_url: str | None, model: str | None):
        if provider not in SUPPORTED_PROVIDERS: raise ValueError("仅支持 openai 或 deepseek")
        if not api_key.strip(): raise ValueError("API Key 不能为空")
        encrypted, nonce = self.cipher.encrypt(api_key.strip()); now = datetime.now(UTC)
        async with self.sessions() as s:
            async with s.begin():
                row = await s.scalar(select(ProviderCredentialRow).where(ProviderCredentialRow.user_id==user_id, ProviderCredentialRow.provider==provider))
                if row is None:
                    has_active = await s.scalar(select(ProviderCredentialRow.id).where(ProviderCredentialRow.user_id==user_id, ProviderCredentialRow.is_active==True))
                    row = ProviderCredentialRow(id=uuid.uuid4().hex, user_id=user_id, provider=provider, encrypted_api_key=encrypted, nonce=nonce, key_version=1, base_url=(base_url or DEFAULTS[provider][0]).rstrip("/"), model=model, is_active=not bool(has_active), created_at=now, updated_at=now, last_verified_at=None, last_error_code=None); s.add(row)
                else:
                    row.encrypted_api_key, row.nonce, row.base_url, row.model, row.key_version, row.updated_at, row.last_error_code = encrypted, nonce, (base_url or DEFAULTS[provider][0]).rstrip("/"), model, row.key_version + 1, now, None
                return row
    async def resolve(self, user_id: str | None, provider: str | None = None):
        if not user_id: return None
        async with self.sessions() as s:
            stmt=select(ProviderCredentialRow).where(ProviderCredentialRow.user_id==user_id)
            if provider: stmt=stmt.where(ProviderCredentialRow.provider==provider)
            else: stmt=stmt.where(ProviderCredentialRow.is_active==True)
            row=await s.scalar(stmt.order_by(ProviderCredentialRow.updated_at.desc()))
            if row is None: return None
            try: key=self.cipher.decrypt(row.encrypted_api_key,row.nonce)
            except Exception as error: raise ProviderAuthError(internal_context={"provider":row.provider,"reason":"credential_decrypt_failed"}) from error
            return ResolvedCredential(row.provider,key,row.base_url,row.model)
    async def list_for_user(self,user_id):
        async with self.sessions() as s:
            rows=(await s.scalars(select(ProviderCredentialRow).where(ProviderCredentialRow.user_id==user_id).order_by(ProviderCredentialRow.provider))).all()
            return [{"provider":r.provider,"base_url":r.base_url,"model":r.model,"is_active":r.is_active,"key_version":r.key_version,"last_verified_at":r.last_verified_at,"last_error_code":r.last_error_code,"updated_at":r.updated_at} for r in rows]
    async def activate(self,user_id,provider):
        async with self.sessions() as s:
            async with s.begin():
                row=await s.scalar(select(ProviderCredentialRow).where(ProviderCredentialRow.user_id==user_id,ProviderCredentialRow.provider==provider))
                if row is None:return False
                rows=(await s.scalars(select(ProviderCredentialRow).where(ProviderCredentialRow.user_id==user_id))).all()
                for item in rows:item.is_active=(item.id==row.id)
                row.updated_at=datetime.now(UTC); return True
    async def verify(self,user_id,provider):
        credential=await self.resolve(user_id,provider)
        if credential is None: return False,"CREDENTIAL_NOT_FOUND"
        def request_models():
            req=urllib.request.Request(credential.base_url.rstrip("/")+"/models",headers={"Authorization":"Bearer "+credential.api_key})
            with urllib.request.urlopen(req,timeout=10) as response: json.loads(response.read(65536)); return True,None
        try: success,code=await asyncio.to_thread(request_models)
        except urllib.error.HTTPError as error: success,code=False,("PROVIDER_AUTH_FAILED" if error.code in {401,403} else "PROVIDER_VERIFY_FAILED")
        except Exception: success,code=False,"PROVIDER_NETWORK_ERROR"
        async with self.sessions() as s:
            async with s.begin():
                row=await s.scalar(select(ProviderCredentialRow).where(ProviderCredentialRow.user_id==user_id,ProviderCredentialRow.provider==provider))
                if row: row.last_verified_at=datetime.now(UTC) if success else row.last_verified_at; row.last_error_code=code
        return success,code
    async def delete(self,user_id,provider):
        async with self.sessions() as s:
            async with s.begin():
                row=await s.scalar(select(ProviderCredentialRow).where(ProviderCredentialRow.user_id==user_id,ProviderCredentialRow.provider==provider))
                if row is None:return False
                await s.delete(row); return True
