"""计划质量评估领域模型（Phase E，审计 §11）。

与旧 EvaluationResult（仅 passed/score）不同：
- 状态三分：PASS / FAIL / INDETERMINATE（无法验证≠通过）
- issue 携带 code/severity/evidence/plan_field_paths/affected_task_ids/repair_scope
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class EvaluationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INDETERMINATE = "indeterminate"


class IssueSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"


class RepairScope(str, Enum):
    TASK = "task"
    SYNTHESIS = "synthesis"
    CLARIFICATION = "clarification"


class EvaluationIssue(BaseModel):
    code: str
    severity: IssueSeverity
    message: str
    evidence: list[str] = Field(default_factory=list)
    plan_field_paths: list[str] = Field(default_factory=list)
    affected_task_ids: list[str] = Field(default_factory=list)
    repair_scope: RepairScope = RepairScope.TASK


class PlanEvaluationResult(BaseModel):
    status: EvaluationStatus
    issues: list[EvaluationIssue] = Field(default_factory=list)
    evaluator_version: str = "deterministic-v1"
    evaluated_plan_revision: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
