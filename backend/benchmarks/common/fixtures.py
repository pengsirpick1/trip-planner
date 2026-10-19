from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.domain import Task, TaskGraph, TaskResult
from app.tools.base import ToolResult, ToolSpec


class DeterministicExecutor:
    """External-work simulator used with the real GraphRuntime and TaskGraph."""

    executor_id = "benchmark-deterministic-executor"

    def __init__(self, delays: dict[str, float] | None = None):
        self.delays = delays or {}
        self.calls: list[str] = []

    async def execute(self, task: Task, _context: Any) -> TaskResult:
        self.calls.append(task.id)
        await asyncio.sleep(self.delays.get(task.id, 0.0))
        return TaskResult(
            task_id=task.id,
            success=True,
            output=f"evidence:{task.id}:{task.goal}",
            executor_id=self.executor_id,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            metadata={"tool_calls": 0 if task.id == "synthesis" else 1, "sources": [f"fake://{task.id}"]},
        )


class CrashOnceExecutor(DeterministicExecutor):
    """Cancels one task once, leaving the Runtime's task-boundary checkpoint intact."""

    def __init__(self, crash_task_id: str, delays: dict[str, float] | None = None):
        super().__init__(delays)
        self.crash_task_id = crash_task_id
        self.crashed = False

    async def execute(self, task: Task, context: Any) -> TaskResult:
        if task.id == self.crash_task_id and not self.crashed:
            self.crashed = True
            raise asyncio.CancelledError(f"deterministic crash at {task.id}")
        return await super().execute(task, context)


class ScriptedTool:
    """Deterministic fake of an external ToolRegistry boundary."""

    def __init__(self, schedule: list[str], *, timeout_seconds: float = 0.02, max_retries: int = 2):
        self.schedule = list(schedule)
        self.calls = 0
        self.spec = ToolSpec(
            name="fault_tool",
            description="offline deterministic fault injection tool",
            capabilities={"fault_research"},
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

    async def execute(self, _arguments, context):
        outcome = self.schedule[min(self.calls, len(self.schedule) - 1)]
        self.calls += 1
        if outcome == "timeout":
            await asyncio.sleep(self.spec.timeout_seconds * 3)
        if outcome == "success":
            return ToolResult(
                tool_call_id=context["tool_call_id"], tool_name="fault_tool", success=True,
                data={"status": "ok", "fact": "deterministic"}, source_references=["fake://fault-tool"],
            )
        if outcome in {"transient_error", "rate_limit_like_error"}:
            category = "rate_limit" if outcome == "rate_limit_like_error" else "network"
            return ToolResult(
                tool_call_id=context["tool_call_id"], tool_name="fault_tool", success=False,
                error=outcome, metadata={"diagnostic": {"category": category}},
            )
        if outcome == "malformed_result":
            # The production registry has no output-schema validator today.  A
            # syntactically valid ToolResult whose payload is unusable therefore
            # reaches ReAct as a successful call; the benchmark records that
            # limitation instead of inventing a benchmark-only validator.
            return ToolResult(
                tool_call_id=context["tool_call_id"], tool_name="fault_tool", success=True,
                data={"unexpected_shape": True}, metadata={"diagnostic": {"category": "malformed"}},
            )
        return ToolResult(
            tool_call_id=context["tool_call_id"], tool_name="fault_tool", success=False,
            error="PERMANENT_ERROR", metadata={"diagnostic": {"category": "permanent"}},
        )


class OneToolThenFinalLLM:
    """No-network ReAct decision source with explicit fake invocation counting."""

    def __init__(self):
        self.calls = 0

    async def decide(self, task, observations, **_kwargs):
        self.calls += 1
        if not observations:
            call = {
                "id": f"{task.id}-tool-0",
                "type": "function",
                "function": {"name": "fault_tool", "arguments": "{}"},
            }
            return {"tool_calls": [call], "assistant_message": {"role": "assistant", "tool_calls": [call]}, "usage": {}}
        return {"final": "offline fault handling complete", "usage": {}}


def parallel_graph(width: int) -> TaskGraph:
    tasks = [
        Task(id=f"research_{index}", goal=f"research {index}", required_capabilities={"benchmark_io"})
        for index in range(width)
    ]
    tasks.append(Task(id="synthesis", goal="combine research", dependencies=[task.id for task in tasks], required_capabilities={"benchmark_synthesis"}))
    return TaskGraph(graph_id=f"parallel-{width}", tasks=tasks)


def replanning_graph() -> TaskGraph:
    return TaskGraph(
        graph_id="replanning-v1",
        tasks=[
            Task(id="transport", goal="research transport", required_capabilities={"geospatial_reasoning"}),
            Task(id="hotel", goal="research accommodation", required_capabilities={"accommodation_research"}),
            Task(id="attractions", goal="research attractions", required_capabilities={"place_research"}),
            Task(id="weather", goal="research weather", required_capabilities={"weather_research"}),
            Task(id="restaurant", goal="research restaurants", dependencies=["attractions"], required_capabilities={"restaurant_research"}),
            Task(id="synthesis", goal="synthesize itinerary", dependencies=["transport", "hotel", "attractions", "weather", "restaurant"], required_capabilities={"itinerary_synthesis"}),
        ],
    )


def recovery_graph() -> TaskGraph:
    previous = None
    tasks = []
    for task_id in ("a", "b", "c", "d", "e", "f"):
        tasks.append(Task(id=task_id, goal=f"recover {task_id}", dependencies=[previous] if previous else [], required_capabilities={"benchmark_io"}))
        previous = task_id
    return TaskGraph(graph_id="checkpoint-recovery", tasks=tasks)
