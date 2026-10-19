"""Phase D：TripPlan 合成（证据校验 / 未知值保持 None / 溯源）。"""

from __future__ import annotations

import pytest

from app.domain import GraphExecutionState, Task, TaskGraph, TaskResult, UserIntent
from app.domain.provenance import build_evidence_index, collect_coordinate_pool
from app.synthesis import TripPlanSynthesizer
from tests.fakes import CannedStructuredLLM

_COORD = "116.397000,39.909000"  # 证据池中的 canonical 坐标（故宫）


def _intent() -> UserIntent:
    return UserIntent(
        session_id="s1",
        raw_query="规划北京一日游",
        planning_intent=True,
        destination="北京",
        start_date="2026-10-01",
        end_date="2026-10-01",
        duration_days=1,
        budget=500,
    )


def _tool_result_payload():
    return {
        "tool_name": "maps_text_search",
        "success": True,
        "data": [{"id": "B000A8UIN8", "name": "故宫博物院", "address": "景山前街4号",
                  "location": _COORD}],
        "source_references": ["mcp://amap/maps_text_search"],
        "metadata": {"provider": "amap"},
    }


def _state() -> GraphExecutionState:
    return GraphExecutionState(
        session_id="s1",
        graph=TaskGraph(
            graph_id="s1-v1",
            version=1,
            tasks=[
                Task(id="place_research", goal="故宫 POI 研究",
                     required_capabilities={"place_research"}),
                Task(id="synthesis", goal="合成", dependencies=["place_research"],
                     required_capabilities={"itinerary_synthesis"}),
            ],
        ),
        task_results={
            "place_research": TaskResult(
                task_id="place_research",
                success=True,
                output="故宫博物院，景山前街4号",
                metadata={
                    "tool_results": [_tool_result_payload()],
                    "sources": ["mcp://amap/maps_text_search"],
                },
            ),
            "synthesis": TaskResult(
                task_id="synthesis",
                success=True,
                output="ok",
                metadata={"sources": [], "tool_results": []},
            ),
        },
    )


def _plan_fixture(**overrides):
    plan = {
        "city": "北京",
        "start_date": "2026-10-01",
        "end_date": "2026-10-01",
        "days": [
            {
                "date": "2026-10-01",
                "day_index": 0,
                "description": "故宫一日",
                "transportation": "地铁",
                "accommodation": "当日往返",
                "attractions": [
                    {
                        "name": "故宫博物院",
                        "address": "景山前街4号",
                        "poi_id": "B000A8UIN8",
                        "location_str": _COORD,
                        "description": "明清皇宫",
                        "evidence_ids": [],
                        "ticket_price": None,
                    }
                ],
                "meals": [],
            }
        ],
        "weather_info": [],
        "overall_suggestions": "建议早到。",
        "budget": None,
        "assumptions": ["门票未验证"],
    }
    plan.update(overrides)
    return plan


async def test_synthesize_produces_traceable_trip_plan():
    llm = CannedStructuredLLM(structured_map={"SynthesizedPlan": _plan_fixture()})
    synthesizer = TripPlanSynthesizer(llm)
    dto = await synthesizer.synthesize(_intent(), _state())

    assert dto.city == "北京"
    assert dto.graph_version == 1
    assert dto.days[0].attractions[0].name == "故宫博物院"
    location = dto.days[0].attractions[0].location
    assert location is not None
    assert abs(location.longitude - 116.397) < 1e-3
    # 未知票价保持 None，绝不归 0
    assert dto.days[0].attractions[0].ticket_price is None
    assert dto.days[0].attractions[0].unknown_fields == ["ticket_price"]
    # 溯源：sources 索引按 task 分组且含 provider
    assert dto.sources is not None
    assert any(
        "place_research" in task_id for task_id in dto.sources
    )
    # 未引用证据的字段被记录为假设
    assert any("门票未验证" in assumption for assumption in dto.assumptions)


async def test_synthesize_rejects_coordinate_outside_evidence_pool():
    fixture = _plan_fixture()
    bogus = dict(fixture["days"][0]["attractions"][0])
    bogus["location_str"] = "999.500000,888.000000"  # 不在证据池
    fixture["days"][0]["attractions"] = [bogus]
    llm = CannedStructuredLLM(structured_map={"SynthesizedPlan": fixture})
    dto = await TripPlanSynthesizer(llm).synthesize(_intent(), _state())

    attraction = dto.days[0].attractions[0]
    assert attraction.location is None
    assert any("不在证据池" in assumption for assumption in dto.assumptions)


async def test_synthesize_drops_unknown_evidence_ids():
    fixture = _plan_fixture()
    item = dict(fixture["days"][0]["attractions"][0])
    item["evidence_ids"] = ["ghost@1#tool0"]
    fixture["days"][0]["attractions"] = [item]
    llm = CannedStructuredLLM(structured_map={"SynthesizedPlan": fixture})
    dto = await TripPlanSynthesizer(llm).synthesize(_intent(), _state())
    assert any("evidence 引用" in assumption for assumption in dto.assumptions)


def test_evidence_index_and_coordinate_pool():
    index = build_evidence_index(_state())
    assert index
    entry = next(iter(index.values()))
    assert entry.provider == "amap"
    pool = collect_coordinate_pool(index)
    assert _COORD in pool
    assert pool[_COORD]["tool_name"] == "maps_text_search"


def test_xhs_search_url_helper():
    from app.synthesis.trip_plan import _xhs_search_url

    url = _xhs_search_url("故宫博物院", "北京")
    assert url.startswith("https://www.xiaohongshu.com/search_result?keyword=")
    assert "故宫博物院" not in url  # 已 URL 编码
    assert "search_result" in url


async def test_synthesize_hotel_candidate_keeps_only_traceable_evidence_and_safe_links():
    fixture = _plan_fixture(hotel_candidates=[{
        "name": "前门酒店", "address": "前门大街", "rating": "4.5",
        "distance_summary": "距故宫约 2 km", "commute_summary": "公共交通约 20 分钟",
        "selection_reason": "靠近首日游览区", "rejection_reason": "实时房价待平台确认",
        "evidence_ids": ["place_research@1#tool0"],
    }])
    dto = await TripPlanSynthesizer(CannedStructuredLLM(structured_map={"SynthesizedPlan": fixture})).synthesize(_intent(), _state())
    assert len(dto.hotel_candidates) == 1
    candidate = dto.hotel_candidates[0]
    assert candidate.source_references == ["mcp://amap/maps_text_search"]
    assert candidate.verification_links["携程核验"].startswith("https://m.ctrip.com/")
    assert candidate.verification_links["美团核验"].startswith("https://i.meituan.com/")
