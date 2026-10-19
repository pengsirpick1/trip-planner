"""溯源模型与证据索引（Phase D）。

溯源链目标：
  TripPlan 某字段 → evidence_id(task_id+graph_version) → ToolResult/来源
  → provider + 查询 + 观察时间。
本模块从 GraphExecutionState 的任务结果/工具结果建立只读证据索引；
Synthesizer 只允许引用该索引中的证据，禁止凭空引用。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..domain import GraphExecutionState


class EvidenceEntry(BaseModel):
    evidence_id: str
    task_id: str
    graph_version: int
    provider: Optional[str] = None
    query: Optional[str] = None
    observed_at: Optional[str] = None
    source: Optional[str] = None
    snippet: Optional[str] = None
    tool_name: Optional[str] = None
    # Phase G 修复：保留工具返回的结构化 data，例如 {"location":"lng,lat"}，
    # 让坐标池不依赖 LLM 把坐标抄写到 final answer 文本中
    data: Optional[Any] = None


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _collect_payload(items: Any, depth: int = 0) -> str:
    """把工具结果压成短摘要；完整 data 留在 checkpoint 供溯源和坐标校验。"""
    import json

    if depth > 2:
        return ""
    try:
        text = json.dumps(items, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(items)
    return text[:600]


def build_evidence_index(
    state: GraphExecutionState,
    graph_version: int | None = None,
) -> dict[str, EvidenceEntry]:
    """从 task_results.metadata.tool_results 构建证据索引（key=evidence_id）。"""
    version = graph_version if graph_version is not None else state.graph.version
    index: dict[str, EvidenceEntry] = {}
    for task_id, result in state.task_results.items():
        metadata = result.metadata or {}
        # vN 保留下来的成功结果带 graph_version 标签，溯源用各自真实版本
        result_version = metadata.get("graph_version", version)
        tool_results = metadata.get("tool_results", []) or []
        for idx, tool_result in enumerate(tool_results):
            if not isinstance(tool_result, dict):
                continue
            if tool_result.get('success') is not True:
                continue
            evidence_id = f"{task_id}@{result_version}#tool{idx}"
            provider = tool_result.get("metadata", {}).get("provider") if isinstance(
                tool_result.get("metadata"), dict
            ) else None
            observed_at = (
                tool_result.get("metadata", {}).get("retrieved_at")
                if isinstance(tool_result.get("metadata"), dict)
                else None
            )
            sources = tool_result.get("source_references") or []
            snippet = _collect_payload(tool_result.get("data"))
            raw_data = tool_result.get("data")
            index[evidence_id] = EvidenceEntry(
                evidence_id=evidence_id,
                task_id=task_id,
                graph_version=result_version,
                provider=provider or "unknown",
                query=_first_query(metadata.get("llm_steps")),
                observed_at=observed_at or None,
                source=sources[0] if sources else None,
                snippet=snippet or None,
                tool_name=tool_result.get("tool_name"),
                data=raw_data,
            )
    return index


def _first_query(llm_steps: Any) -> Optional[str]:
    if not isinstance(llm_steps, list):
        return None
    for step in llm_steps:
        if isinstance(step, dict) and step.get("kind") == "tool_request":
            return step.get("tool")
    return None


def collect_coordinate_pool(index: dict[str, EvidenceEntry]) -> dict[str, dict[str, str]]:
    r"""从证据 snippet 里粗提取 'lng,lat' 形坐标串池，供 Synthesizer 引用校验。

    同时扫描工具返回的结构化 data（如 maps_search_detail 的 {"location":"lng,lat"}），
    避免 LLM 必须把坐标抄写到 final answer 才能被识别（Phase G 修复）。

    返回值：坐标串 → {evidence_id, tool_name}。
    """
    import re

    pattern = re.compile(r"(?<![0-9.])(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)(?![0-9.])")
    pool: dict[str, dict[str, str]] = {}

    def _scan(text: str, evidence_id: str, tool_name: str | None) -> None:
        if not text:
            return
        for match in pattern.finditer(text):
            lng, lat = float(match.group(1)), float(match.group(2))
            if -180 <= lng <= 180 and -90 <= lat <= 90:
                canonical = f"{lng:.6f},{lat:.6f}"
                pool.setdefault(canonical, {"evidence_id": evidence_id, "tool_name": tool_name or ""})

    for evidence_id, entry in index.items():
        _scan(entry.snippet or "", evidence_id, entry.tool_name)
        if entry.data is None:
            continue
        try:
            blob = json.dumps(entry.data, ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        _scan(blob, evidence_id, entry.tool_name)
    return pool


def build_source_summary(index: dict[str, EvidenceEntry]) -> dict[str, Any]:
    """给 API 展示的溯源摘要（紧凑、可读）。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in index.values():
        grouped.setdefault(entry.task_id, []).append(
            {
                "evidence_id": entry.evidence_id,
                "provider": entry.provider,
                "query": entry.query,
                "observed_at": entry.observed_at,
                "source": entry.source,
            }
        )
    return grouped
