"""Phase B：M1 意图栈（DIRECT 不被阻断、normalize 年份注入、merge 语义）。"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain import IntentUpdate, UserIntent
from app.elicitation.analysis import RequirementAnalyzer
from app.intent.merge import merge_intent
from app.intent.normalize import IntentNormalizer


def _intent(**overrides) -> UserIntent:
    base = dict(
        session_id="s1",
        raw_query="test",
        planning_intent=True,
        destination="北京",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 3),
        duration_days=3,
    )
    base.update(overrides)
    return UserIntent(**base)


# ---- DIRECT 修复（D4）：planning_intent=False 不再被 destination/travel_time 阻断 ----


def test_direct_question_without_dates_is_ready():
    analyzer = RequirementAnalyzer()
    intent = _intent(
        planning_intent=False,
        destination="北京",
        start_date=None,
        end_date=None,
        duration_days=None,
    )
    analysis = analyzer.analyze(intent)
    assert analysis.ready is True
    assert analysis.blocking_fields == []


def test_planning_question_without_dates_needs_clarification():
    analyzer = RequirementAnalyzer()
    intent = _intent(
        planning_intent=True,
        destination="北京",
        start_date=None,
        end_date=None,
        duration_days=None,
    )
    analysis = analyzer.analyze(intent)
    assert analysis.ready is False
    assert analysis.blocking_fields == ["travel_time"]


def test_planning_intent_none_is_conservative():
    analyzer = RequirementAnalyzer()
    intent = _intent(
        planning_intent=None,
        destination=None,
        start_date=None,
        end_date=None,
        duration_days=None,
    )
    analysis = analyzer.analyze(intent)
    assert analysis.ready is False
    assert set(analysis.missing_fields) == {"destination", "travel_time"}


def test_out_of_domain_rejected():
    analyzer = RequirementAnalyzer()
    intent = _intent(task_type="non_travel", planning_intent=None)
    analysis = analyzer.analyze(intent)
    assert analysis.ready is False
    assert analysis.conflicts


# ---- normalize：年份注入 + 非法输入进 ambiguities ----


def test_normalize_uses_injected_year():
    normalizer = IntentNormalizer()
    update = IntentUpdate(extracted_fields={"start_date": "10月1日"})
    normalized = normalizer.normalize(update, year=2025)
    assert normalized.extracted_fields["start_date"] == date(2025, 10, 1)


def test_normalize_default_year_from_clock(monkeypatch):
    from datetime import datetime, timezone

    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, 3, 1, tzinfo=tz or timezone.utc)

    normalizer = IntentNormalizer()
    update = IntentUpdate(extracted_fields={"end_date": "3月5日"})
    normalized = normalizer.normalize(update, now=_Fixed.now())
    assert normalized.extracted_fields["end_date"] == date(2027, 3, 5)


def test_normalize_invalid_date_flagged():
    normalizer = IntentNormalizer()
    update = IntentUpdate(extracted_fields={"start_date": "2025-13-40"})
    normalized = normalizer.normalize(update, year=2025)
    assert "start_date" not in normalized.extracted_fields
    assert any("invalid start_date" in item for item in normalized.ambiguities)


def test_normalize_string_lists():
    normalizer = IntentNormalizer()
    update = IntentUpdate(extracted_fields={"preferences": "美食"})
    normalized = normalizer.normalize(update)
    assert normalized.extracted_fields["preferences"] == ["美食"]


# ---- merge：list 合并 / correction 替换 / 既有信息保持 ----


def test_merge_appends_preferences_without_duplicate():
    intent = _intent(preferences=["美食"])
    update = IntentUpdate(extracted_fields={"preferences": ["美食", "徒步"]})
    merged = merge_intent(intent, update)
    assert merged.preferences == ["美食", "徒步"]


def test_merge_correction_replaces_field():
    intent = _intent(destination="北京")
    update = IntentUpdate(extracted_fields={"destination": "上海"}, correction_fields={"destination"})
    merged = merge_intent(intent, update)
    assert merged.destination == "上海"


def test_merge_preserves_existing_on_non_correction_conflict():
    intent = _intent(destination="北京")
    update = IntentUpdate(extracted_fields={"destination": "上海"})
    merged = merge_intent(intent, update)
    assert merged.destination == "北京"  # 已显式信息优先，parser 不应静默覆盖


def test_normalize_must_have_sentence_split_into_short_places():
    from app.intent.normalize import IntentNormalizer

    normalizer = IntentNormalizer()
    update = IntentUpdate(
        extracted_fields={
            "must_have": ["上午故宫博物院，下午安排故宫附近步行可达的两个景点"]
        }
    )
    normalized = normalizer.normalize(update)
    tokens = normalized.extracted_fields["must_have"]
    assert "故宫" in tokens
    assert not any(len(token) >= 12 for token in tokens)
