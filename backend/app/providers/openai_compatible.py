"""OpenAI 兼容 Provider adapter（迁自 travel_agent/agent/llm.py）。

Phase B 修复说明（审计 G21，最小范围）：
- decide()/generate_structured() 都把 usage 随返回值交付（ReAct 在本地聚合），
  generate_structured 每次调用前重置 self.structured_attempts；
- self.usage 仅为“最近一次请求”的观测值，不用于跨阶段累计；
  真正的多阶段/并发归因（每次调用返回独立 usage 快照 + 调用方记账）在
  Phase H 的成本/遥测改造中收敛。
"""

import asyncio
import json
import time
import urllib.error
import urllib.request

from ..config import Settings
from .protocols import is_validation_error, parse_structured_content
from ..errors import (ProviderAuthError, ProviderInvalidResponseError, ProviderNetworkError,
    ProviderQuotaError, ProviderRateLimitError, ProviderTimeoutError, ProviderUnavailableError)


class OpenAICompatibleToolCallingLLM:
    def __init__(self, config: Settings, transport=None):
        self.config = config
        self.transport = transport

    def _require_config(self) -> None:
        if not (
            self.config.llm_api_key
            and self.config.llm_api_key.get_secret_value()
            and self.config.llm_base_url
            and self.config.llm_model
        ):
            raise ProviderAuthError(internal_context={"provider": "openai_compatible", "reason": "not_configured"})

    async def _request(self, payload: dict, credential=None):
        started = time.monotonic()
        if credential is None:
            self._require_config()
        def send():
            request = urllib.request.Request(
                (credential.base_url if credential else self.config.llm_base_url).rstrip("/") + "/chat/completions",
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": "Bearer " + (credential.api_key if credential else self.config.llm_api_key.get_secret_value()),
                    "Content-Type": "application/json",
                },
            )
            if self.transport is not None:
                return self.transport(request, self.config.llm_timeout_seconds)
            with urllib.request.urlopen(request, timeout=self.config.llm_timeout_seconds) as response:
                return json.loads(response.read())

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(send), self.config.llm_timeout_seconds
            )
        except TimeoutError as error:
            raise ProviderTimeoutError(cause=error, internal_context={"provider": "openai_compatible"}) from error
        except urllib.error.HTTPError as error:
            from ..tools.diagnostics import diagnose
            try:
                body = json.loads(error.read(8192))
                details = body.get('error', {})
                code = details.get('code') if isinstance(details, dict) else None
                text = details.get('message', '') if isinstance(details, dict) else str(details)
            except Exception:
                code, text = None, ''
            context = {"provider": "openai_compatible", "http_status": error.code, "provider_code": str(code) if code else None, "confirmed_no_usage": True}
            if error.code in {401, 403}: raise ProviderAuthError(cause=error, internal_context=context)
            if error.code == 402 or str(code).upper() in {"INSUFFICIENT_QUOTA", "QUOTA_EXCEEDED"}: raise ProviderQuotaError(cause=error, internal_context=context)
            if error.code == 429: raise ProviderRateLimitError(cause=error, internal_context=context)
            if error.code >= 500: raise ProviderUnavailableError(cause=error, internal_context=context)
            raise ProviderInvalidResponseError(cause=error, internal_context=context)
        except Exception as error:
            if isinstance(error, (ProviderAuthError, ProviderQuotaError, ProviderRateLimitError, ProviderUnavailableError, ProviderInvalidResponseError)):
                raise
            raise ProviderNetworkError(cause=error, internal_context={"provider": "openai_compatible", "exception": type(error).__name__}) from error
        try:
            message = response["choices"][0]["message"]
            usage = response.get("usage", {})
            return message, usage, {"kind": "provider", "status": "SUCCEEDED", "usage": dict(usage), "provider_request_id": response.get("id"), "latency_ms": round((time.monotonic() - started) * 1000)}
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderInvalidResponseError(cause=error, internal_context={"provider": "openai_compatible", "reason": "malformed_envelope"}) from error

    async def decide(self, task, observations, *, messages=None, tools=(), dependency_results=None, _credential=None):
        payload = {
            "model": (_credential.model if _credential and _credential.model else self.config.llm_model),
            "messages": messages or [{"role": "user", "content": task.goal}],
        }
        if tools:
            payload.update(tools=list(tools), tool_choice="auto")
        message, usage, _ = await self._request(payload, _credential)
        if message.get("tool_calls"):
            return {"tool_calls": message["tool_calls"], "assistant_message": message, "usage": usage}
        if not isinstance(message.get("content"), str) or not message["content"].strip():
            raise ProviderInvalidResponseError(internal_context={"provider": "openai_compatible", "reason": "empty_message"})
        return {"final": message["content"], "usage": usage}

    async def decide_with_telemetry(self, task, observations, *, messages=None, tools=(), dependency_results=None, _credential=None):
        payload = {"model": (_credential.model if _credential and _credential.model else self.config.llm_model), "messages": messages or [{"role": "user", "content": task.goal}]}
        if tools: payload.update(tools=list(tools), tool_choice="auto")
        message, usage, attempt = await self._request(payload, _credential)
        if message.get("tool_calls"):
            return {"tool_calls": message["tool_calls"], "assistant_message": message, "usage": usage}, [attempt]
        if not isinstance(message.get("content"), str) or not message["content"].strip():
            raise ProviderInvalidResponseError(internal_context={"provider": "openai_compatible", "reason": "empty_message"})
        return {"final": message["content"], "usage": usage}, [attempt]

    async def generate_structured(self, messages, response_model, _credential=None):
        value, _ = await self.generate_structured_with_telemetry(messages, response_model, _credential)
        return value

    async def generate_structured_with_telemetry(self, messages, response_model, _credential=None):
        if _credential is None: self._require_config()
        schema_message = {
            "role": "system",
            "content": (
                "Return only the JSON object described by this output schema, without a wrapper: "
                + json.dumps(response_model.model_json_schema(), ensure_ascii=False)
            ),
        }
        conversation = [schema_message, *messages]
        attempts: list[dict] = []
        for attempt in range(2):
            message, usage, attempt_snapshot = await self._request(
                {
                    "model": (_credential.model if _credential and _credential.model else self.config.llm_model),
                    "messages": conversation,
                    "response_format": {"type": "json_object"},
                }
            , _credential)
            try:
                parsed = parse_structured_content(message["content"], response_model)
                attempt_snapshot["kind"] = "structured"
                attempts.append(attempt_snapshot)
                return parsed, attempts
            except Exception as error:
                attempt_snapshot.update(kind="structured", status="FAILED", error_code=type(error).__name__)
                attempts.append(attempt_snapshot)
                if attempt == 1:
                    # Gateway must persist every real request, including the failed
                    # repair attempt; carry immutable snapshots with the exception.
                    setattr(error, "telemetry_attempts", tuple(attempts))
                    raise
                conversation = [
                    *conversation,
                    {"role": "assistant", "content": message["content"]},
                    {
                        "role": "user",
                        "content": (
                            "Your output failed JSON/schema validation. Return corrected valid JSON "
                            "satisfying the exact output schema. Preserve the user facts; do not add "
                            "a wrapper or prose."
                        ),
                    },
                ]
