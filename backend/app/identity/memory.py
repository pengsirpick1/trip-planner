"""长期偏好记忆：保存稳定、明确的旅行偏好，并以受限摘要回灌 Agent。"""

from __future__ import annotations

import json
from typing import Any

from ..persistence.user_repository import MemoryRecord, SQLiteUserRepository


class MemoryService:
    """把显式意图压缩为有界的用户偏好，而非保存全部对话文本。"""

    max_context_items = 10
    max_summary_chars = 160

    def __init__(self, repository: SQLiteUserRepository) -> None:
        self.repository = repository

    @staticmethod
    def _clean_text(value: Any) -> str:
        return " ".join(str(value).strip().split())[:120]

    @classmethod
    def _facts(cls, fields: dict[str, Any]) -> list[tuple[str, str, Any, str, float]]:
        """仅提取用户本轮明确表达的、适合跨行程复用的偏好。"""
        facts = []
        for field, category, importance in (
            ("preferences", "preference", 0.8), ("avoid", "avoidance", 0.9),
            ("accommodation", "accommodation", 0.7), ("transportation", "transport", 0.65),
        ):
            value = fields.get(field)
            if value in (None, "", []):
                continue
            if isinstance(value, list):
                clean = list(dict.fromkeys(filter(None, (cls._clean_text(item) for item in value))))[:8]
                if clean:
                    facts.append((field, category, clean, f"{category}: {', '.join(clean)}", importance))
            elif isinstance(value, (str, int, float, bool)):
                clean = cls._clean_text(value)
                if clean:
                    facts.append((field, category, clean, f"{category}: {clean}", importance))
        constraints = fields.get("constraints")
        if isinstance(constraints, dict):
            stable = {str(k): v for k, v in constraints.items() if str(k) in {"budget", "pace", "travel_style"}}
            if stable:
                compact = json.dumps(stable, ensure_ascii=False, separators=(",", ":"))[:160]
                facts.append(("travel_constraints", "constraint", stable, f"constraint: {compact}", 0.7))
        return facts

    async def remember_explicit_fields(self, user_id: str | None, fields: dict[str, Any], source_run_id: str) -> None:
        if not user_id:
            return
        existing = {item.memory_key: item for item in await self.repository.list_memories(user_id, limit=30)}
        for key, category, value, summary, importance in self._facts(fields):
            # 兴趣/避好通常是累积信息；住宿和交通则以用户最近一次明确表达为准。
            if key in {"preferences", "avoid"} and isinstance(value, list):
                previous = existing.get(key)
                old_values = previous.value if previous and isinstance(previous.value, list) else []
                value = list(dict.fromkeys([*old_values, *value]))[-8:]
                summary = f"{category}: {', '.join(value)}"
            await self.repository.upsert_memory(user_id, key, category, value, summary[:self.max_summary_chars], 1.0, importance, source_run_id)

    async def context_for(self, user_id: str | None, message: str) -> list[dict[str, Any]]:
        if not user_id:
            return []
        memories = await self.repository.list_memories(user_id, limit=30)
        terms = {item.lower() for item in self._clean_text(message).split() if len(item) > 1}
        def rank(record: MemoryRecord):
            text = f"{record.category} {record.summary}".lower()
            relevance = sum(1 for term in terms if term in text)
            return (relevance, record.importance, record.updated_at)
        selected = sorted(memories, key=rank, reverse=True)[:self.max_context_items]
        return [
            {"memory_id": item.id, "category": item.category, "key": item.memory_key,
             "summary": item.summary[:self.max_summary_chars], "value": item.value}
            for item in selected
        ]
