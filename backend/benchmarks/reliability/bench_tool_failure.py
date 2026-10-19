from __future__ import annotations

import asyncio
import time

from app.domain import Task, TaskGraph
from app.executors.react import AgentProfile, ReActExecutor
from app.runtime.graph_runtime import GraphRuntime
from app.runtime.registry import ExecutorRegistry
from app.tools.registry import ToolRegistry

from benchmarks.common.fixtures import OneToolThenFinalLLM, ScriptedTool
from benchmarks.common.metrics import summary
from benchmarks.common.recorder import BenchmarkRecord


SAMPLE_COUNT = 20
RETRYABLE = ("transient_error", "rate_limit_like_error", "timeout")


async def _run(schedule: list[str], *, recover: bool):
    tool = ScriptedTool(schedule, max_retries=2 if recover else 0)
    tools = ToolRegistry()
    tools.register(tool)
    llm = OneToolThenFinalLLM()
    registry = ExecutorRegistry()
    registry.register(
        ReActExecutor(
            "benchmark-react", llm, tools,
            AgentProfile("fault-agent", {"fault_research"}, {"fault_tool"}, max_steps=1, max_tool_calls=1),
        ),
        {"fault_research"},
    )
    started = time.perf_counter()
    state = await GraphRuntime(registry).run(
        TaskGraph(graph_id="fault-recovery", tasks=[Task(id="fault", goal="read deterministic fact", required_capabilities={"fault_research"})]),
        session_id="fault",
    )
    elapsed = (time.perf_counter() - started) * 1000
    result = state.task_results["fault"]
    tool_result = result.metadata.get("tool_results", [{}])[0]
    return state, result, tool, llm, elapsed, int((tool_result.get("metadata") or {}).get("attempts", 1)) - 1


async def run_benchmark() -> list[BenchmarkRecord]:
    records: list[BenchmarkRecord] = []
    for rate in (0, 10, 20):
        failures = SAMPLE_COUNT * rate // 100
        for sample_id in range(SAMPLE_COUNT):
            fault = RETRYABLE[sample_id % len(RETRYABLE)] if sample_id < failures else "success"
            schedule = [fault, "success"] if fault != "success" else ["success"]
            for mode, recover in (("fail_fast", False), ("runtime_recovery", True)):
                state, result, tool, llm, elapsed, retry_count = await _run(schedule, recover=recover)
                records.append(BenchmarkRecord(
                    benchmark="tool_failure_recovery", case_id=f"failure_rate_{rate}", mode=mode,
                    sample_id=sample_id, success=result.success, elapsed_ms=round(elapsed, 3),
                    node_executions=1, tool_calls=tool.calls, llm_calls=llm.calls,
                    metadata={
                        "failure_rate_percent": rate, "fault_type": fault,
                        "task_success": result.success, "tool_success": bool(result.success),
                        "retry_count": retry_count, "recovery_success": bool(recover and fault != "success" and result.success),
                        "exhausted_budget_count": 0, "max_tool_calls": 1, "retry_limit": tool.spec.max_retries,
                    },
                ))
    for fault in ("malformed_result", "permanent_error"):
        for mode, recover in (("fail_fast", False), ("runtime_recovery", True)):
            state, result, tool, llm, elapsed, retry_count = await _run([fault], recover=recover)
            records.append(BenchmarkRecord(
                benchmark="tool_failure_recovery", case_id=f"edge_{fault}", mode=mode, sample_id=0,
                success=result.success, elapsed_ms=round(elapsed, 3), node_executions=1,
                tool_calls=tool.calls, llm_calls=llm.calls,
                metadata={"fault_type": fault, "task_success": result.success, "tool_success": bool(result.success), "retry_count": retry_count, "recovery_success": False, "unsupported_repair": True, "malformed_accepted_without_validation": fault == "malformed_result" and result.success},
            ))
    return records


def _success_rate(records):
    return sum(record.success for record in records) / len(records) * 100 if records else 0.0


def report_section(records: list[BenchmarkRecord]) -> str:
    lines = ["## 6. Tool Failure Recovery", "", "Uses production ToolRegistry timeout/retry rules and ReActExecutor. Failure schedules are fixed; malformed-output validation and semantic repair are not implemented by the current runtime.", "", "| Injected retryable failure rate | Fail-fast success | Runtime recovery success | Mean recovery latency | P95 recovery latency |", "|---:|---:|---:|---:|---:|"]
    for rate in (0, 10, 20):
        baseline = [item for item in records if item.case_id == f"failure_rate_{rate}" and item.mode == "fail_fast"]
        recovery = [item for item in records if item.case_id == f"failure_rate_{rate}" and item.mode == "runtime_recovery"]
        stats = summary([item.elapsed_ms for item in recovery])
        lines.append(f"| {rate}% | {_success_rate(baseline):.1f}% | {_success_rate(recovery):.1f}% | {stats['mean']:.3f} ms | {stats['p95']:.3f} ms |")
    lines.extend(["", "- `permanent_error` remains failed in both modes. A syntactically valid but malformed payload is accepted as successful in both modes, which correctly exposes that output-schema validation and semantic repair are NOT_IMPLEMENTED in the current production runtime."])
    return "\n".join(lines)


async def _main():
    print(report_section(await run_benchmark()))


if __name__ == "__main__":
    asyncio.run(_main())
