"""Deterministic recovery actions; scheduling is deliberately outside Phase 3."""
from __future__ import annotations

class ReconciliationService:
    def __init__(self, quota_repository, telemetry_repository):
        self.quota_repository = quota_repository
        self.telemetry_repository = telemetry_repository

    async def reconcile(self, llm_call_id: str):
        call = await self.telemetry_repository.get_call(llm_call_id)
        if call is None:
            return await self.quota_repository.mark_pending(llm_call_id, "OUTCOME_UNKNOWN")
        usage = {"prompt_tokens": call.prompt_tokens, "completion_tokens": call.completion_tokens}
        if usage["prompt_tokens"] or usage["completion_tokens"]:
            return await self.quota_repository.settle(llm_call_id, usage)
        if call.status == "FAILED":
            return await self.quota_repository.release(llm_call_id, "EXPLICIT_NO_USAGE_FAILURE")
        # CREATED/OUTCOME_UNKNOWN has no reliable provider outcome: keep its hold.
        return await self.quota_repository.mark_pending(llm_call_id, "OUTCOME_UNKNOWN")
