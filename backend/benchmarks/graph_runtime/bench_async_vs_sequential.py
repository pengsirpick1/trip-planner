from __future__ import annotations

import asyncio
import time

from app.runtime.graph_runtime import GraphRuntime
from app.runtime.registry import ExecutorRegistry

from benchmarks.common.assertions import require, same_successful_outputs
from benchmarks.common.fixtures import DeterministicExecutor, parallel_graph
from benchmarks.common.metrics import reduction, speedup, summary
from benchmarks.common.recorder import BenchmarkRecord


DELAYS = (0.03, 0.05, 0.07, 0.09, 0.10)
SAMPLES = 20


async def _run_one(width: int, parallel: bool):
    executor = DeterministicExecutor({f"research_{index}": DELAYS[index] for index in range(width)} | {"synthesis": 0.01})
    registry = ExecutorRegistry()
    registry.register(executor, {"benchmark_io", "benchmark_synthesis"})
    started = time.perf_counter()
    state = await GraphRuntime(registry).run(parallel_graph(width), session_id=f"scheduler-{width}-{parallel}", parallel_ready_nodes=parallel)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return state, elapsed_ms, len(executor.calls)


async def run_benchmark(samples: int = SAMPLES) -> list[BenchmarkRecord]:
    records: list[BenchmarkRecord] = []
    for width in (2, 3, 5):
        for sample_id in range(samples):
            sequential, sequential_ms, sequential_nodes = await _run_one(width, parallel=False)
            concurrent, concurrent_ms, concurrent_nodes = await _run_one(width, parallel=True)
            require(same_successful_outputs(sequential, concurrent), "parallel and sequential outputs diverged")
            for mode, state, elapsed, nodes in (
                ("sequential", sequential, sequential_ms, sequential_nodes),
                ("async_ready_batch", concurrent, concurrent_ms, concurrent_nodes),
            ):
                records.append(BenchmarkRecord(
                    benchmark="async_dag_scheduling", case_id=f"width_{width}", mode=mode,
                    sample_id=sample_id, success=state.current_status == "succeeded", elapsed_ms=round(elapsed, 3),
                    node_executions=nodes, tool_calls=width, llm_calls=0,
                    metadata={"total_nodes": width + 1, "parallel_width": width, "correctness_pass": True},
                ))
    return records


def report_section(records: list[BenchmarkRecord]) -> str:
    lines = ["## 2. Runtime Scheduler Microbenchmark", "", "Synthetic fan-out only: same TaskGraph and executor; only the formal Runtime ready-batch scheduling flag changes. This section validates scheduler behavior and is not a travel-workflow performance claim.", "", "| Parallel width | Sequential mean | Async mean | P50 async | P95 async | Reduction | Speedup | Correctness |", "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for width in (2, 3, 5):
        baseline = [item.elapsed_ms for item in records if item.case_id == f"width_{width}" and item.mode == "sequential"]
        async_values = [item.elapsed_ms for item in records if item.case_id == f"width_{width}" and item.mode == "async_ready_batch"]
        left, right = summary(baseline), summary(async_values)
        lines.append(f"| {width} | {left['mean']:.3f} ms | {right['mean']:.3f} ms | {right['p50']:.3f} ms | {right['p95']:.3f} ms | {reduction(left['mean'], right['mean']):.2f}% | {speedup(left['mean'], right['mean']):.2f}x | 100% |")
    return "\n".join(lines)


async def _main():
    section_records = await run_benchmark()
    print(report_section(section_records))


if __name__ == "__main__":
    asyncio.run(_main())
