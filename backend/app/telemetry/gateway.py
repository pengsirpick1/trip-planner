from __future__ import annotations
import time
import asyncio
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from ..retry_policy import RetryPolicy

@dataclass(frozen=True)
class LLMCallContext:
    stage: str
    user_id: str | None = None
    run_id: str | None = None
    graph_id: str | None = None
    graph_version: int | None = None
    task_id: str | None = None

class TelemetryLLMGateway:
    """Assigns logical call identity and persists immutable per-request snapshots."""
    def __init__(self, provider, repository=None, *, quota_service=None, provider_name="openai_compatible", model=None,
                 reserve_prompt_tokens: int = 4000, reserve_completion_tokens: int = 1500, retry_policy=None, rate_limiter=None, rate_limit=30):
        self.provider, self.repository = provider, repository
        self.quota_service = quota_service
        self.provider_name, self.model = provider_name, model
        self.reserve_prompt_tokens, self.reserve_completion_tokens = reserve_prompt_tokens, reserve_completion_tokens
        self.retry_policy = retry_policy or RetryPolicy()
        self.rate_limiter, self.rate_limit = rate_limiter, rate_limit
        self.credential_repository = None
        self._context = ContextVar("llm_call_context", default=None)
    @contextmanager
    def scope(self, context: LLMCallContext):
        token = self._context.set(context)
        try: yield
        finally: self._context.reset(token)
    def _infer(self, method, args):
        context = self._context.get()
        if context: return context
        if method == "generate_structured":
            name = getattr(args[1], "__name__", "UNKNOWN")
            stage = {"IntentUpdate":"INTENT", "ComplexitySignals":"ROUTER", "PlanDraft":"PLANNER", "TripPlan":"SYNTHESIS"}.get(name, "STRUCTURED")
        else: stage = "REACT" if args and args[0] is not None else "DIRECT"
        return LLMCallContext(stage=stage)
    async def _record(self, call_id, context, status, attempts, *, provider=None, model=None):
        if self.repository is not None:
            complete = getattr(self.repository, "complete_logical_call", None)
            if complete is not None:
                await complete(call_id, status, attempts)
            else:
                await self.repository.record(call_id, context, model or self.model, provider or self.provider_name, status, attempts)
    async def decide(self, *args, **kwargs): return await self._invoke("decide", args, kwargs)
    async def generate_structured(self, *args, **kwargs): return await self._invoke("generate_structured", args, kwargs)
    async def _invoke(self, method, args, kwargs):
        call_id, context, started = uuid.uuid4().hex, self._infer(method, args), time.monotonic()
        attempts = []
        quota_reserved = False
        effective_provider, effective_model = self.provider_name, self.model
        try:
            credential = await self.credential_repository.resolve(context.user_id) if self.credential_repository else None
            effective_provider = credential.provider if credential is not None else self.provider_name
            effective_model = (credential.model or self.model) if credential is not None else self.model
            if credential is not None:
                # Pass the decrypted value only in this in-memory provider call.
                # It is never added to telemetry, prompts, events, or exceptions.
                kwargs = dict(kwargs)
                kwargs["_credential"] = credential
            if self.rate_limiter is not None:
                subject = context.user_id or "anonymous"
                if not await self.rate_limiter.allow(f"llm:{subject}:{self.provider_name}", self.rate_limit, 60):
                    from ..errors import ProviderRateLimitError
                    raise ProviderRateLimitError(internal_context={"local_rate_limit": True, "confirmed_no_usage": True})
            # The logical identity is durable before quota can point at it.  This
            # intentionally fails closed: DB trouble must not spend the shared key.
            if self.repository is not None and hasattr(self.repository, "create_logical_call"):
                await self.repository.create_logical_call(call_id, context, effective_model, effective_provider)
            if self.quota_service is not None and context.user_id and credential is None:
                estimate = self.quota_service.pricing.get(self.provider_name, self.model)
                from ._quota_estimate import reserve_estimate
                await self.quota_service.reserve(context.user_id, call_id, self.provider_name, self.model,
                    reserve_estimate(estimate, self.reserve_prompt_tokens, self.reserve_completion_tokens))
                quota_reserved = True
            telemetry_method = getattr(self.provider, method + "_with_telemetry", None)
            retry_index = 0
            while True:
                try:
                    if telemetry_method:
                        value, provider_attempts = await telemetry_method(*args, **kwargs)
                    else:
                        value = await getattr(self.provider, method)(*args, **kwargs)
                        provider_attempts = [{"kind":"provider", "status":"SUCCEEDED", "usage": dict(value.get("usage") or {}) if isinstance(value, dict) else {}, "latency_ms": round((time.monotonic()-started)*1000)}]
                    attempts.extend(provider_attempts)
                    break
                except Exception as provider_error:
                    attempts.extend(list(getattr(provider_error, "telemetry_attempts", ())) or [{"kind":"provider", "status":"FAILED", "usage":{}, "latency_ms":round((time.monotonic()-started)*1000), "error_code":getattr(provider_error, "code", "INTERNAL_ERROR")}])
                    decision = self.retry_policy.provider(provider_error, retry_index)
                    if not decision.retry:
                        setattr(provider_error, "telemetry_attempts", tuple(attempts))
                        raise
                    retry_index += 1
                    await asyncio.sleep(decision.delay_seconds)
            try:
                await self._record(call_id, context, "SUCCEEDED", attempts, provider=effective_provider, model=effective_model)
                if quota_reserved:
                    await self.quota_service.settle(call_id, self._usage(attempts))
            except Exception as persistence_error:
                if quota_reserved:
                    try: await self.quota_service.mark_pending_reconciliation(call_id, "TELEMETRY_PERSISTENCE_FAILURE")
                    except Exception:
                        from ..quota import QuotaReconciliationRequired
                        raise QuotaReconciliationRequired("模型已响应但账务状态未知，禁止自动重试") from persistence_error
                raise
            return value
        except Exception as error:
            attempts = list(getattr(error, "telemetry_attempts", attempts))
            if not attempts: attempts = [{"kind":"provider", "status":"FAILED", "usage":{}, "latency_ms":round((time.monotonic()-started)*1000), "error_code":getattr(error, "code", "INTERNAL_ERROR")}]
            try:
                await self._record(call_id, context, "FAILED", attempts, provider=effective_provider, model=effective_model)
                if quota_reserved:
                    usage = self._usage(attempts)
                    if usage["prompt_tokens"] or usage["completion_tokens"]:
                        await self.quota_service.settle(call_id, usage)
                    elif getattr(error, "no_cost", False):
                        await self.quota_service.release(call_id, "EXPLICIT_NO_COST_FAILURE")
                    else:
                        # A generic transport error can occur after a provider has
                        # accepted the request. Preserve the hold for reconciliation.
                        await self.quota_service.mark_pending_reconciliation(call_id, "OUTCOME_UNKNOWN")
            except Exception as persistence_error:
                if quota_reserved:
                    try:
                        await self.quota_service.mark_pending_reconciliation(call_id, "TELEMETRY_PERSISTENCE_FAILURE")
                    except Exception:
                        from ..quota import QuotaReconciliationRequired
                        raise QuotaReconciliationRequired("模型结果无法可靠记账，禁止自动重试") from persistence_error
            raise

    @staticmethod
    def _usage(attempts):
        return {"prompt_tokens": sum(int((x.get("usage") or {}).get("prompt_tokens", 0) or 0) for x in attempts),
                "completion_tokens": sum(int((x.get("usage") or {}).get("completion_tokens", 0) or 0) for x in attempts)}
