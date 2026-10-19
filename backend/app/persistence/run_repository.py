"""Run 持久化：SQLite 实现（runs + run_events 两表）。

设计说明：
- 同步 sqlite3 一律用 asyncio.to_thread 包裹，避免阻塞事件循环（审计 T20）。
- 事件与记录在同一事务写入；事件 seq 单调递增（Phase G 供 SSE Last-Event-ID 续读）。
- Phase B/F 会把 TaskGraph 执行状态（checkpoint/版本历史）合入本库，本文件只负责 run 层。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional, Protocol

from ..application.travel_service import RunEvent, RunRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT,
    session_id TEXT,
    status TEXT NOT NULL,
    kind TEXT,
    message TEXT NOT NULL DEFAULT '',
    error_code TEXT,
    data TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""


class RunRepository(Protocol):
    async def create(self, record: RunRecord) -> None: ...
    async def update(self, record: RunRecord) -> None: ...
    async def get(self, run_id: str) -> Optional[RunRecord]: ...
    async def list_events(self, run_id: str) -> list[RunEvent]: ...
    def close(self) -> None: ...


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _now() -> datetime:
    return datetime.now(UTC)


def _row_to_record(row) -> RunRecord:
    return RunRecord(
        run_id=row[0],
        user_id=row[1],
        session_id=row[2],
        status=row[3],
        kind=row[4],
        message=row[5] or "",
        error_code=row[6],
        data=json.loads(row[7]) if row[7] else None,
        created_at=datetime.fromisoformat(row[8]),
        updated_at=datetime.fromisoformat(row[9]),
        conversation=json.loads(row[10]) if len(row) > 10 and row[10] else None,
    )


class SQLiteRunRepository:
    """runs / run_events 的最小 SQLite 实现。"""

    def __init__(self, database_path: str | Path) -> None:
        self.path = str(database_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(_SCHEMA)
            columns = {r[1] for r in conn.execute('PRAGMA table_info(runs)')}
            if 'conversation' not in columns:
                conn.execute('ALTER TABLE runs ADD COLUMN conversation TEXT')
            if 'user_id' not in columns:
                conn.execute('ALTER TABLE runs ADD COLUMN user_id TEXT')

    # ---- 同步内部实现（在线程中执行） ----

    def _create_sync(self, record: RunRecord) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO runs (run_id,user_id,session_id,status,kind,message,error_code,data,created_at,updated_at,conversation) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.user_id,
                    record.session_id,
                    record.status.value,
                    record.kind.value if record.kind else None,
                    record.message,
                    record.error_code,
                    json.dumps(record.data) if record.data is not None else None,
                    _iso(record.created_at),
                    _iso(record.updated_at),
                    json.dumps(record.conversation) if record.conversation else None,
                ),
            )
            self._insert_events_sync(conn, record)

    def _update_sync(self, record: RunRecord) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "UPDATE runs SET user_id=?, session_id=?, status=?, kind=?, message=?, error_code=?, "
                "data=?, created_at=?, updated_at=?, conversation=? WHERE run_id=?",
                (
                    record.user_id,
                    record.session_id,
                    record.status.value,
                    record.kind.value if record.kind else None,
                    record.message,
                    record.error_code,
                    json.dumps(record.data) if record.data is not None else None,
                    _iso(record.created_at),
                    _iso(record.updated_at),
                    json.dumps(record.conversation) if record.conversation else None,
                    record.run_id,
                ),
            )
            conn.execute("DELETE FROM run_events WHERE run_id=?", (record.run_id,))
            self._insert_events_sync(conn, record)

    @staticmethod
    def _insert_events_sync(conn: sqlite3.Connection, record: RunRecord) -> None:
        for event in record.events:
            conn.execute(
                "INSERT OR REPLACE INTO run_events VALUES (?,?,?,?,?)",
                (
                    record.run_id,
                    event.seq,
                    event.event,
                    json.dumps(event.payload, ensure_ascii=False),
                    _iso(event.timestamp),
                ),
            )

    def _get_sync(self, run_id: str) -> Optional[RunRecord]:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT run_id, user_id, session_id, status, kind, message, error_code, data, "
                "created_at, updated_at, conversation FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            record = _row_to_record(row)
            rows = conn.execute(
                "SELECT seq, event, payload, timestamp FROM run_events "
                "WHERE run_id=? ORDER BY seq", (run_id,)
            ).fetchall()
            record.events = [
                RunEvent(
                    seq=seq,
                    event=event,
                    payload=json.loads(payload),
                    timestamp=datetime.fromisoformat(ts),
                )
                for seq, event, payload, ts in rows
            ]
            return record

    def _list_events_sync(self, run_id: str) -> list[RunEvent]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT seq, event, payload, timestamp FROM run_events "
                "WHERE run_id=? ORDER BY seq", (run_id,)
            ).fetchall()
            return [
                RunEvent(
                    seq=seq,
                    event=event,
                    payload=json.loads(payload),
                    timestamp=datetime.fromisoformat(ts),
                )
                for seq, event, payload, ts in rows
            ]

    # ---- async 接口（在 to_thread 中执行，避免阻塞事件循环） ----

    async def create(self, record: RunRecord) -> None:
        await asyncio.to_thread(self._create_sync, record)

    async def update(self, record: RunRecord) -> None:
        await asyncio.to_thread(self._update_sync, record)

    async def get(self, run_id: str) -> Optional[RunRecord]:
        return await asyncio.to_thread(self._get_sync, run_id)

    async def list_events(self, run_id: str) -> list[RunEvent]:
        return await asyncio.to_thread(self._list_events_sync, run_id)

    def close(self) -> None:
        return None
