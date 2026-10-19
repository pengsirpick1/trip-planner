from __future__ import annotations
import base64, os
import pytest
from app.credentials import CredentialCipher

def test_aes_gcm_roundtrip_and_tamper_rejection():
    cipher=CredentialCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())
    encrypted, nonce=cipher.encrypt("sk-never-log-this")
    assert cipher.decrypt(encrypted,nonce)=="sk-never-log-this"
    with pytest.raises(Exception): cipher.decrypt(encrypted[:-2]+"xx",nonce)

def test_invalid_master_key_is_rejected():
    with pytest.raises(ValueError): CredentialCipher("too-short")
