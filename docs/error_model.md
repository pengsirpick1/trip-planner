# Phase 4A Error Model

All public expected failures use `{ "code", "message", "trace_id" }`. Internal causes and contexts are never serialized. A request middleware accepts `X-Trace-Id` or creates one, returns it in the response header, and exposes it in every structured error.

| Source | Code | HTTP | Retryable | Quota action | Alert candidate |
|---|---|---:|---|---|---|
| Provider timeout | `PROVIDER_TIMEOUT` | 503 | yes | outcome-dependent | yes |
| Provider rate limit | `PROVIDER_RATE_LIMITED` | 503 | yes | outcome-dependent | yes |
| Provider auth/quota | `PROVIDER_AUTH_FAILED` / `PROVIDER_QUOTA_EXHAUSTED` | 503 | no | release only when no-cost is explicit | yes |
| Provider network/5xx | `PROVIDER_NETWORK_ERROR` / `PROVIDER_UNAVAILABLE` | 503 | yes | pending if outcome unknown | yes |
| Invalid provider response | `PROVIDER_INVALID_RESPONSE` | 503 | no | settle known usage | yes |
| Tool timeout/network | `TOOL_TIMEOUT` / `TOOL_NETWORK_ERROR` | 503 | yes | none | yes |
| User quota | `QUOTA_INSUFFICIENT` | 429 | no | none | no |
| Reconciliation required | `QUOTA_RECONCILIATION_REQUIRED` | 503 | no | keep pending hold | yes |
| Telemetry persistence | `TELEMETRY_PERSISTENCE_FAILED` | 503 | no | pending reconciliation | yes |
| Concurrent checkpoint | `CONCURRENT_STATE_UPDATE` | 409 | no | none | no |
| Database unavailable | `DATABASE_UNAVAILABLE` | 503 | yes | none | yes |

The provider adapter is the HTTP normalization boundary: 401/403 map to provider auth, 402 and quota codes map to provider quota, 429 maps to rate limit, 5xx to unavailable, timeout to timeout, connection failures to network, and malformed successful envelopes to invalid response.

`OUTCOME_UNKNOWN` remains distinct: Provider success followed by telemetry persistence failure must keep the quota reservation pending and must not be retried automatically. Known usage settles; explicitly confirmed no-cost failures release.

`sanitize_exception` removes Bearer credentials, URL passwords, `sk-` keys, API-key/token/password query values. Prompts and raw provider bodies are excluded from the structured error context.

Phase 4A defines `retryable` only. It does not add retry, alert, Redis, rate-limit, BYOK, or credential-encryption behavior.
