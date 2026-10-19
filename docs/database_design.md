# Database design

The local MySQL 8.4 compose service maps to `127.0.0.1:3307`; Alembic owns the schema. Tables are `users`, `user_memories`, `runs`, `run_events`, and `checkpoints`.

`checkpoints` has unique `(user_id, run_id)` and a version field. Saves use optimistic CAS: `UPDATE ... WHERE user_id=? AND run_id=? AND version=?`; a zero row count raises `ConcurrentCheckpointUpdateError`. This provides database-local consistency without Redis or distributed coordination.
