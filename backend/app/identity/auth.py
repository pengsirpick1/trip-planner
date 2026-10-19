"""无额外第三方依赖的密码哈希与 Bearer access token。

密码仅保存 PBKDF2-HMAC-SHA256 派生值；token 是带过期时间的 HMAC 签名载荷，
不把密码或用户偏好放入 token。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass


class AuthError(ValueError):
    pass


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    username: str


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class AuthService:
    algorithm = "pbkdf2_sha256"
    iterations = 600_000

    def __init__(self, secret: str, token_ttl_minutes: int = 60 * 24 * 7) -> None:
        if len(secret) < 24:
            raise ValueError("AUTH_SECRET_KEY 至少需要 24 个字符")
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = max(1, int(token_ttl_minutes)) * 60

    @classmethod
    def validate_credentials(cls, username: str, password: str) -> tuple[str, str]:
        username = username.strip()
        if not 3 <= len(username) <= 64:
            raise AuthError("用户名长度应为 3 到 64 个字符")
        if not 8 <= len(password) <= 128:
            raise AuthError("密码长度应为 8 到 128 个字符")
        return username, password

    @classmethod
    def hash_password(cls, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, cls.iterations)
        return f"{cls.algorithm}${cls.iterations}${_b64encode(salt)}${_b64encode(digest)}"

    @classmethod
    def verify_password(cls, password: str, encoded: str) -> bool:
        try:
            algorithm, iterations, salt, expected = encoded.split("$", 3)
            if algorithm != cls.algorithm:
                return False
            actual = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), _b64decode(salt), int(iterations)
            )
            return hmac.compare_digest(actual, _b64decode(expected))
        except (ValueError, TypeError):
            return False

    def issue_access_token(self, user: AuthenticatedUser) -> str:
        payload = json.dumps(
            {"sub": user.id, "username": user.username, "exp": int(time.time()) + self._ttl_seconds},
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        body = _b64encode(payload)
        signature = _b64encode(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def decode_access_token(self, token: str) -> AuthenticatedUser:
        try:
            body, signature = token.split(".", 1)
            expected = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _b64decode(signature)):
                raise AuthError("登录凭证签名无效")
            payload = json.loads(_b64decode(body))
            if int(payload["exp"]) < int(time.time()):
                raise AuthError("登录凭证已过期")
            return AuthenticatedUser(id=str(payload["sub"]), username=str(payload["username"]))
        except AuthError:
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise AuthError("登录凭证格式无效") from error
