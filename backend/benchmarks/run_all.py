from __future__ import annotations

import asyncio
import random

from benchmarks.common.metrics import reduction, speedup, summary
from benchmarks.common.offline import offline_guard
from benchmarks.common.recorder import write_artifacts
from benchmarks.context.bench_evidence_compression import report_section as context_report
from benchmarks.context.bench_evidence_compression import run_benchmark as run_context
from benchmarks.graph_runtime.bench_async_vs_sequential import report_section as graph_report
from benchmarks.graph_runtime.bench_async_vs_sequential import run_benchmark as run_graph
from benchmarks.graph_runtime.bench_travel_workflow_comparison import report_section as travel_workflow_report
from benchmarks.graph_runtime.bench_travel_workflow_comparison import run_benchmark as run_travel_workflow
from benchmarks.recovery.bench_checkpoint_resume import report_section as recovery_report
from benchmarks.recovery.bench_checkpoint_resume import run_benchmark as run_recovery
from benchmarks.reliability.bench_tool_failure import report_section as reliability_report
from benchmarks.reliability.bench_tool_failure import run_benchmark as run_reliability
from benchmarks.replanning.bench_incremental_vs_full import report_section as replanning_report
from benchmarks.replanning.bench_incremental_vs_full import run_benchmark as run_replanning


def _safe_conclusions(records) -> str:
    travel = [item for item in records if item.benchmark == "travel_workflow_dependency_comparison"]
    travel_parallel = summary([item.elapsed_ms for item in travel if item.mode == "dag_parallel"])
    travel_serial = summary([item.elapsed_ms for item in travel if item.mode == "dag_serial"])
    single_agent = summary([item.elapsed_ms for item in travel if item.mode == "single_agent_serial"])
    async_records = [item for item in records if item.benchmark == "async_dag_scheduling"]
    sequential = summary([item.elapsed_ms for item in async_records if item.case_id == "width_5" and item.mode == "sequential"])
    concurrent = summary([item.elapsed_ms for item in async_records if item.case_id == "width_5" and item.mode == "async_ready_batch"])
    replanning = [item for item in records if item.benchmark == "incremental_replanning"]
    full_replan = summary([item.node_executions for item in replanning if item.mode == "full_replan"])
    incremental = summary([item.node_executions for item in replanning if item.mode == "incremental"])
    recovery = [item for item in records if item.benchmark == "checkpoint_recovery"]
    restart_duplicate = summary([item.metadata["duplicated_tool_calls"] for item in recovery if item.mode == "restart_from_scratch"])
    resume_duplicate = summary([item.metadata["duplicated_tool_calls"] for item in recovery if item.mode == "resume_checkpoint"])
    context = [item for item in records if item.benchmark == "evidence_context_compression" and item.case_id == "large"]
    raw = next(item for item in context if item.mode == "raw_context")
    evidence = next(item for item in context if item.mode == "evidence_context")
    reliability = [item for item in records if item.benchmark == "tool_failure_recovery" and item.case_id == "failure_rate_20"]
    fail_fast = sum(item.success for item in reliability if item.mode == "fail_fast") / 20 * 100
    recovered = sum(item.success for item in reliability if item.mode == "runtime_recovery") / 20 * 100
    return "\n".join([
        "## Resume-Safe Conclusions", "", "### Safe Resume Claims",
        f"- In a 20-sample offline dependency-aware travel workflow, ready-node scheduling reduced mean latency by {reduction(travel_serial['mean'], travel_parallel['mean']):.1f}% ({speedup(travel_serial['mean'], travel_parallel['mean']):.2f}x) versus the identical serialized DAG; it reduced latency by {reduction(single_agent['mean'], travel_parallel['mean']):.1f}% versus a same-work single-agent serial baseline. All modes completed five simulated evidence operations and produced an identical final artifact.",
        f"- Separately, the synthetic width-5 scheduler microbenchmark reduced mean latency by {reduction(sequential['mean'], concurrent['mean']):.1f}% versus the same Runtime's sequential mode. This validates fan-out scheduling only, not travel-workflow performance.",
        f"- Across 20 fixed local-constraint changes, Incremental Replanning reduced mean node re-execution by {reduction(full_replan['mean'], incremental['mean']):.1f}% while all reuse and graph-validity checks passed.",
        f"- Across deterministic 25%/50%/75% interruption points, checkpoint resume reduced duplicate tool calls by {reduction(restart_duplicate['mean'], resume_duplicate['mean']):.1f}% with output-equivalence checks passing.",
        f"- Production evidence-context transfer reduced the large synthetic payload's estimated downstream input tokens by {reduction(raw.prompt_tokens, evidence.prompt_tokens):.1f}% while preserving 100% of predefined required facts.",
        f"- Under deterministic 20% retryable tool-failure injection, current timeout/network/rate-limit recovery raised task success from {fail_fast:.1f}% to {recovered:.1f}%.",
        "", "### Claims NOT Safe for Resume",
        "- Do not present these offline synthetic latency numbers as production latency or provider-token usage.",
        "- Do not present UTF-8-byte/4 estimates as real model token accounting.",
        "- Do not claim malformed-result validation or semantic repair: the current Runtime does not implement them.",
        "- Do not infer recommendation quality or model accuracy from this engineering harness.",
    ])


async def main() -> None:
    random.seed(20260911)
    with offline_guard():
        travel_workflow = await run_travel_workflow()
        graph = await run_graph()
        replanning = await run_replanning()
        recovery = await run_recovery()
        context = await run_context()
        reliability = await run_reliability()
    records = [*travel_workflow, *graph, *replanning, *recovery, *context, *reliability]
    sections = [travel_workflow_report(travel_workflow), graph_report(graph), replanning_report(replanning), recovery_report(recovery), context_report(context), reliability_report(reliability), _safe_conclusions(records)]
    output = write_artifacts(records, sections)
    print(f"OFFLINE SYNTHETIC TOOL BENCHMARK complete: {output}")
    print("External LLM calls: 0")
    print("External Tool API calls: 0")


if __name__ == "__main__":
    asyncio.run(main())
