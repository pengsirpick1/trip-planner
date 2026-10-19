# Shared-Key Quota Accounting

This phase meters the platform's shared provider key with per-user virtual integer points. It does not inspect a provider balance and does not implement payment, Redis, BYOK, or distributed locks.

```text
logical llm_call (CREATED)
  -> reserve points + RESERVE ledger entry
  -> Provider HTTP request (no database transaction is open)
  -> immutable attempt telemetry
  -> SETTLE actual usage | RELEASE confirmed no-cost failure | PENDING_RECONCILIATION
```

`llm_call_id` identifies one logical model action. Provider retries and structured-output repairs have their own attempt records. Ledger idempotency keys are `quota:<call_id>:reserve`, `:settle`, and `:release`; the ledger is append-only.

Accounts retain `total_points`, `reserved_points`, and `consumed_points`; available points are calculated as `total - reserved - consumed`. Amounts are `BIGINT` smallest units. Pricing uses `Decimal`, then rounds up. Each reservation stores a pricing snapshot, so a later price-registry change cannot rewrite the cost basis of an in-flight call.

Reservation concurrency uses one conditional update:

```sql
UPDATE user_quota_accounts
SET reserved_points = reserved_points + :estimate, version = version + 1
WHERE user_id = :user_id
  AND total_points - reserved_points - consumed_points >= :estimate;
```

This is different from checkpoint CAS: it atomically gates available balance. Settlement locks reservation first and account second everywhere, then moves a hold to consumption and appends the matching ledger entry in the same short transaction.

Provider and database cannot share an atomic transaction. Before the provider call, database failure fails closed. If the provider returns usage, the reservation settles even if structured validation later fails. A known no-cost failure releases. A timeout, response ambiguity, or telemetry persistence failure is `PENDING_RECONCILIATION`; its hold is deliberately retained and never blindly retried.

`ReconciliationService` is deterministic rather than a scheduler: known telemetry usage settles; an explicit failed call with no usage releases; `CREATED` or unknown provider outcomes remain pending. It is safe to invoke repeatedly because reservation status and ledger idempotency make terminal operations no-ops.

Crash handling: before Provider means no spend; after Provider before telemetry means pending; after telemetry before settlement is reconciled from immutable usage; after settlement is already terminal. This design is for a shared platform key. Future BYOK must use an independent encrypted-key and billing policy path.
