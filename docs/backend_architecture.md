# Backend architecture

When `DATABASE_URL` is set, the service uses one SQLAlchemy 2 Async/asyncmy session factory for run, user-memory, and checkpoint repositories. Without it, SQLite remains the injected legacy/test adapter.

The ownership path is `Bearer Token -> AuthContext -> TravelApplicationService -> tenant-scoped repository -> GraphRuntime -> MySQLStateStore`. GraphRuntime carries immutable `ExecutionOwnership(user_id, run_id, session_id)` for every checkpoint save.
