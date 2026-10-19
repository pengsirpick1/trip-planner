"""复杂度信号归一化（迁自 travel_agent + Phase B 严格化修复，审计 G4）。

修复点：字符串布尔不再用 bool("false") 误判为 True；数字解析失败抛明确错误，
由上层转成类型化失败，绝不静默产出错误信号。
"""

from typing import Any

from pydantic import BaseModel

from ..domain import ComplexitySignals

_TRUE_TOKENS = {"true", "1", "yes", "y", "是"}
_FALSE_TOKENS = {"false", "0", "no", "n", "否", ""}


class SemanticComplexitySignals(BaseModel):
    estimated_subgoals: Any = 1
    requires_external_information: Any = False
    requires_multiple_information_domains: Any = False
    requires_comparison: Any = False
    requires_constraint_optimization: Any = False
    requires_temporal_reasoning: Any = False
    requires_geographic_reasoning: Any = False
    requires_cross_result_synthesis: Any = False
    dependency_depth_estimate: Any = 0
    uncertainty: Any = 0
    confidence: Any = 1


def _coerce_bool(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
        raise ValueError(f"invalid boolean for {key}: {value!r}")
    raise ValueError(f"invalid boolean for {key}: {value!r}")


def _coerce_int(key: str, value: Any, minimum: int) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        raise ValueError(f"invalid integer for {key}: {value!r}") from None


def _coerce_float(key: str, value: Any, low: float, high: float) -> float:
    try:
        return min(high, max(low, float(value)))
    except (TypeError, ValueError):
        raise ValueError(f"invalid float for {key}: {value!r}") from None


class ComplexitySignalsNormalizer:
    def normalize(self, signals: SemanticComplexitySignals) -> ComplexitySignals:
        data = signals.model_dump()
        estimated_subgoals = _coerce_int("estimated_subgoals", data["estimated_subgoals"], minimum=1)
        dependency_depth = _coerce_int("dependency_depth_estimate", data["dependency_depth_estimate"], minimum=0)
        if estimated_subgoals == 1:
            dependency_depth = 0
        normalized = {
            "estimated_subgoals": estimated_subgoals,
            "dependency_depth_estimate": dependency_depth,
            "uncertainty": _coerce_float("uncertainty", data["uncertainty"], 0.0, 1.0),
            "confidence": _coerce_float("confidence", data["confidence"], 0.0, 1.0),
        }
        for key in (
            "requires_external_information",
            "requires_multiple_information_domains",
            "requires_comparison",
            "requires_constraint_optimization",
            "requires_temporal_reasoning",
            "requires_geographic_reasoning",
            "requires_cross_result_synthesis",
        ):
            normalized[key] = _coerce_bool(key, data[key])
        return ComplexitySignals(**normalized)
