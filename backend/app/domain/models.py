"""领域契约（迁自 travel_agent/agent/domain/models.py + Phase B 扩展）。

与 API DTO（app/models/schemas.py 的 TripPlan 等展示模型）职责分离：
本包表达“执行域”概念（意图/信号/任务图/执行状态/结果），不是旅行展示输出契约。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    INVALIDATED = "invalidated"
    WAITING_FOR_USER = "waiting_for_user"


class ConditionalEdge(BaseModel):
    """A deliberately small control-flow edge for graph control nodes."""
    source_task_id: str
    on: str
    target_task_id: str | None = None


class UserIntent(BaseModel):
    """归一化后的用户意图。

    planning_intent（Phase B 新增，修复 DIRECT 被日期/目的地阻断）：
    - True  : 请求产出完整行程规划；
    - False : 信息/决策类问题（无需 destination/travel_time 即算 READY）；
    - None  : 未知，保守按需要澄清处理。
    """

    session_id: str
    task_type: str | None = None
    planning_intent: bool | None = None
    raw_query: str
    origin: str | None = None
    destination: str | None = None
    destination_candidates: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    duration_days: int | None = None
    budget: float | None = None
    traveler_count: int | None = None
    preferences: list[str] = Field(default_factory=list)
    must_have: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    """声明式计划节点；运行时生命周期保存在 GraphExecutionState。"""

    id: str
    goal: str
    task_type: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    required_capabilities: set[str] = Field(default_factory=set)
    expected_output: str | None = None
    completion_criteria: str | None = None
    priority: int = 0
    max_retries: int = 0
    assigned_agent: str | None = None
    local_context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskGraph(BaseModel):
    graph_id: str
    version: int = 1
    tasks: list[Task]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
    conditional_edges: list[ConditionalEdge] = Field(default_factory=list)

    def task(self, task_id: str) -> Task:
        return next(t for t in self.tasks if t.id == task_id)

    def downstream_task_ids(self, task_id: str) -> set[str]:
        seen: set[str] = set()
        pending = [task_id]
        while pending:
            parent = pending.pop()
            children = [task.id for task in self.tasks if parent in task.dependencies and task.id not in seen]
            seen.update(children)
            pending.extend(children)
        return seen

    def validate_graph(self) -> None:
        ids = {t.id for t in self.tasks}
        if len(ids) != len(self.tasks):
            raise ValueError("duplicate task id")
        if any(d not in ids for t in self.tasks for d in t.dependencies):
            raise ValueError("missing dependency")
        if any(edge.source_task_id not in ids or (edge.target_task_id and edge.target_task_id not in ids)
               for edge in self.conditional_edges):
            raise ValueError("conditional edge references missing task")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("task graph contains a cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dep in self.task(task_id).dependencies:
                visit(dep)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in ids:
            visit(task_id)


class TaskResult(BaseModel):
    task_id: str
    success: bool
    output: Any = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    executor_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphExecutionState(BaseModel):
    session_id: str
    graph: TaskGraph
    task_statuses: dict[str, TaskStatus] = Field(default_factory=dict)
    task_results: dict[str, TaskResult] = Field(default_factory=dict)
    task_attempts: dict[str, list[TaskResult]] = Field(default_factory=dict)
    current_status: str = "pending"
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
    agent_local_states: dict[str, dict[str, Any]] = Field(default_factory=dict)


class EvaluationResult(BaseModel):
    passed: bool
    score: float | None = None
    issues: list[str] = Field(default_factory=list)
    affected_task_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
