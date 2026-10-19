"""Phase B：Runtime/ReAct/检查点/恢复。"""

from __future__ import annotations

from datetime import UTC, datetime
import asyncio
import time

import pytest

from app.domain import Task, TaskGraph, TaskResult, TaskStatus
from app.executors.react import AgentProfile, ReActExecutor, _compact
from app.runtime.graph_runtime import GraphRuntime
from app.runtime.worker_pool import GlobalWorkerPool
from app.runtime.recovery import RecoveryService
from app.runtime.registry import ExecutorRegistry
from app.runtime.state_store import SQLiteStateStore
from app.tools import ToolRegistry
from tests.fakes import CannedStructuredLLM, FakeWeatherTool

_CAPS = {"weather_research", "itinerary_synthesis"}


def _weather_graph(graph_id="g1", version=1) -> TaskGraph:
    return TaskGraph(
        graph_id=graph_id,
        version=version,
        tasks=[
            Task(
                id="tokyo",
                goal="Use the weather tool for CURRENT conditions in Tokyo",
                required_capabilities={"weather_research"},
                expected_output="measured conditions",
                completion_criteria="measured",
            ),
            Task(
                id="kyoto",
                goal="Use the weather tool for CURRENT conditions in Kyoto",
                required_capabilities={"weather_research"},
                expected_output="measured conditions",
                completion_criteria="measured",
            ),
            Task(
                id="compare",
                goal="Compare the measured Tokyo and Kyoto conditions; do not repeat the queries",
                dependencies=["tokyo", "kyoto"],
                required_capabilities={"itinerary_synthesis"},
                expected_output="comparison",
                completion_criteria="uses dependency results",
            ),
        ],
    )


def _build_runtime(llm=None, store=None, event_callback=None):
    tools = ToolRegistry()
    tools.register(FakeWeatherTool())
    registry = ExecutorRegistry()
    registry.register(
        ReActExecutor(
            "react-test",
            llm if llm is not None else CannedStructuredLLM(),
            tools,
            AgentProfile("test-agent", _CAPS, {"weather"}, max_steps=4),
        ),
        _CAPS,
    )
    return GraphRuntime(registry, store, event_callback=event_callback)


async def test_runtime_runs_dag_with_tools_and_dependencies(workdir):
    store = SQLiteStateStore(workdir / "checkpoints.sqlite")
    events: list[tuple[str, dict]] = []
    runtime = _build_runtime(store=store, event_callback=lambda e, p: events.append((e, p)))
    state = await runtime.run(_weather_graph(), session_id="s1")

    assert state.current_status == "succeeded"
    assert all(status is TaskStatus.SUCCEEDED for status in state.task_statuses.values())
    compare = state.task_results["compare"]
    assert compare.success
    assert "tokyo" in compare.metadata["dependency_results"]
    assert "kyoto" in compare.metadata["dependency_results"]
    # 下游合成不得重复调工具
    assert compare.metadata["tool_calls"] == 0
    # 真实执行：tokyo/kyoto 各 1 次工具调用，且不重复
    assert state.task_results["tokyo"].metadata["tool_calls"] == 1
    names = [name for name, _ in events]
    assert "task_started" in names and "graph_completed" in names


async def test_react_enforces_hard_tool_budget(workdir):
    """模型单轮返回多个调用也不能绕过每任务工具预算。"""
    class MultiCallLLM:
        def __init__(self):
            self.calls = 0
            self.messages = []

        async def decide(self, task, observations, **kwargs):
            self.calls += 1
            self.messages = kwargs.get("messages", [])
            if self.calls == 1:
                calls = [
                    {"id": "a", "function": {"name": "weather", "arguments": '{"city":"Tokyo"}'}},
                    {"id": "b", "function": {"name": "weather", "arguments": '{"city":"Kyoto"}'}},
                ]
                return {"tool_calls": calls, "assistant_message": {"role": "assistant", "tool_calls": calls}, "usage": {}}
            return {"final": "enough evidence", "usage": {}}

    tools = ToolRegistry()
    tools.register(FakeWeatherTool())
    llm = MultiCallLLM()
    registry = ExecutorRegistry()
    registry.register(
        ReActExecutor("bounded", llm, tools,
                      AgentProfile("bounded", {"weather_research"}, {"weather"}, max_steps=3, max_tool_calls=1)),
        {"weather_research"},
    )
    state = await GraphRuntime(registry).run(
        TaskGraph(graph_id="bounded", tasks=[Task(id="weather", goal="weather", required_capabilities={"weather_research"})])
    )
    result = state.task_results["weather"]
    assert result.success
    assert result.metadata["tool_calls"] == 1
    assert result.metadata["token_optimization"]["tool_calls_used"] == 1
    assert any(item["kind"] == "tool_skipped_budget" for item in result.metadata["llm_steps"])
    skipped_response = next(message for message in llm.messages if message.get("tool_call_id") == "b")
    assert "TOOL_CALL_SKIPPED_BUDGET" in skipped_response["content"]


