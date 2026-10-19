"""Offline comparison for the dependency shape used by a travel-planning run.

This is deliberately separate from ``bench_async_vs_sequential``.  That file
is a scheduler microbenchmark with an arbitrary fan-out width.  This benchmark
uses the production ``TaskGraph`` and ``GraphRuntime`` with the travel-workflow
dependency shape instead:

    must-have POI ─┐
    nearby POI ────┼─> spatial route validation ─┐
    hotel ─────────┘                              ├─> itinerary synthesis
    weather ──────────────────────────────────────┘

The five research task nodes represent four current agent profiles: the two
POI tasks intentionally share ``poi_agent``.  They are separate evidence
streams, not an invented fifth agent profile.  All three modes perform the
same five simulated I/O operations and produce the same final artifact:

* ``dag_parallel``: production ready-batch Runtime scheduling;
* ``dag_serial``: the same graph, Runtime and task executors, serialized;
* ``single_agent_serial``: one executor performs the same five research steps
  in sequence before producing the final artifact.

The delays model reproducible external evidence acquisition.  This is still an
offline, provider-free engineering benchmark: it validates dependency-aware
scheduling, not LLM quality, real API latency, or provider token usage.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from app.domain import Task, TaskGraph, TaskResult
from app.runtime.graph_runtime import GraphRuntime
from app.runtime.registry import ExecutorRegistry

from benchmarks.common.assertions import require, same_successful_outputs
from benchmarks.common.metrics import reduction, speedup, summary
from benchmarks.common.recorder import BenchmarkRecord


SAMPLES = 20
CASE_ID = "beijing_3_day_constraint_aware"
RESEARCH_TASK_IDS = (
    "must_have_poi_research",
    "nearby_poi_research",
    "hotel_research",
    "weather_research",
    "spatial_route_validation",
)
ROOT_RESEARCH_TASK_IDS = RESEARCH_TASK_IDS[:4]
DELAYS = {
    "must_have_poi_research": 0.045,
    "nearby_poi_research": 0.052,
    "hotel_research": 0.060,
    "weather_research": 0.035,
    "spatial_route_validation": 0.070,
    "itinerary_synthesis": 0.012,
}


def travel_workflow_graph() -> TaskGraph:
    """The dependency pattern of a constraint-aware multi-day travel plan."""
    return TaskGraph(
        graph_id="benchmark-travel-workflow",
        metadata={
            "benchmark_only": True,
            "workflow": "travel_research_dependency_v1",
            "research_task_count": len(RESEARCH_TASK_IDS),
            "agent_profiles": ["poi_agent", "hotel_agent", "weather_agent", "transport_agent"],
        },
        tasks=[
            Task(
                id="must_have_poi_research",
                task_type="must_have_poi_research",
                goal="核验北京航空航天大学等必去地点的开放、入口、游览时长与坐标",
                required_capabilities={"place_research"},
                assigned_agent="poi_agent",
            ),
            Task(
                id="nearby_poi_research",
                task_type="nearby_poi_research",
                goal="围绕必去地点寻找步行可达的相邻景点，给出距离和游览时长",
                required_capabilities={"place_research", "geospatial_reasoning"},
                assigned_agent="poi_agent",
            ),
            Task(
                id="hotel_research",
                task_type="accommodation_research",
                goal="筛选靠近核心游览区域、满足预算约束的住宿候选",
                required_capabilities={"accommodation_research"},
                assigned_agent="hotel_agent",
            ),
            Task(
                id="weather_research",
                task_type="weather_research",
                goal="查询旅行日期天气风险并给出室内外安排建议",
                required_capabilities={"weather_research"},
                assigned_agent="weather_agent",
            ),
            Task(
                id="spatial_route_validation",
                task_type="spatial_route_validation",
                goal="基于景点和酒店候选校验分日聚类与相邻地点的空间可行性",
                dependencies=["must_have_poi_research", "nearby_poi_research", "hotel_research"],
                required_capabilities={"geospatial_reasoning"},
                assigned_agent="transport_agent",
            ),
            Task(
                id="itinerary_synthesis",
                task_type="itinerary_synthesis",
                goal="汇聚研究证据，生成三日北京行程",
                dependencies=list(RESEARCH_TASK_IDS),
                required_capabilities={"itinerary_synthesis"},
                assigned_agent="itinerary_agent",
            ),
        ],
    )


def _artifact(task_id: str) -> dict[str, Any]:
    return {
        "must_have_poi_research": {"must_have_verified": ["北京航空航天大学"], "duration_hours": 2},
        "nearby_poi_research": {"nearby_candidates": ["奥林匹克森林公园", "中国科学技术馆"], "walkable": True},
        "hotel_research": {"hotel_area": "中关村", "nightly_budget_cny": 550},
        "weather_research": {"weather_risk": "low", "indoor_backup": "中国科学技术馆"},
        "spatial_route_validation": {"day_clusters_valid": True, "long_transfer_removed": True},
    }[task_id]


def _final_artifact() -> dict[str, Any]:
    return {
        "destination": "北京",
        "days": 3,
        "verified_evidence": list(RESEARCH_TASK_IDS),
        "spatially_validated": True,
        "itinerary": "按北航—奥森—科技馆的空间聚类安排，酒店位于中关村。",
    }


class TravelWorkflowExecutor:
    """Stable fake evidence service behind the real Runtime boundary."""

    executor_id = "benchmark-travel-workflow-executor"

    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, task: Task, context: Any) -> TaskResult:
        self.calls.append(task.id)
        started_at = datetime.now(UTC)
        await asyncio.sleep(DELAYS[task.id])
        if task.id == "spatial_route_validation":
            require(set(context.dependency_results) == set(task.dependencies), "spatial validation did not receive its required evidence")
        if task.id == "itinerary_synthesis":
            require(set(context.dependency_results) == set(RESEARCH_TASK_IDS), "synthesis did not receive all research evidence")
            output: Any = _final_artifact()
        else:
            output = _artifact(task.id)
        return TaskResult(
            task_id=task.id,
            success=True,
            output=output,
            executor_id=self.executor_id,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            metadata={
                "tool_calls": 0 if task.id == "itinerary_synthesis" else 1,
                "sources": [] if task.id == "itinerary_synthesis" else [f"fake://travel/{task.id}"],
                "benchmark_simulated_io": task.id != "itinerary_synthesis",
            },
        )


class SingleAgentTravelExecutor:
    """Single-agent baseline: identical research steps, no DAG scheduling."""

    executor_id = "benchmark-single-agent-executor"

    def __init__(self):
        self.calls: list[str] = []
        self.research_steps: list[str] = []

    async def execute(self, task: Task, _context: Any) -> TaskResult:
        self.calls.append(task.id)
        started_at = datetime.now(UTC)
        for research_task_id in RESEARCH_TASK_IDS:
            self.research_steps.append(research_task_id)
            await asyncio.sleep(DELAYS[research_task_id])
            _artifact(research_task_id)
        await asyncio.sleep(DELAYS["itinerary_synthesis"])
        return TaskResult(
            task_id=task.id,
            success=True,
            output=_final_artifact(),
            executor_id=self.executor_id,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            metadata={
                "tool_calls": len(RESEARCH_TASK_IDS),
                "logical_research_steps": list(self.research_steps),
                "logical_workflow_nodes": len(RESEARCH_TASK_IDS) + 1,
                "benchmark_simulated_io": True,
            },
        )


def _registry(executor: Any, capabilities: set[str]) -> ExecutorRegistry:
    registry = ExecutorRegistry()
    registry.register(executor, capabilities)
    return registry


async def _run_dag(*, parallel: bool):
    executor = TravelWorkflowExecutor()
    graph = travel_workflow_graph()
    started = time.perf_counter()
    state = await GraphRuntime(_registry(executor, set().union(*(task.required_capabilities for task in graph.tasks)))).run(
        graph,
        session_id=f"travel-workflow-{'parallel' if parallel else 'serial'}",
        parallel_ready_nodes=parallel,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return state, executor, elapsed_ms


async def _run_single_agent():
    executor = SingleAgentTravelExecutor()
    task = Task(
        id="itinerary_synthesis",
        task_type="single_agent_itinerary",
        goal="依次完成必去景点、周边景点、酒店、天气和空间校验，再生成三日北京行程",
        required_capabilities={"single_agent_travel_planning"},
        assigned_agent="single_agent",
    )
    started = time.perf_counter()
    state = await GraphRuntime(_registry(executor, {"single_agent_travel_planning"})).run(
        TaskGraph(graph_id="benchmark-single-agent-travel", tasks=[task]),
        session_id="travel-workflow-single-agent",
        parallel_ready_nodes=False,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return state, executor, elapsed_ms


async def run_benchmark(samples: int = SAMPLES) -> list[BenchmarkRecord]:
    records: list[BenchmarkRecord] = []
    for sample_id in range(samples):
        dag_parallel, parallel_executor, parallel_ms = await _run_dag(parallel=True)
        dag_serial, serial_executor, serial_ms = await _run_dag(parallel=False)
        single_agent, single_executor, single_ms = await _run_single_agent()

        require(same_successful_outputs(dag_parallel, dag_serial), "parallel and serial travel DAG outputs diverged")
        require(
            dag_parallel.task_results["itinerary_synthesis"].output == single_agent.task_results["itinerary_synthesis"].output,
            "single-agent baseline did not produce the same final itinerary artifact",
        )
        require(single_executor.research_steps == list(RESEARCH_TASK_IDS), "single agent did not complete every research step")

        for mode, state, executor, elapsed_ms, logical_nodes in (
            ("dag_parallel", dag_parallel, parallel_executor, parallel_ms, len(travel_workflow_graph().tasks)),
            ("dag_serial", dag_serial, serial_executor, serial_ms, len(travel_workflow_graph().tasks)),
            ("single_agent_serial", single_agent, single_executor, single_ms, len(RESEARCH_TASK_IDS) + 1),
        ):
            records.append(BenchmarkRecord(
                benchmark="travel_workflow_dependency_comparison",
                case_id=CASE_ID,
                mode=mode,
                sample_id=sample_id,
                success=state.current_status == "succeeded",
                elapsed_ms=round(elapsed_ms, 3),
                node_executions=len(executor.calls),
                tool_calls=len(RESEARCH_TASK_IDS),
                llm_calls=0,
                metadata={
                    "logical_workflow_nodes": logical_nodes,
                    "logical_research_steps": len(RESEARCH_TASK_IDS),
                    "same_final_artifact": True,
                    "root_research_nodes": list(ROOT_RESEARCH_TASK_IDS),
                    "dependent_research_node": "spatial_route_validation",
                    "fan_in_node": "itinerary_synthesis",
                    "simulated_external_io": True,
                    "external_llm_calls": 0,
                    "external_tool_api_calls": 0,
                },
            ))
    return records


def report_section(records: list[BenchmarkRecord]) -> str:
    modes = {mode: [record for record in records if record.mode == mode] for mode in ("dag_parallel", "dag_serial", "single_agent_serial")}
    stats = {mode: summary([record.elapsed_ms for record in values]) for mode, values in modes.items()}
    dag_parallel = stats["dag_parallel"]
    dag_serial = stats["dag_serial"]
    single_agent = stats["single_agent_serial"]
    return "\n".join([
        "## 1. Dependency-Aware Travel Workflow", "",
        "Offline business-shape comparison using the real TaskGraph and GraphRuntime. Five research task nodes perform the same simulated evidence work: four root streams (must-have POI, nearby POI, hotel, weather), then spatial validation depends on the two POI streams plus hotel; synthesis waits for all five. The POI streams share the current `poi_agent` profile, so this is five task nodes across four research profiles, not a fictitious five-profile claim.",
        "",
        "| Mode | Mean latency | P50 | P95 | Simulated tool calls | Runtime node executions | Final artifact |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| DAG parallel | {dag_parallel['mean']:.3f} ms | {dag_parallel['p50']:.3f} ms | {dag_parallel['p95']:.3f} ms | 5 | 6 | identical |",
        f"| Same DAG serialized | {dag_serial['mean']:.3f} ms | {dag_serial['p50']:.3f} ms | {dag_serial['p95']:.3f} ms | 5 | 6 | identical |",
        f"| Single agent serialized | {single_agent['mean']:.3f} ms | {single_agent['p50']:.3f} ms | {single_agent['p95']:.3f} ms | 5 | 1 (6 logical steps) | identical |",
        "",
        f"- Versus the identical serialized DAG: mean latency reduction {reduction(dag_serial['mean'], dag_parallel['mean']):.2f}%; speedup {speedup(dag_serial['mean'], dag_parallel['mean']):.2f}x.",
        f"- Versus the same-work single-agent serial baseline: mean latency reduction {reduction(single_agent['mean'], dag_parallel['mean']):.2f}%; speedup {speedup(single_agent['mean'], dag_parallel['mean']):.2f}x.",
        "- This measures only dependency-aware orchestration under controlled simulated I/O. It does not measure online LLM/API latency, model quality, or provider token consumption.",
    ])


async def _main() -> None:
    print(report_section(await run_benchmark()))


if __name__ == "__main__":
    asyncio.run(_main())
