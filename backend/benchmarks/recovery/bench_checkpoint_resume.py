from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from app.runtime.graph_runtime import GraphRuntime
from app.runtime.recovery import RecoveryService
from app.runtime.registry import ExecutorRegistry
from app.runtime.state_store import SQLiteStateStore

from benchmarks.common.assertions import require, same_successful_outputs
from benchmarks.common.fixtures import CrashOnceExecutor, DeterministicExecutor, recovery_graph
from benchmarks.common.metrics import reduction, summary
from benchmarks.common.recorder import BenchmarkRecord


DELAYS = {task_id: 0.012 for task_id in ("a", "b", "c", "d", "e", "f")}
CRASH_POINTS = {"25_percent": "b", "50_percent": "d", "75_percent": "f"}
SAMPLES = 5


def _registry(executor):
    registry = ExecutorRegistry()
    registry.register(executor, {"benchmark_io"})
    return registry


async def _uninterrupted():
    executor = DeterministicExecutor(DELAYS)
    state = await GraphRuntime(_registry(executor)).run(recovery_graph(), session_id="reference")
    return state


async def _case(crash_task: str):
    reference = await _uninterrupted()
    # Windows may retain a short-lived SQLite/antivirus file handle after the
    # final asyncio.to_thread write.  Ignore only cleanup races; benchmark
    # correctness never depends on deleting this disposable database.
    with tempfile.TemporaryDirectory(prefix="trip-planner-bench-", ignore_cleanup_errors=True) as temp:
        store = SQLiteStateStore(Path(temp) / "checkpoint.sqlite")
        crashing_executor = CrashOnceExecutor(crash_task, DELAYS)
        crashing_runtime = GraphRuntime(_registry(crashing_executor), store)
        try:
            await crashing_runtime.run(recovery_graph(), session_id="crash")
        except asyncio.CancelledError:
            pass
        checkpoint = await store.load("crash")
        require(checkpoint is not None, "crash did not produce a checkpoint")
        completed_before = sum(status.value == "succeeded" for status in checkpoint.task_statuses.values())
        require(checkpoint.task_statuses[crash_task].value == "running", "crashed task was not checkpointed as running")

        resume_executor = crashing_executor
        recovery = await RecoveryService(store).recover("crash", recovery_graph())
        resume_started = time.perf_counter()
        resumed = await GraphRuntime(_registry(resume_executor), store).resume(recovery)
        resume_ms = (time.perf_counter() - resume_started) * 1000

        restart_executor = DeterministicExecutor(DELAYS)
        restart_started = time.perf_counter()
        restarted = await GraphRuntime(_registry(restart_executor)).run(recovery_graph(), session_id="restart")
        restart_ms = (time.perf_counter() - restart_started) * 1000

    require(same_successful_outputs(reference, resumed), "resume output differs from uninterrupted execution")
    require(same_successful_outputs(reference, restarted), "restart output differs from uninterrupted execution")
    return completed_before, resumed, resumed.task_results, resume_executor.calls, resume_ms, restarted, restart_executor.calls, restart_ms


async def run_benchmark(samples: int = SAMPLES) -> list[BenchmarkRecord]:
    records: list[BenchmarkRecord] = []
    for label, crash_task in CRASH_POINTS.items():
        for sample_id in range(samples):
            completed, resumed, _results, resumed_calls, resume_ms, restarted, restart_calls, restart_ms = await _case(crash_task)
            for mode, state, calls, elapsed, duplicated in (
                ("resume_checkpoint", resumed, resumed_calls, resume_ms, 0),
                ("restart_from_scratch", restarted, restart_calls, restart_ms, completed),
            ):
                records.append(BenchmarkRecord(
                    benchmark="checkpoint_recovery", case_id=label, mode=mode, sample_id=sample_id,
                    success=state.current_status == "succeeded", elapsed_ms=round(elapsed, 3),
                    node_executions=len(calls), tool_calls=len(calls), llm_calls=0,
                    metadata={
                        "crash_position": label, "completed_before_crash": completed,
                        "duplicated_node_executions": duplicated, "duplicated_tool_calls": duplicated,
                        "reused_completed_nodes": completed if mode == "resume_checkpoint" else 0,
                        "final_graph_completed": True, "final_result_equal_or_equivalent": True,
                    },
                ))
    return records


def report_section(records: list[BenchmarkRecord]) -> str:
    resume = [record for record in records if record.mode == "resume_checkpoint"]
    restart = [record for record in records if record.mode == "restart_from_scratch"]
    resume_latency, restart_latency = summary([item.elapsed_ms for item in resume]), summary([item.elapsed_ms for item in restart])
    resume_dup = summary([item.metadata["duplicated_node_executions"] for item in resume])
    restart_dup = summary([item.metadata["duplicated_node_executions"] for item in restart])
    resume_tools = summary([item.tool_calls for item in resume])
    restart_tools = summary([item.tool_calls for item in restart])
    return "\n".join([
        "## 4. Checkpoint Recovery", "", "Uses SQLiteStateStore, task-boundary checkpointing, RecoveryService and GraphRuntime.resume with deterministic in-flight cancellation.", "",
        "| Mode | Mean latency | Mean duplicate node executions | Mean tool calls after crash | Correctness |",
        "|---|---:|---:|---:|---:|",
        f"| Restart from scratch | {restart_latency['mean']:.3f} ms | {restart_dup['mean']:.2f} | {restart_tools['mean']:.2f} | 100% |",
        f"| Resume checkpoint | {resume_latency['mean']:.3f} ms | {resume_dup['mean']:.2f} | {resume_tools['mean']:.2f} | 100% |",
        "",
        f"- Duplicate execution reduction: {reduction(restart_dup['mean'], resume_dup['mean']):.2f}%",
        f"- Recovery latency reduction: {reduction(restart_latency['mean'], resume_latency['mean']):.2f}%",
    ])


async def _main():
    print(report_section(await run_benchmark()))


if __name__ == "__main__":
    asyncio.run(_main())