async def test_react_runs_one_turn_tool_calls_concurrently():
    """Independent calls returned in one assistant message must not serialize."""
    from app.tools.base import ToolResult, ToolSpec

    class SlowTool:
        spec = ToolSpec(name="slow", description="slow", capabilities={"weather_research"}, timeout_seconds=2)
        async def execute(self, args, context):
            await asyncio.sleep(0.08)
            return ToolResult(tool_call_id=context["tool_call_id"], tool_name="slow", success=True, data=args)

    class BatchLLM:
        calls = 0
        async def decide(self, task, observations, **kwargs):
            self.calls += 1
            if self.calls == 1:
                calls = [{"id": key, "function": {"name": "slow", "arguments": "{}"}} for key in ("a", "b")]
                return {"tool_calls": calls, "assistant_message": {"role": "assistant", "tool_calls": calls}, "usage": {}}
            return {"final": "done", "usage": {}}

    tools = ToolRegistry(); tools.register(SlowTool())
    registry = ExecutorRegistry()
    registry.register(ReActExecutor("parallel", BatchLLM(), tools,
                      AgentProfile("parallel", {"weather_research"}, {"slow"}, max_steps=1, max_tool_calls=2)),
                      {"weather_research"})
    started = time.monotonic()
    state = await GraphRuntime(registry).run(TaskGraph(graph_id="parallel", tasks=[Task(id="a", goal="a", required_capabilities={"weather_research"})]))
    elapsed = time.monotonic() - started
    result = state.task_results["a"]
    assert result.success
    assert elapsed < 0.14
    assert result.metadata["metrics"]["tool_wait_time_ms"] >= 150
    assert result.metadata["metrics"]["total_time_ms"] < 140


def test_compact_context_drops_nested_tool_history():
    compact = _compact({"tool_results": [{"data": "x" * 5000}], "output": "ok"})
    assert compact == {"output": "ok"}


async def test_checkpoint_persists_latest_snapshot(workdir):
    store = SQLiteStateStore(workdir / "checkpoints.sqlite")
    runtime = _build_runtime(store=store)
    await runtime.run(_weather_graph(), session_id="ck")
    restored = await store.load("ck")
    assert restored is not None
    assert restored.task_results.keys() == {"tokyo", "kyoto", "compare"}
    assert restored.graph.version == 1
    # SQLite 单 session 行：同 key 二次运行覆盖最新快照（版本历史属 Phase F）
    await runtime.run(_weather_graph(), session_id="ck")
    restored2 = await store.load("ck")
    assert restored2 is not None and restored2.current_status == "succeeded"


async def test_recovery_resets_running_to_pending(workdir):
    store = SQLiteStateStore(workdir / "checkpoints.sqlite")
    runtime = _build_runtime(store=store)
    await runtime.run(_weather_graph(), session_id="rc")

    crashed = await store.load("rc")
    crashed.current_status = "running"
    crashed.task_statuses["tokyo"] = TaskStatus.RUNNING
    await store.save(crashed)

    recovered = await RecoveryService(store).recover("rc", crashed.graph)
    assert recovered.task_statuses["tokyo"] is TaskStatus.PENDING
    assert "tokyo" in recovered.metadata["recovery"]["recovered_task_ids"]

    resumed = await runtime.resume(recovered)
    assert resumed.current_status == "succeeded"


class _FailingExecutor:
    executor_id = "fail-exec"

    async def execute(self, task, context):
        return TaskResult(
            task_id=task.id,
            success=False,
            error="boom",
            finished_at=datetime.now(UTC),
        )


async def test_failed_parent_skips_downstream(workdir):
    registry = ExecutorRegistry()
    registry.register(_FailingExecutor(), {"research", "dep"})
    runtime = GraphRuntime(registry, SQLiteStateStore(workdir / "fail.sqlite"))
    graph = TaskGraph(
        graph_id="fail",
        tasks=[
            Task(id="a", goal="a", required_capabilities={"research"}),
            Task(id="b", goal="b", dependencies=["a"], required_capabilities={"dep"}),
        ],
    )
    state = await runtime.run(graph, session_id="f1")
    assert state.current_status == "failed"
    assert state.task_statuses["a"] is TaskStatus.FAILED
    assert state.task_statuses["b"] is TaskStatus.SKIPPED
    assert state.task_results["b"].success is False


async def test_retry_then_fail_within_max_retries():
    calls = {"n": 0}

    class _RetryOnceFailingExecutor(_FailingExecutor):
        async def execute(self, task, context):
            calls["n"] += 1
            return TaskResult(task_id=task.id, success=False, error="boom")

    registry = ExecutorRegistry()
    registry.register(_RetryOnceFailingExecutor(), {"research"})
    runtime = GraphRuntime(registry)
    graph = TaskGraph(
        graph_id="retry",
        tasks=[
            Task(
                id="a",
                goal="a",
                required_capabilities={"research"},
                max_retries=2,
            )
        ],
    )
    state = await runtime.run(graph, session_id="r1")
    assert state.current_status == "failed"
    assert state.task_statuses["a"] is TaskStatus.FAILED
    # max_retries=2 → 最多 3 次尝试后失败
    assert calls["n"] == 3
    assert len(state.task_attempts["a"]) == 3


async def test_global_worker_pool_bounds_cross_run_concurrency_and_preserves_run_state():
    """Independent conversations share finite slots instead of unbounded tasks."""
    active = 0
    peak = 0

    class SlowExecutor:
        executor_id = "slow"

        async def execute(self, task, context):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.03)
                return TaskResult(task_id=task.id, success=True, output=context.state.session_id)
            finally:
                active -= 1

    registry = ExecutorRegistry()
    registry.register(SlowExecutor(), {"research"})
    pool = GlobalWorkerPool.homogeneous(size=2, capabilities=registry.capabilities, max_queue_size=8)
    runtime = GraphRuntime(registry, worker_pool=pool)
    graph = lambda graph_id: TaskGraph(graph_id=graph_id, tasks=[
        Task(id="a", goal="a", required_capabilities={"research"}),
        Task(id="b", goal="b", required_capabilities={"research"}),
    ])
    try:
        left, right = await asyncio.gather(
            runtime.run(graph("left"), session_id="run-left"),
            runtime.run(graph("right"), session_id="run-right"),
        )
    finally:
        await pool.close()

    assert peak == 2
    assert left.current_status == right.current_status == "succeeded"
    assert {result.output for result in left.task_results.values()} == {"run-left"}
    assert {result.output for result in right.task_results.values()} == {"run-right"}
