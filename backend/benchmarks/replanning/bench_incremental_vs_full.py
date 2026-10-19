from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from app.application.replanning_service import ReplanningService
from app.application.revision_scope import select_revision_targets
from app.domain import GraphExecutionState, PlanningContext, UserIntent
from app.runtime.graph_runtime import GraphRuntime
from app.runtime.registry import ExecutorRegistry

from benchmarks.common.assertions import require
from benchmarks.common.fixtures import DeterministicExecutor, replanning_graph
from benchmarks.common.metrics import reduction, summary
from benchmarks.common.recorder import BenchmarkRecord


CASE_PATH = Path(__file__).resolve().parents[1] / "cases" / "replanning_cases.json"
CAPABILITIES = {"geospatial_reasoning", "accommodation_research", "place_research", "weather_research", "restaurant_research", "itinerary_synthesis"}
DELAYS = {"transport": 0.018, "hotel": 0.018, "attractions": 0.018, "weather": 0.018, "restaurant": 0.018, "synthesis": 0.018}


def _registry(executor: DeterministicExecutor) -> ExecutorRegistry:
    registry = ExecutorRegistry()
    registry.register(executor, CAPABILITIES)
    return registry


def _context() -> PlanningContext:
    return PlanningContext(
        session_id="benchmark-replan",
        intent=UserIntent(session_id="benchmark-replan", raw_query="offline replanning", task_type="travel", planning_intent=True, destination="北京", duration_days=3),
        available_capabilities=CAPABILITIES,
    )


async def _initial_state() -> GraphExecutionState:
    executor = DeterministicExecutor(DELAYS)
    return await GraphRuntime(_registry(executor)).run(replanning_graph(), session_id="benchmark-replan")


async def run_benchmark() -> list[BenchmarkRecord]:
    cases = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    service = ReplanningService()
    records: list[BenchmarkRecord] = []
    for sample_id, case in enumerate(cases):
        previous = await _initial_state()
        targets = select_revision_targets(previous.graph, case["message"])
        scope = service.compute_repair_scope(previous.graph, targets)
        hints = {task_id: [case["change"]] for task_id in scope}
        incremental_outcome = service.build_repair_graph(previous.graph, scope, hints, _context())
        require(incremental_outcome.success and incremental_outcome.graph is not None, f"incremental graph build failed: {case['case_id']}")
        incremental_executor = DeterministicExecutor(DELAYS)
        incremental_initial = service.merge_state(previous, incremental_outcome.graph, scope)
        incremental_started = time.perf_counter()
        incremental = await GraphRuntime(_registry(incremental_executor)).run(
            incremental_outcome.graph, session_id="benchmark-replan", initial_state=incremental_initial
        )
        incremental_ms = (time.perf_counter() - incremental_started) * 1000

        all_tasks = [task.id for task in previous.graph.tasks]
        full_outcome = service.build_repair_graph(previous.graph, all_tasks, {task_id: [case["change"]] for task_id in all_tasks}, _context())
        require(full_outcome.success and full_outcome.graph is not None, f"full graph build failed: {case['case_id']}")
        full_executor = DeterministicExecutor(DELAYS)
        full_started = time.perf_counter()
        full = await GraphRuntime(_registry(full_executor)).run(full_outcome.graph, session_id="benchmark-replan")
        full_ms = (time.perf_counter() - full_started) * 1000

        kept_ids = [task_id for task_id in all_tasks if task_id not in scope]
        require(incremental.current_status == full.current_status == "succeeded", "replan did not complete")
        require(all(incremental.task_results[task_id].output == previous.task_results[task_id].output for task_id in kept_ids), "unaffected result was not reused")
        require(all(case["change"] in incremental.task_results[task_id].output for task_id in scope), "updated constraint not visible in rerun task")
        incremental.graph.validate_graph()

        for mode, state, executor, elapsed in (
            ("incremental", incremental, incremental_executor, incremental_ms),
            ("full_replan", full, full_executor, full_ms),
        ):
            reexecuted = len(executor.calls)
            records.append(BenchmarkRecord(
                benchmark="incremental_replanning", case_id=case["case_id"], mode=mode,
                sample_id=sample_id, success=True, elapsed_ms=round(elapsed, 3),
                # State contains reused historical evidence in incremental mode;
                # count only the external-work simulator calls made by this run.
                node_executions=reexecuted,
                tool_calls=sum(task_id != "synthesis" for task_id in executor.calls),
                llm_calls=0,
                metadata={
                    "total_graph_nodes": len(all_tasks), "affected_nodes": len(scope),
                    "reexecuted_nodes": reexecuted, "reused_nodes": len(kept_ids) if mode == "incremental" else 0,
                    "planner_calls": 0, "final_graph_valid": True,
                    # Offline evidence proves that the formal repair hint reaches
                    # every rerun node.  It cannot prove an LLM-produced travel
                    # plan satisfies the request without calling a real model.
                    "updated_constraint_satisfied": None,
                    "updated_constraint_propagated": True,
                    "scope": scope, "expected_domain": case["expected_domain"],
                },
            ))
    return records


def report_section(records: list[BenchmarkRecord]) -> str:
    incremental = [record for record in records if record.mode == "incremental"]
    full = [record for record in records if record.mode == "full_replan"]
    inc_latency, full_latency = summary([item.elapsed_ms for item in incremental]), summary([item.elapsed_ms for item in full])
    inc_nodes = summary([item.node_executions for item in incremental])
    full_nodes = summary([item.node_executions for item in full])
    inc_tools = summary([item.tool_calls for item in incremental])
    full_tools = summary([item.tool_calls for item in full])
    return "\n".join([
        "## 3. Incremental Replanning", "", "Uses ReplanningService scope calculation, graph versioning and merge_state reuse; 20 fixed constraint-change cases.", "",
        "| Mode | Mean latency | Mean re-executed nodes | Mean tool calls | Runtime guards |",
        "|---|---:|---:|---:|---:|",
        f"| Full replan | {full_latency['mean']:.3f} ms | {full_nodes['mean']:.2f} | {full_tools['mean']:.2f} | 100% |",
        f"| Incremental | {inc_latency['mean']:.3f} ms | {inc_nodes['mean']:.2f} | {inc_tools['mean']:.2f} | 100% |",
        "",
        f"- Node re-execution reduction: {reduction(full_nodes['mean'], inc_nodes['mean']):.2f}%",
        f"- Tool-call reduction: {reduction(full_tools['mean'], inc_tools['mean']):.2f}%",
        f"- Latency reduction: {reduction(full_latency['mean'], inc_latency['mean']):.2f}%",
        "- Semantic itinerary-quality validation is NOT_IMPLEMENTED_OFFLINE; this benchmark verifies scope, dependency invalidation, result reuse and repair-hint propagation only.",
    ])


async def _main():
    print(report_section(await run_benchmark()))


if __name__ == "__main__":
    asyncio.run(_main())
