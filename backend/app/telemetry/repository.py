from __future__ import annotations
import uuid
from datetime import UTC, datetime
from sqlalchemy.ext.asyncio import async_sessionmaker
from ..persistence.sqlalchemy_models import LLMCallAttemptRow, LLMCallRow

class MySQLLLMTelemetryRepository:
    """Append-only telemetry writer. A session is opened per logical call, never shared."""
    def __init__(self, sessions: async_sessionmaker): self.sessions = sessions
    async def create_logical_call(self, call_id, context, model, provider):
        """Persist identity before quota reservation and before the Provider request."""
        now = datetime.now(UTC)
        async with self.sessions() as session:
            async with session.begin():
                session.add(LLMCallRow(id=call_id, user_id=context.user_id, run_id=context.run_id,
                    graph_id=context.graph_id, graph_version=context.graph_version, task_id=context.task_id,
                    stage=context.stage, model=model, provider=provider, status="CREATED", attempt_count=0,
                    prompt_tokens=0, completion_tokens=0, total_tokens=0, created_at=now, completed_at=None))

    async def complete_logical_call(self, call_id, status, attempts):
        now = datetime.now(UTC)
        def total(name): return sum(int((a.get("usage") or {}).get(name, 0) or 0) for a in attempts)
        async with self.sessions() as session:
            async with session.begin():
                row = await session.scalar(__import__('sqlalchemy').select(LLMCallRow).where(LLMCallRow.id == call_id))
                if row is None: raise KeyError(call_id)
                row.status=status; row.attempt_count=len(attempts); row.prompt_tokens=total("prompt_tokens"); row.completion_tokens=total("completion_tokens"); row.total_tokens=total("total_tokens"); row.completed_at=now
                session.add_all([LLMCallAttemptRow(id=uuid.uuid4().hex, call_id=call_id, attempt_no=index, kind=item.get("kind", "provider"), status=item.get("status", "FAILED"), provider_request_id=item.get("provider_request_id"), latency_ms=item.get("latency_ms"), prompt_tokens=int((item.get("usage") or {}).get("prompt_tokens", 0) or 0), completion_tokens=int((item.get("usage") or {}).get("completion_tokens", 0) or 0), total_tokens=int((item.get("usage") or {}).get("total_tokens", 0) or 0), error_code=item.get("error_code"), created_at=now) for index, item in enumerate(attempts, 1)])

    async def record(self, call_id, context, model, provider, status, attempts):
        """Compatibility helper used by older callers and isolated tests."""
        await self.create_logical_call(call_id, context, model, provider)
        await self.complete_logical_call(call_id, status, attempts)

    async def get_call(self, call_id):
        async with self.sessions() as session:
            return await session.scalar(__import__('sqlalchemy').select(LLMCallRow).where(LLMCallRow.id == call_id))
