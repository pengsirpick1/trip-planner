"""局部重规划（Phase F）请求模型。

触发源两类：
- 执行失败（EXECUTION_FAILED）：受影响任务 = 失败任务 ∪ 其下游；
- 质量评估 FAIL 且 issue 显式带 affected_task_ids（如 BUDGET/MUST_HAVE 等确定性映射）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .models import TaskGraph


class ReplanRequest(BaseModel):
    reason: str
    affected_task_ids: list[str] = Field(default_factory=list)
    repair_hints: dict[str, list[str]] = Field(default_factory=dict)


class ReplanDecision(BaseModel):
    proceed: bool
    target_task_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class ReplanningOutcome(BaseModel):
    success: bool
    graph: TaskGraph | None = None
    errors: list[str] = Field(default_factory=list)
