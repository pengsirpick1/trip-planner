"""意图领域契约（迁自 travel_agent/agent/domain/intent.py）。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .models import UserIntent


class IntentTurnStatus(str, Enum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    OUT_OF_DOMAIN = "out_of_domain"
    CONFLICT = "conflict"


class IntentUpdate(BaseModel):
    """Parser 提出的字段级更新；绝不直接修改 UserIntent。"""

    model_config = {"extra": "forbid"}
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    ambiguities: list[str] = Field(default_factory=list)
    assumptions: Any = Field(default_factory=list)
    correction_fields: set[str] = Field(default_factory=set)
    # User-written alias -> canonical entity suitable for planning/tool queries.
    # Kept separate from facts so the product can still show the original wording.
    entity_resolutions: dict[str, str] = Field(default_factory=dict)


class RequirementAnalysis(BaseModel):
    ready: bool
    missing_fields: list[str] = Field(default_factory=list)
    blocking_fields: list[str] = Field(default_factory=list)
    inferred_fields: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    clarification_needed: bool = False
    conflicts: list[str] = Field(default_factory=list)


class ClarificationQuestion(BaseModel):
    id: str
    field: str
    question: str
    options: list[str] = Field(default_factory=list)
    allow_free_text: bool = True
    priority: int
    reason: str


class ClarificationRecord(BaseModel):
    questions: list[ClarificationQuestion]
    user_answer: str | None = None


class ConversationState(BaseModel):
    intent: UserIntent
    clarification_history: list[ClarificationRecord] = Field(default_factory=list)
    # The last accepted plan is conversation memory, rather than an implicit
    # one-shot response.  It is JSON on purpose so the persistence boundary
    # remains independent from the API DTO version.
    active_plan: dict[str, Any] | None = None
    plan_revision: int = 0


class IntentTurnResult(BaseModel):
    intent: UserIntent
    status: IntentTurnStatus
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    analysis: RequirementAnalysis
    # 仅保留本轮解析出的显式字段，供长期记忆写入；不能用合并后的 intent，
    # 否则会把旧记忆再次误认为用户刚刚确认。
    explicit_fields: dict[str, Any] = Field(default_factory=dict)
