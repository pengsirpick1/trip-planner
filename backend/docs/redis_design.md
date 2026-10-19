# Redis acceleration boundary

Redis is optional in this service. MySQL remains the authoritative store for runs, events, checkpoints, quota reservations, ledger entries and LLM telemetry. If Redis is unavailable at startup, the application uses a process-local in-memory adapter; run execution remains available and no durable state is lost.

| Capability | Key shape | TTL / rule | Failure behavior |
| --- | --- | --- | --- |
| Run read snapshot | `agent:run:{user_id}:{run_id}` | 120 seconds; invalidate before a message mutation | Read falls back to the tenant-scoped repository. Checkpoints are never cached. |
| Quota read snapshot | `quota:account:{user_id}` | 15 seconds | Ledger, reservations and quota mutations stay in MySQL. A short-lived read can be stale. |
| API limiter | `ratelimit:{sha256(identity)}:{bucket}` | fixed 60-second window | Process-local fallback; multi-instance global limiting is unavailable while Redis is down. |
| LLM/tool limiter | `llm:{user_id}:{provider}:{bucket}`, `tool:{user_id}:{tool}:{bucket}` | fixed 60-second window | Same process-local fallback; provider/quota correctness is unaffected. |
| Request idempotency | `idempotency:{sha256(subject,key,path)}` | 300 seconds | A successful response is replayed with its original status; in-flight duplicates receive 409. Redis loss removes only this short replay window. |
| Error counter | `errors:{code}` | 300 seconds | Monitoring signal is lost, never business state. |

The rate limiter is intentionally a fixed-window counter, not a sliding window. It is atomic in Redis but permits boundary bursts. It is suitable for inexpensive admission control; provider quotas and database accounting remain the final correctness guards.

No raw Bearer credential is put in a Redis key. API and idempotency identities are SHA-256 digests. The application does not implement a provider-balance cache because no provider balance API is part of this product.
