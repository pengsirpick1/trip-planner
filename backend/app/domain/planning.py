"""规划领域契约（迁自 travel_agent/agent/domain/planning.py）。"""

from typing import Any

from pydantic import BaseModel, Field

from .models import TaskGraph, UserIntent


class PlanTaskDraft(BaseModel):
    id: str
    goal: str = Field(min_length=1)
    task_type: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    required_capabilities: set[str] = Field(min_length=1)
    expected_output: str = Field(min_length=1)
    completion_criteria: str = Field(min_length=1)
    priority: int = 0
    assigned_agent: str | None = None


class PlanDraft(BaseModel):
    tasks: list[PlanTaskDraft] = Field(min_length=1)
    rationale_summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    requires_human_approval: bool = False


class PlanningContext(BaseModel):
    session_id: str
    intent: UserIntent
    assumptions: list[str] = Field(default_factory=list)
    available_capabilities: set[str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanningResult(BaseModel):
    success: bool
    graph: TaskGraph | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
