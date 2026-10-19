from __future__ import annotations
import asyncio
import json
import urllib.error

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.errors import ProviderAuthError, ProviderRateLimitError, ProviderTimeoutError, ProviderUnavailableError, sanitize_exception
from app.providers.openai_compatible import OpenAICompatibleToolCallingLLM
from app.config import Settings
from app.telemetry.gateway import TelemetryLLMGateway
from app.errors import ProviderRateLimitError, ProviderTimeoutError

def _settings(transport):
    return Settings(llm_api_key="test-key", llm_base_url="https://provider.invalid/v1", llm_model="test")

def test_sanitizer_never_keeps_common_secrets():
    value = sanitize_exception("Bearer abc.def mysql://user:password@host/db sk-secret-example?api_key=abc")
    assert ":password@" not in value and "sk-secret-example" not in value and "abc.def" not in value

@pytest.mark.parametrize("status,error_type", [(401, ProviderAuthError), (429, ProviderRateLimitError), (500, ProviderUnavailableError)])
def test_provider_http_status_maps_to_stable_typed_error(status, error_type):
    def transport(request, timeout):
        body = json.dumps({"error":{"code":"x","message":"sk-secret-example"}}).encode()
        raise urllib.error.HTTPError(request.full_url, status, "failed", {}, __import__('io').BytesIO(body))
    provider = OpenAICompatibleToolCallingLLM(_settings(transport), transport=transport)
    with pytest.raises(error_type): asyncio.run(provider._request({"model":"test","messages":[]}))

def test_provider_timeout_is_retryable():
    def transport(request, timeout): raise TimeoutError()
    provider = OpenAICompatibleToolCallingLLM(_settings(transport), transport=transport)
    with pytest.raises(ProviderTimeoutError) as caught: asyncio.run(provider._request({"model":"test","messages":[]}))
    assert caught.value.retryable is True

def test_app_error_contract_contains_trace_and_no_secret():
    app = FastAPI()
    from app.errors import ProviderAuthError
    from app.trace_context import new_trace_id
    from fastapi.responses import JSONResponse
    @app.exception_handler(ProviderAuthError)
    async def handler(request, error): return JSONResponse(status_code=error.http_status, content={"code":error.code,"message":error.public_message,"trace_id":new_trace_id()})
    @app.get("/boom")
    async def boom(): raise ProviderAuthError(internal_context={"raw":"sk-secret-example"})
    response = TestClient(app).get("/boom")
    assert response.status_code == 503 and set(response.json()) == {"code","message","trace_id"}
    assert "secret" not in response.text

def test_gateway_retries_only_confirmed_no_usage_provider_failure():
    class Provider:
        def __init__(self): self.calls = 0
        async def decide_with_telemetry(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ProviderRateLimitError(internal_context={"confirmed_no_usage": True})
            return {"final": "ok", "usage": {}}, [{"kind":"provider","status":"SUCCEEDED","usage":{}}]
    async def run():
        provider = Provider(); result = await TelemetryLLMGateway(provider).decide(None, [])
        assert result["final"] == "ok" and provider.calls == 2
    asyncio.run(run())

def test_gateway_does_not_retry_timeout_with_unknown_outcome():
    class Provider:
        def __init__(self): self.calls = 0
        async def decide_with_telemetry(self, *args, **kwargs):
            self.calls += 1
            raise ProviderTimeoutError(internal_context={"confirmed_no_usage": False})
    async def run():
        provider = Provider()
        with pytest.raises(ProviderTimeoutError): await TelemetryLLMGateway(provider).decide(None, [])
        assert provider.calls == 1
    asyncio.run(run())
