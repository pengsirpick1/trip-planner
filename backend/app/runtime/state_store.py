"""执行状态存储（迁自 travel_agent/agent/runtime/state_store.py + Phase B 异步修复）。

Phase B 修复（审计 T20）：SQLite 同步调用一律经 asyncio.to_thread，避免阻塞事件循环。
同图 Task-boundary checkpoint：session_id 单行保存最新快照（版本历史属 Phase F 范围）。
"""

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ..domain import GraphExecutionState


class StateStore(Protocol):
    async def save(self, state: GraphExecutionState, *, user_id: str | None = None, run_id: str | None = None) -> None: ...
    async def load(self, session_id: str, *, user_id: str | None = None) -> GraphExecutionState | None: ...


class InMemoryStateStore:
    def __init__(self):
        self._states: dict[str, GraphExecutionState] = {}

    async def save(self, state, *, user_id=None, run_id=None):
        self._states[state.session_id] = state.model_copy(deep=True)

    async def load(self, session_id, *, user_id=None):
        state = self._states.get(session_id)
        return state.model_copy(deep=True) if state else None


class CheckpointValidationError(ValueError):
    pass


class CheckpointNotFoundError(LookupError):
    pass


class SQLiteStateStore:
    """Latest-snapshot durable store；payload 是显式 Pydantic JSON，绝不用 pickle。"""

    schema_version = 1

    def __init__(self, database_path: str | Path):
        self.path = str(database_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS checkpoints ("
                "session_id TEXT PRIMARY KEY, graph_id TEXT NOT NULL, "
                "graph_version INTEGER NOT NULL, schema_version INTEGER NOT NULL, "
                "payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )

    def _save_sync(self, state: GraphExecutionState) -> None:
        payload = state.model_dump_json()
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "graph_id=excluded.graph_id, graph_version=excluded.graph_version, "
                "schema_version=excluded.schema_version, payload=excluded.payload, "
                "updated_at=excluded.updated_at",
                (
                    state.session_id,
                    state.graph.graph_id,
                    state.graph.version,
                    self.schema_version,
                    payload,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def _load_sync(self, session_id: str) -> GraphExecutionState | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT schema_version, payload FROM checkpoints WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        if row[0] != self.schema_version:
            raise CheckpointValidationError(f"unsupported checkpoint schema version: {row[0]}")
        try:
            return GraphExecutionState.model_validate_json(row[1])
        except Exception as error:
            raise CheckpointValidationError(f"corrupted checkpoint: {error}") from error

    async def save(self, state: GraphExecutionState, *, user_id: str | None = None, run_id: str | None = None) -> None:
        await asyncio.to_thread(self._save_sync, state)

    async def load(self, session_id: str, *, user_id: str | None = None) -> GraphExecutionState | None:
        return await asyncio.to_thread(self._load_sync, session_id)
