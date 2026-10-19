"""SQLite 用户、访问身份和长期记忆存储。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(UTC).isoformat()


class UserRecord(BaseModel):
    id: str
    username: str
    password_hash: str = Field(exclude=True)
    created_at: datetime
    last_login_at: datetime | None = None


class MemoryRecord(BaseModel):
    id: str
    user_id: str
    memory_key: str
    category: str
    value: Any
    summary: str
    confidence: float = 1.0
    importance: float = 0.5
    source_run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class SQLiteUserRepository:
    """独立于 RunRepository 的用户域表；两者共享同一 SQLite 数据库。"""

    def __init__(self, database_path: str | Path) -> None:
        self.path = str(database_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );
                CREATE TABLE IF NOT EXISTS user_memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance REAL NOT NULL,
                    source_run_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, memory_key),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_user_memories_lookup
                    ON user_memories(user_id, updated_at DESC);
                """
            )

    @staticmethod
    def _user(row) -> UserRecord:
        return UserRecord(
            id=row[0], username=row[1], password_hash=row[2],
            created_at=datetime.fromisoformat(row[3]),
            last_login_at=datetime.fromisoformat(row[4]) if row[4] else None,
        )

    @staticmethod
    def _memory(row) -> MemoryRecord:
        return MemoryRecord(
            id=row[0], user_id=row[1], memory_key=row[2], category=row[3],
            value=json.loads(row[4]), summary=row[5], confidence=float(row[6]),
            importance=float(row[7]), source_run_id=row[8],
            created_at=datetime.fromisoformat(row[9]), updated_at=datetime.fromisoformat(row[10]),
        )

    def _create_user_sync(self, username: str, password_hash: str) -> UserRecord:
        now, user_id = _now(), uuid.uuid4().hex
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute("INSERT INTO users VALUES (?,?,?,?,?)", (user_id, username, password_hash, now, None))
        except sqlite3.IntegrityError as error:
            raise ValueError("用户名已存在") from error
        return UserRecord(id=user_id, username=username, password_hash=password_hash, created_at=datetime.fromisoformat(now))

    async def create_user(self, username: str, password_hash: str) -> UserRecord:
        return await asyncio.to_thread(self._create_user_sync, username, password_hash)

    def _get_by_username_sync(self, username: str) -> UserRecord | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT id,username,password_hash,created_at,last_login_at FROM users WHERE username=?", (username,)).fetchone()
        return self._user(row) if row else None

    async def get_by_username(self, username: str) -> UserRecord | None:
        return await asyncio.to_thread(self._get_by_username_sync, username)

    def _get_by_id_sync(self, user_id: str) -> UserRecord | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT id,username,password_hash,created_at,last_login_at FROM users WHERE id=?", (user_id,)).fetchone()
        return self._user(row) if row else None

    async def get_by_id(self, user_id: str) -> UserRecord | None:
        return await asyncio.to_thread(self._get_by_id_sync, user_id)

    async def touch_login(self, user_id: str) -> None:
        def update():
            with sqlite3.connect(self.path) as conn:
                conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (_now(), user_id))
        await asyncio.to_thread(update)

    def _upsert_memory_sync(self, user_id: str, memory_key: str, category: str, value: Any,
                            summary: str, confidence: float, importance: float, source_run_id: str | None) -> MemoryRecord:
        now = _now()
        with sqlite3.connect(self.path) as conn:
            existing = conn.execute("SELECT id,created_at FROM user_memories WHERE user_id=? AND memory_key=?", (user_id, memory_key)).fetchone()
            memory_id, created_at = (existing[0], existing[1]) if existing else (uuid.uuid4().hex, now)
            conn.execute(
                "INSERT INTO user_memories (id,user_id,memory_key,category,value_json,summary,confidence,importance,source_run_id,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,memory_key) DO UPDATE SET "
                "category=excluded.category,value_json=excluded.value_json,summary=excluded.summary,confidence=excluded.confidence," 
                "importance=excluded.importance,source_run_id=excluded.source_run_id,updated_at=excluded.updated_at",
                (memory_id, user_id, memory_key, category, json.dumps(value, ensure_ascii=False), summary,
                 confidence, importance, source_run_id, created_at, now),
            )
            row = conn.execute("SELECT id,user_id,memory_key,category,value_json,summary,confidence,importance,source_run_id,created_at,updated_at FROM user_memories WHERE id=?", (memory_id,)).fetchone()
        return self._memory(row)

    async def upsert_memory(self, *args, **kwargs) -> MemoryRecord:
        return await asyncio.to_thread(self._upsert_memory_sync, *args, **kwargs)

    def _list_memories_sync(self, user_id: str, limit: int) -> list[MemoryRecord]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT id,user_id,memory_key,category,value_json,summary,confidence,importance,source_run_id,created_at,updated_at "
                "FROM user_memories WHERE user_id=? ORDER BY importance DESC, updated_at DESC LIMIT ?", (user_id, limit)
            ).fetchall()
        return [self._memory(row) for row in rows]

    async def list_memories(self, user_id: str, limit: int = 30) -> list[MemoryRecord]:
        return await asyncio.to_thread(self._list_memories_sync, user_id, limit)

    async def delete_memory(self, user_id: str, memory_id: str) -> bool:
        def delete() -> bool:
            with sqlite3.connect(self.path) as conn:
                return conn.execute("DELETE FROM user_memories WHERE id=? AND user_id=?", (memory_id, user_id)).rowcount > 0
        return await asyncio.to_thread(delete)
