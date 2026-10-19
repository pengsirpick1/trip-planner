"""Pure policy: retry decisions are separate from execution and accounting."""
from __future__ import annotations
from dataclasses import dataclass
from .errors import AppError

@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay_seconds: float = 0.0
    reason: str = "not_retryable"

class RetryPolicy:
    """Only confirmed no-usage provider failures are retried in Phase 4B."""
    MAX_PROVIDER_RETRIES = 2
    def provider(self, error: Exception, attempt_index: int) -> RetryDecision:
        if not isinstance(error, AppError) or attempt_index >= self.MAX_PROVIDER_RETRIES:
            return RetryDecision(False, reason="budget_or_non_app_error")
        # Timeout and transport failures are deliberately not retried: the
        # provider may have accepted the request and produced billable usage.
        if error.code not in {"PROVIDER_RATE_LIMITED", "PROVIDER_UNAVAILABLE"}:
            return RetryDecision(False, reason="outcome_unknown_or_terminal")
        if not error.internal_context.get("confirmed_no_usage", False):
            return RetryDecision(False, reason="outcome_not_confirmed")
        return RetryDecision(True, delay_seconds=min(0.25 * (2 ** attempt_index), 1.0), reason="confirmed_no_usage")
