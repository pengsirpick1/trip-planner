"""Deterministic handoff for the itinerary synthesis node.

The graph keeps an explicit ``itinerary_synthesis`` node so the UI can show
that research converged before the final plan is rendered.  This executor
does not ask an LLM to rewrite the research, however: the final
``TripPlanSynthesizer`` already consumes the evidence index and is the one
LLM call that turns evidence into a displayable itinerary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..domain import TaskResult


class EvidenceHandoffExecutor:
    """Mark dependency evidence as ready for final structured synthesis.

    Only a small, presentation-safe summary is put on the task result.  The
    complete tool records remain on their original research tasks, where the
    provenance builder reads them for coordinate validation and source links.
    """

    executor_id = "evidence-handoff"

    @staticmethod
    def _summary(dependency_results: dict[str, TaskResult]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for task_id, result in dependency_results.items():
            metadata = result.metadata or {}
            items.append(
                {
                    "task_id": task_id,
                    "success": result.success,
                    "output_preview": str(result.output or "")[:240],
                    "sources": list(dict.fromkeys(metadata.get("sources") or []))[:4],
                    "evidence_count": len(metadata.get("tool_results") or []),
                }
            )
        return items

    async def execute(self, task, context) -> TaskResult:
        started = datetime.now(UTC)
        dependencies = dict(getattr(context, "dependency_results", {}) or {})
        handoff = self._summary(dependencies)
        source_count = sum(len(item["sources"]) for item in handoff)
        evidence_count = sum(item["evidence_count"] for item in handoff)
        return TaskResult(
            task_id=task.id,
            success=True,
            output=(
                f"已汇集 {len(handoff)} 个研究任务的 {evidence_count} 条工具证据，"
                "交由最终行程生成器进行结构化规划。"
            ),
            executor_id=self.executor_id,
            started_at=started,
            finished_at=datetime.now(UTC),
            metadata={
                "tool_calls": 0,
                "sources": [],
                "tool_results": [],
                "llm_steps": [],
                "evidence_handoff": {
                    "deterministic": True,
                    "llm_calls": 0,
                    "dependency_count": len(handoff),
                    "dependency_source_count": source_count,
                    "dependency_evidence_count": evidence_count,
                    "dependencies": handoff,
                },
            },
        )
