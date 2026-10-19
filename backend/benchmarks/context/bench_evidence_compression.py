from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.domain import TaskResult
from app.executors.react import _dependency_context

from benchmarks.common.assertions import require
from benchmarks.common.metrics import estimate_tokens, reduction
from benchmarks.common.recorder import BenchmarkRecord


CASE_PATH = Path(__file__).resolve().parents[1] / "cases" / "context_cases.json"
FACTS = ("hotel_name=Offline Hotel", "price=600", "location=Central", "rating=4.7", "opening_hours=09:00-17:00", "weather=Sunny", "transport_duration=25")


def _results(payload_chars: int) -> dict[str, TaskResult]:
    result = {}
    for task_id in ("hotel", "attractions", "weather", "transport"):
        result[task_id] = TaskResult(
            task_id=task_id,
            success=True,
            output="; ".join(FACTS),
            metadata={
                "sources": [f"fake://{task_id}"],
                "tool_results": [{"tool_name": f"{task_id}_tool", "data": {"raw_payload": "x" * payload_chars, "facts": FACTS}}],
                "llm_steps": [{"private_trace": "y" * payload_chars}],
            },
        )
    return result


async def run_benchmark() -> list[BenchmarkRecord]:
    cases = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    records: list[BenchmarkRecord] = []
    for sample_id, case in enumerate(cases):
        results = _results(case["payload_chars"])
        raw = json.dumps({key: value.model_dump(mode="json") for key, value in results.items()}, ensure_ascii=False, sort_keys=True)
        evidence = json.dumps(_dependency_context(results), ensure_ascii=False, sort_keys=True)
        raw_tokens, evidence_tokens = estimate_tokens(raw), estimate_tokens(evidence)
        preserved = sum(fact in evidence for fact in FACTS) / len(FACTS)
        require(preserved == 1.0, f"required fact lost for {case['case_id']}")
        records.append(BenchmarkRecord(
            benchmark="evidence_context_compression", case_id=case["case_id"], mode="raw_context",
            sample_id=sample_id, success=True, elapsed_ms=0.0, node_executions=0, tool_calls=0, llm_calls=0,
            prompt_tokens=raw_tokens, completion_tokens=0,
            metadata={"context_chars": len(raw), "context_tokens_estimate": raw_tokens, "peak_context_tokens": raw_tokens, "required_fact_preservation_rate": 1.0, "token_method": "utf8_bytes_div_4_estimate"},
        ))
        records.append(BenchmarkRecord(
            benchmark="evidence_context_compression", case_id=case["case_id"], mode="evidence_context",
            sample_id=sample_id, success=True, elapsed_ms=0.0, node_executions=0, tool_calls=0, llm_calls=0,
            prompt_tokens=evidence_tokens, completion_tokens=0,
            metadata={"context_chars": len(evidence), "context_tokens_estimate": evidence_tokens, "peak_context_tokens": evidence_tokens, "required_fact_preservation_rate": 1.0, "token_method": "utf8_bytes_div_4_estimate"},
        ))
    return records


def report_section(records: list[BenchmarkRecord]) -> str:
    lines = ["## 5. Context / Evidence Compression", "", "Uses production ReAct `_dependency_context`; token values are offline UTF-8-byte/4 estimates, not provider usage.", "", "| Payload | Raw context | Evidence context | Estimated reduction | Required facts preserved |", "|---|---:|---:|---:|---:|"]
    for case_id in ("small", "medium", "large"):
        raw = next(item for item in records if item.case_id == case_id and item.mode == "raw_context")
        evidence = next(item for item in records if item.case_id == case_id and item.mode == "evidence_context")
        lines.append(f"| {case_id} | {raw.prompt_tokens} est. tokens | {evidence.prompt_tokens} est. tokens | {reduction(raw.prompt_tokens, evidence.prompt_tokens):.2f}% | {evidence.metadata['required_fact_preservation_rate'] * 100:.0f}% |")
    return "\n".join(lines)


async def _main():
    print(report_section(await run_benchmark()))


if __name__ == "__main__":
    asyncio.run(_main())
