"""Phase E：确定性评估器（只读、FAIL/INDETERMINATE/PASS）。"""

from __future__ import annotations

import pytest

from app.domain import UserIntent
from app.domain.evaluation import EvaluationStatus, IssueSeverity
from app.evaluation.deterministic import (
    BUDGET_VIOLATION,
    DeterministicTripEvaluator,
    INCOMPLETE_PLAN,
    MUST_HAVE_MISSING,
    SCHEDULE_CONFLICT,
    UNSUPPORTED_CLAIM,
)
from app.models import schemas


def _intent(**overrides) -> UserIntent:
    base = dict(
        session_id="s",
        raw_query="plan",
        planning_intent=True,
        destination="北京",
        start_date="2026-10-01",
        end_date="2026-10-01",
        duration_days=1,
        budget=500,
        must_have=[],
    )
    base.update(overrides)
    return UserIntent(**base)


def _day(attractions=None, day_index=0, date: str = "2026-10-01") -> schemas.DayPlan:
    attractions = attractions or [
        schemas.Attraction(
            name="故宫博物院",
            address="景山前街4号",
            description="明清皇宫",
            evidence_ids=["place@1#tool0"],
        )
    ]
    return schemas.DayPlan(
        date=date,
        day_index=day_index,
        description="故宫一日",
        transportation="地铁",
        accommodation="无",
        attractions=attractions,
        meals=[],
    )


def _plan(**overrides) -> schemas.TripPlan:
    base = dict(
        city="北京",
        start_date="2026-10-01",
        end_date="2026-10-01",
        days=[_day()],
        overall_suggestions="早到，提前预约。",
        budget=schemas.Budget(total=132, total_attractions=72, total_meals=40,
                              total_hotels=0, total_transportation=20),
        assumptions=[],
        plan_revision=1,
        graph_version=1,
    )
    base.update(overrides)
    return schemas.TripPlan(**base)


async def test_evaluator_pass():
    result = await DeterministicTripEvaluator().evaluate(_intent(), _plan())
    assert result.status is EvaluationStatus.PASS
    assert result.evaluated_plan_revision == 1


async def test_evaluator_budget_violation_is_fail():
    plan = _plan(budget=schemas.Budget(total=900, total_attractions=500))
    result = await DeterministicTripEvaluator().evaluate(
        _intent(budget=500), plan
    )
    assert result.status is EvaluationStatus.FAIL
    assert any(issue.code == BUDGET_VIOLATION for issue in result.issues)


async def test_evaluator_must_have_missing_is_fail():
    plan = _plan()
    result = await DeterministicTripEvaluator().evaluate(
        _intent(must_have=["长城"]), plan
    )
    assert result.status is EvaluationStatus.FAIL
    assert any(issue.code == MUST_HAVE_MISSING for issue in result.issues)


async def test_evaluator_accepts_short_chinese_place_alias():
    """用户简称“北航”应匹配已安排的完整 POI 名称。"""
    plan = _plan(days=[_day(attractions=[schemas.Attraction(
        name="北京航空航天大学学院路校区航空航天博物馆",
        address="学院路37号", description="航空航天展览", evidence_ids=["place@1#tool0"],
    )])])
    result = await DeterministicTripEvaluator().evaluate(_intent(must_have=["北航"]), plan)
    assert not any(issue.code == MUST_HAVE_MISSING for issue in result.issues)


async def test_evaluator_schedule_conflict_is_pass():
    plan = _plan(end_date="2026-10-02", days=[_day(), _day(day_index=1, date="2026-10-02")])
    intent = _intent(duration_days=2, end_date="2026-10-02")
    result = await DeterministicTripEvaluator().evaluate(intent, plan)
    assert result.status is EvaluationStatus.PASS  # 2 天 2 行 一致


async def test_evaluator_schedule_day_count_mismatch_fail():
    plan = _plan(end_date="2026-10-02")  # 声明 2 天但只有 1 天
    result = await DeterministicTripEvaluator().evaluate(_intent(), plan)
    assert result.status is EvaluationStatus.FAIL
    assert any(issue.code == SCHEDULE_CONFLICT for issue in result.issues)


async def test_evaluator_unsupported_claim_is_indeterminate():
    attraction = schemas.Attraction(
        name="未知名胜",
        address="某处",
        description="介绍",
        evidence_ids=[],
    )
    plan = _plan(days=[_day(attractions=[attraction])], budget=None)
    intent = _intent(budget=None)
    result = await DeterministicTripEvaluator().evaluate(intent, plan)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert any(issue.code == UNSUPPORTED_CLAIM for issue in result.issues)
    assert all(issue.severity is IssueSeverity.WARNING for issue in result.issues)


async def test_evaluator_empty_days_is_fail():
    plan = _plan(days=[])
    result = await DeterministicTripEvaluator().evaluate(_intent(), plan)
    assert result.status is EvaluationStatus.FAIL
    assert any(issue.code == INCOMPLETE_PLAN for issue in result.issues)


async def test_evaluator_long_sentence_must_have_downgraded_to_warning():
    """复合长句 must_have：行程含目的地+景点时不误判 FAIL，而是 INDETERMINATE 待核实。"""
    from app.evaluation.deterministic import MUST_HAVE_AMBIGUOUS

    plan = _plan()
    intent = _intent(must_have=["上午故宫博物院，下午安排故宫附近步行可达的两个景点"])
    result = await DeterministicTripEvaluator().evaluate(intent, plan)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert any(issue.code == MUST_HAVE_AMBIGUOUS for issue in result.issues)
    assert not any(issue.code == "MUST_HAVE_MISSING" for issue in result.issues)


async def test_evaluator_short_must_have_still_fails():
    intent = _intent(must_have=["长城"])
    result = await DeterministicTripEvaluator().evaluate(intent, _plan())
    assert result.status is EvaluationStatus.FAIL
