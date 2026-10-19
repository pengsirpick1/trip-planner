"""Phase F：执行预算护栏 + 局部重规划 vN+1。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.replanning_service import ReplanningService
from app.domain import (
    GraphExecutionState,
    Task,
    TaskGraph,
    TaskResult,
    TaskStatus,
    UserIntent,
)
from app.domain.budget import BudgetTracker, ExecutionBudget
from app.executors.react import AgentProfile, ReActExecutor
from app.runtime.graph_runtime import GraphRuntime
from app.runtime.registry import ExecutorRegistry
from app.tools import ToolRegistry


# ---------- 预算 ----------


def test_budget_tracker_thresholds():
    budget = ExecutionBudget(max_llm_decisions=2, max_tool_calls=5, max_elapsed_seconds=600)
    tracker = BudgetTracker(budget)
    assert tracker.register_llm_decision() is False
    assert tracker.register_llm_decision() is True
    assert tracker.exceeded_reason and "llm_decisions" in tracker.exceeded_reason


class _LoopToolLLM:
    """永远调用工具、不给 final，用于触发预算上限。"""

    def __init__(self):
        self._n = 0

    async def generate_structured(self, messages, response_model):
        raise NotImplementedError

    async def decide(self, task, observations, *, messages=None, tools=(), dependency_results=None):
        self._n += 1
        import json

        call_id = f"{task.id}-t{self._n}"
        call = {
            "id": call_id,
            "type": "function",
            "function": {
                "name": "weather",
                "arguments": json.dumps({"location": f"City{self._n}"}),
            },
        }
        return {
            "tool_calls": [call],
            "assistant_message": {"role": "assistant", "content": None, "tool_calls": [call]},
            "usage": {},
        }


class _FakeWeatherTool2:
    def __init__(self):
        from app.tools.base import ToolSpec

        self.spec = ToolSpec(name="weather", description="x", capabilities={"weather_research"})

    async def execute(self, arguments, context):
        from app.tools.base import ToolResult

        return ToolResult(
            tool_call_id=context["tool_call_id"],
            tool_name="weather",
            success=True,
            data={"location": getattr(arguments, "location", None), "ok": True},
        )


async def test_react_budget_exceeded_aborts_task():
    tools = ToolRegistry()
    tools.register(_FakeWeatherTool2())
    registry = ExecutorRegistry()
    registry.register(
        ReActExecutor(
            "react-budget",
            _LoopToolLLM(),
            tools,
            AgentProfile("a", {"weather_research"}, {"weather"}, max_steps=50),
        ),
        {"weather_research"},
    )
    runtime = GraphRuntime(registry)
    graph = TaskGraph(
        graph_id="budget-v1",
        tasks=[Task(id="t1", goal="loop weather", required_capabilities={"weather_research"})],
    )
    state = await runtime.run(
        graph,
        session_id="b1",
        budget=ExecutionBudget(max_llm_decisions=2, max_tool_calls=60, max_elapsed_seconds=600),
    )
    assert state.current_status == "failed"
    assert state.task_results["t1"].success is False
    assert "EXECUTION_BUDGET_EXCEEDED" in (state.task_results["t1"].error or "")
    assert state.metadata.get("budget_exceeded")
    assert state.metadata["budget"]["llm_decisions"] >= 2


# ---------- 局部重规划 ----------


class _CountingExecutor:
    executor_id = "count"

    def __init__(self):
        self.executed: list[str] = []

    async def execute(self, task, context):
        self.executed.append(task.id)
        hint = "上一版本" in (task.goal or "")
        ok = hint or task.id != "broken"
        return TaskResult(
            task_id=task.id,
            success=ok,
            output=f"{task.id} done" if ok else None,
            error=None if ok else "boom",
            finished_at=datetime.now(UTC),
        )


def _diamond_graph(version=1):
    return TaskGraph(
        graph_id=f"repair-session-v{version}",
        version=version,
        tasks=[
            Task(id="a", goal="a", required_capabilities={"r"}),
            Task(id="broken", goal="broken", dependencies=["a"], required_capabilities={"r"}),
            Task(id="c", goal="c", dependencies=["broken"], required_capabilities={"r"}),
        ],
    )


def _prev_state(graph) -> GraphExecutionState:
    state = GraphExecutionState(
        session_id="repair-session",
        graph=graph,
        task_statuses={"a": TaskStatus.SUCCEEDED, "broken": TaskStatus.FAILED, "c": TaskStatus.SKIPPED},
        current_status="failed",
        task_results={
            "a": TaskResult(task_id="a", success=True, output="a ok", finished_at=datetime.now(UTC)),
            "broken": TaskResult(task_id="broken", success=False, error="boom"),
            "c": TaskResult(task_id="c", success=False, error="skipped because a dependency did not succeed"),
        },
    )
    return state


async def test_replan_scope_and_merge_reruns_only_affected_downstream():
    service = ReplanningService()
    graph_v1 = _diamond_graph(1)
    prev_state = _prev_state(graph_v1)
    scope = service.compute_repair_scope(graph_v1, ["broken"])
    assert scope == ["broken", "c"]  # 只重跑失败+下游，a 不重跑

    from app.domain.planning import PlanningContext

    context = PlanningContext(
        session_id="repair-session",
        intent=UserIntent(session_id="repair-session", raw_query="x", planning_intent=True),
        assumptions=[],
        available_capabilities={"r"},
    )
    outcome = service.build_repair_graph(graph_v1, scope, {"broken": ["boom"]}, context)
    assert outcome.success
    graph_v2 = outcome.graph
    assert graph_v2.version == 2
    assert graph_v2.graph_id == "repair-session-v2"

    merged = service.merge_state(prev_state, graph_v2, scope)
    assert merged.task_statuses["a"] is TaskStatus.SUCCEEDED
    assert merged.task_statuses["broken"] is TaskStatus.PENDING
    assert merged.task_results["a"].metadata["graph_version"] == 1
    assert "replans" in merged.metadata

    executor = _CountingExecutor()
    registry = ExecutorRegistry()
    registry.register(executor, {"r"})
    runtime = GraphRuntime(registry)
    final = await runtime.run(graph_v2, session_id="repair-session", initial_state=merged)
    assert final.current_status == "succeeded"
    # v2 只执行了 broken（c 被跳过依赖? broken succeed→c ready run; c ok）
    assert executor.executed == ["broken", "c"]
    assert "a" not in executor.executed


# ---------- 处理器端到端：v1 失败 → 自动修复 v2 ----------


class _FlipExecutor:
    """v1 的 broken 必失败；带修复提示(上一版本)后成功。"""

    executor_id = "flip"

    def __init__(self):
        self.executed: list[str] = []

    async def execute(self, task, context):
        self.executed.append(task.id)
        if "上一版本" in (task.goal or ""):
            return TaskResult(task_id=task.id, success=True, output=f"{task.id} fixed", finished_at=datetime.now(UTC))
        if task.id == "broken":
            return TaskResult(task_id=task.id, success=False, error="boom v1", finished_at=datetime.now(UTC))
        return TaskResult(task_id=task.id, success=True, output=f"{task.id} ok", finished_at=datetime.now(UTC))


async def test_processor_auto_repair_on_execution_failure():
    from app.application.intent_service import IntentService
    from app.application.planning_service import PlanningService
    from app.application.routing_service import RoutingService
    from app.application.travel_processor import TravelPipelineProcessor
    from app.application.travel_service import RunRecord
    from app.intent.llm_parser import LLMIntentParser
    from app.planning.builder import PlanBuilder
    from app.planning.llm_structured import LLMStructuredPlanner
    from app.routing.llm_analyzer import LLMComplexityAnalyzer
    from app.routing.policy import ComplexityRouter
    from app.synthesis import TripPlanSynthesizer
    from tests.fakes import CannedStructuredLLM

    llm = CannedStructuredLLM(
        structured_map={
            "IntentUpdate": {
                "extracted_fields": {
                    "task_type": "travel",
                    "planning_intent": True,
                    "destination": "北京",
                    "start_date": "2026-10-01",
                    "end_date": "2026-10-01",
                    "duration_days": 1,
                }
            },
            "SemanticComplexitySignals": {
                "estimated_subgoals": 3,
                "requires_multiple_information_domains": True,
                "requires_cross_result_synthesis": True,
                "confidence": 0.9,
            },
            "PlanDraft": {
                "tasks": [
                    {
                        "id": "a",
                        "goal": "research a",
                        "required_capabilities": ["r"],
                        "dependencies": [],
                        "expected_output": "x",
                        "completion_criteria": "x",
                        "priority": 0,
                    },
                    {
                        "id": "broken",
                        "goal": "research b",
                        "required_capabilities": ["r"],
                        "dependencies": ["a"],
                        "expected_output": "x",
                        "completion_criteria": "x",
                        "priority": 1,
                    },
                ]
            },
        }
    )
    registry = ExecutorRegistry()
    executor = _FlipExecutor()
    registry.register(executor, {"r", "itinerary_synthesis"})
    runtime = GraphRuntime(registry)
    processor = TravelPipelineProcessor(
        intent_service=IntentService(LLMIntentParser(llm)),
        routing_service=RoutingService(LLMComplexityAnalyzer(llm), ComplexityRouter()),
        planning_service=PlanningService(LLMStructuredPlanner(llm), PlanBuilder()),
        runtime=runtime,
        llm=llm,
        capabilities={"r", "itinerary_synthesis"},
        replanning_service=ReplanningService(PlanBuilder()),
        execution_budget=ExecutionBudget(max_llm_decisions=1000, max_tool_calls=1000),
        max_repair_attempts=1,
        synthesizer=None,
    )
    outcome = await processor.process(
        "请规划北京 10月1日 一日游", RunRecord(run_id="r1", session_id="sess-pf")
    )
    # v1 broken 失败 → 自动重规划 v2 修复；因无合成器停在 typed SYNTHESIS_NOT_READY（而非 EXECUTION_FAILED）
    names = {event["event"] for event in outcome.events}
    assert "replan_triggered" in names
    assert "replan_published" in names
    published = next(e for e in outcome.events if e["event"] == "replan_published")
    assert published["payload"]["previous_version"] == 1
    assert published["payload"]["next_version"] == 2
    assert outcome.kind.value == "failed"
    assert outcome.error_code == "TRIP_PLAN_SYNTHESIS_NOT_READY"
    # a 只执行一次（v1），v2 只重跑 broken
    assert executor.executed.count("a") == 1
    assert executor.executed.count("broken") == 2
