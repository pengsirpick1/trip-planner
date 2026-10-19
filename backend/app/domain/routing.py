"""路由领域契约（迁自 travel_agent/agent/domain/routing.py）。"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ExecutionRoute(str, Enum):
    DIRECT = "direct"
    SIMPLE = "simple"
    COMPLEX = "complex"


class ExecutionProfile(str, Enum):
    """Research fan-out and per-run concurrency budgets, not Agent instances."""

    DIRECT = "direct"
    LIGHT = "light"
    STANDARD = "standard"
    DEEP = "deep"

    @property
    def max_research_agents(self) -> int:
        """Legacy name kept for API compatibility; this is a task fan-out budget."""
        return {self.DIRECT: 0, self.LIGHT: 2, self.STANDARD: 4, self.DEEP: 5}[self]

    @property
    def max_concurrent_workers(self) -> int:
        """Maximum local worker slots one Run may hold at the same time."""
        return {self.DIRECT: 1, self.LIGHT: 2, self.STANDARD: 3, self.DEEP: 4}[self]


class ComplexitySignals(BaseModel):
    estimated_subgoals: int = Field(ge=1)
    requires_external_information: bool = False
    requires_multiple_information_domains: bool = False
    requires_comparison: bool = False
    requires_constraint_optimization: bool = False
    requires_temporal_reasoning: bool = False
    requires_geographic_reasoning: bool = False
    requires_cross_result_synthesis: bool = False
    dependency_depth_estimate: int = Field(default=0, ge=0)
    uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_structure(self):
        if self.dependency_depth_estimate and self.estimated_subgoals < 2:
            raise ValueError("dependency depth requires at least two subgoals")
        return self


class RouteDecision(BaseModel):
    route: ExecutionRoute
    complexity_score: int = Field(ge=0)
    reasons: list[str] = Field(min_length=1)
    signals: ComplexitySignals
    confidence: float = Field(ge=0.0, le=1.0)
    execution_profile: ExecutionProfile = ExecutionProfile.DIRECT
    metadata: dict[str, Any] = Field(default_factory=dict)
