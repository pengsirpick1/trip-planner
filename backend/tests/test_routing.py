"""Phase B：路由（normalize 严格化 + 确定性策略）。"""

from __future__ import annotations

import pytest

from app.domain import ComplexitySignals, ExecutionProfile, ExecutionRoute, UserIntent
from app.routing.normalize import (
    ComplexitySignalsNormalizer,
    SemanticComplexitySignals,
)
from app.routing.policy import ComplexityRouter, profile_for_trip_scope


# ---- canonical normalize 严格化（审计 G4） ----


def test_string_false_is_false_not_true():
    signals = SemanticComplexitySignals(requires_comparison="false")
    normalized = ComplexitySignalsNormalizer().normalize(signals)
    assert normalized.requires_comparison is False


def test_bool_tokens_variants():
    normalizer = ComplexitySignalsNormalizer()
    for token in ("true", "True", "是", "yes", "1"):
        assert normalizer.normalize(SemanticComplexitySignals(requires_comparison=token)).requires_comparison is True
    for token in ("false", "False", "否", "no", "0"):
        assert normalizer.normalize(SemanticComplexitySignals(requires_comparison=token)).requires_comparison is False


def test_invalid_bool_raises():
    with pytest.raises(ValueError, match="invalid boolean"):
        ComplexitySignalsNormalizer().normalize(
            SemanticComplexitySignals(requires_comparison="maybe")
        )


def test_invalid_int_raises_instead_of_silent_zero():
    with pytest.raises(ValueError, match="invalid integer"):
        ComplexitySignalsNormalizer().normalize(
            SemanticComplexitySignals(estimated_subgoals="many")
        )


def test_numeric_strings_coerced():
    normalized = ComplexitySignalsNormalizer().normalize(
        SemanticComplexitySignals(
            estimated_subgoals="2",
            dependency_depth_estimate="1",
            confidence="0.8",
            uncertainty="0.1",
        )
    )
    assert normalized.estimated_subgoals == 2
    assert normalized.dependency_depth_estimate == 1
    assert normalized.confidence == 0.8
    assert normalized.uncertainty == 0.1


# ---- 确定性路由 ----


def _signals(**overrides) -> ComplexitySignals:
    base = dict(
        estimated_subgoals=1,
        requires_external_information=False,
        requires_multiple_information_domains=False,
        requires_comparison=False,
        requires_constraint_optimization=False,
        requires_temporal_reasoning=False,
        requires_geographic_reasoning=False,
        requires_cross_result_synthesis=False,
        dependency_depth_estimate=0,
        uncertainty=0.0,
        confidence=0.9,
    )
    base.update(overrides)
    return ComplexitySignals(**base)


def test_single_factual_goes_direct():
    decision = ComplexityRouter().route(_signals())
    assert decision.route is ExecutionRoute.DIRECT
    assert decision.signals.estimated_subgoals == 1
    assert decision.execution_profile is ExecutionProfile.DIRECT


def test_external_lookup_single_subgoal_goes_simple():
    decision = ComplexityRouter().route(
        _signals(requires_external_information=True)
    )
    assert decision.route is ExecutionRoute.SIMPLE
    assert decision.execution_profile is ExecutionProfile.LIGHT


def test_three_subgoals_goes_complex():
    decision = ComplexityRouter().route(
        _signals(
            estimated_subgoals=3,
            requires_multiple_information_domains=True,
            requires_external_information=True,
        )
    )
    assert decision.route is ExecutionRoute.COMPLEX
    assert decision.execution_profile is ExecutionProfile.STANDARD


def test_deep_profile_has_hard_worker_cap():
    decision = ComplexityRouter().route(
        _signals(estimated_subgoals=6, dependency_depth_estimate=3,
                 requires_external_information=True, requires_multiple_information_domains=True)
    )
    assert decision.execution_profile is ExecutionProfile.DEEP
    assert decision.execution_profile.max_research_agents == 5


def test_one_day_scope_corrects_overestimated_deep_signal_to_light():
    intent = UserIntent(session_id="one-day", raw_query="北京一日游", planning_intent=True,
                        destination="北京", duration_days=1, must_have=["故宫"])
    deep_decision = ComplexityRouter().route(
        _signals(estimated_subgoals=6, dependency_depth_estimate=3,
                 requires_external_information=True, requires_multiple_information_domains=True)
    )
    assert deep_decision.execution_profile is ExecutionProfile.DEEP
    assert profile_for_trip_scope(deep_decision, intent) is ExecutionProfile.LIGHT


def test_router_never_reads_raw_query_or_selects_tools():
    """Router 只消费信号：构造不含 raw_query 的输入即可通过类型检查。"""
    router = ComplexityRouter()
    decision = router.route(_signals())
    assert isinstance(decision.route, ExecutionRoute)
    assert not any("tool" in reason.lower() for reason in decision.reasons)
