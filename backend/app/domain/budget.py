"""执行预算（Phase F）：单次 run 的资源护栏。

防止空转/失控烧钱：LLM 决策次数、工具调用次数、墙钟耗时三档上限；
任一超限即视为 exceeded，由 runtime 中止并给出类型化结果，绝不无限重试。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field


class ExecutionBudget(BaseModel):
    max_llm_decisions: int = Field(default=40, ge=1, description="单次 run 的 LLM 决策上限")
    max_tool_calls: int = Field(default=60, ge=1, description="单次 run 的工具调用上限")
    max_elapsed_seconds: float = Field(default=900.0, gt=0, description="墙钟上限（秒）")


@dataclass
class BudgetTracker:
    budget: ExecutionBudget
    started_at: float = field(default_factory=time.monotonic)
    llm_decisions: int = 0
    tool_calls: int = 0

    def register_llm_decision(self) -> bool:
        """记录一次模型决策；返回是否因此超限。"""
        self.llm_decisions += 1
        return self.exceeded

    def register_tool_call(self) -> bool:
        self.tool_calls += 1
        return self.exceeded

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def exceeded(self) -> bool:
        return (
            self.llm_decisions >= self.budget.max_llm_decisions
            or self.tool_calls >= self.budget.max_tool_calls
            or self.elapsed >= self.budget.max_elapsed_seconds
        )

    @property
    def exceeded_reason(self) -> Optional[str]:
        if self.llm_decisions >= self.budget.max_llm_decisions:
            return f"llm_decisions={self.llm_decisions} >= {self.budget.max_llm_decisions}"
        if self.tool_calls >= self.budget.max_tool_calls:
            return f"tool_calls={self.tool_calls} >= {self.budget.max_tool_calls}"
        if self.elapsed >= self.budget.max_elapsed_seconds:
            return f"elapsed={self.elapsed:.0f}s >= {self.budget.max_elapsed_seconds:.0f}s"
        return None

    def snapshot(self) -> dict:
        return {
            "llm_decisions": self.llm_decisions,
            "tool_calls": self.tool_calls,
            "elapsed_seconds": round(self.elapsed, 2),
            "exceeded": self.exceeded,
            "reason": self.exceeded_reason,
        }


DEFAULT_EXECUTION_BUDGET = ExecutionBudget()
