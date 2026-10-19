"""Phase B：真实 M1→M5 处理器集成（确定性 fake LLM，无网络）。"""

from __future__ import annotations

import pytest

from app.application.travel_processor import TravelPipelineProcessor
from app.application.travel_service import ResultKind, RunRecord
from app.executors.react import AgentProfile, ReActExecutor
from app.intent.llm_parser import LLMIntentParser
from app.planning.builder import PlanBuilder
from app.planning.llm_structured import LLMStructuredPlanner
from app.routing.llm_analyzer import LLMComplexityAnalyzer
from app.routing.policy import ComplexityRouter
from app.application.intent_service import IntentService
from app.application.routing_service import RoutingService
from app.application.planning_service import PlanningService
from app.runtime.graph_runtime import GraphRuntime
from app.runtime.registry import ExecutorRegistry
from app.tools import ToolRegistry
from tests.fakes import CannedStructuredLLM, FakeWeatherTool

_CAPS = {"weather_research", "itinerary_synthesis"}


def _run(message: str) -> RunRecord:
    return RunRecord(run_id="run-1", session_id="sess-1")


def _make_pipeline(llm) -> TravelPipelineProcessor:
    tools = ToolRegistry()
    tools.register(FakeWeatherTool())
    registry = ExecutorRegistry()
    registry.register(
        ReActExecutor(
            "react-integration",
            llm,
            tools,
            AgentProfile("integration-agent", _CAPS, {"weather"}, max_steps=4),
        ),
        _CAPS,
    )
    runtime = GraphRuntime(registry)
    intent_service = IntentService(LLMIntentParser(llm))
    routing_service = RoutingService(LLMComplexityAnalyzer(llm), ComplexityRouter())
    planning_service = PlanningService(LLMStructuredPlanner(llm), PlanBuilder())
    return TravelPipelineProcessor(
        intent_service=intent_service,
        routing_service=routing_service,
        planning_service=planning_service,
        runtime=runtime,
        llm=llm,
        capabilities=_CAPS,
    )


_INTENT_PLAN = {
    "task_type": "travel",
    "planning_intent": True,
    "destination": "北京",
    "start_date": "2026-10-01",
    "end_date": "2026-10-03",
    "duration_days": 3,
    "budget": 3000,
    "preferences": ["美食"],
}
_SIGNALS_COMPLEX = {
    "estimated_subgoals": 3,
    "requires_external_information": True,
    "requires_multiple_information_domains": True,
    "requires_cross_result_synthesis": True,
    "confidence": 0.9,
}
_DRAFT_COMPLEX = {
    "tasks": [
        {
            "id": "weather_north",
            "goal": "Query CURRENT weather for Beijing north area",
            "required_capabilities": ["weather_research"],
            "dependencies": [],
            "expected_output": "measured weather",
            "completion_criteria": "measured",
            "priority": 0,
        },
        {
            "id": "weather_south",
            "goal": "Query CURRENT weather for Beijing south area",
            "required_capabilities": ["weather_research"],
            "dependencies": [],
            "expected_output": "measured weather",
            "completion_criteria": "measured",
            "priority": 0,
        },
        {
            "id": "synthesis",
            "goal": "Integrate measured weather findings",
            "required_capabilities": ["itinerary_synthesis"],
            "dependencies": ["weather_north", "weather_south"],
            "expected_output": "integrated answer",
            "completion_criteria": "uses findings",
            "priority": 1,
        },
    ]
}


async def test_complex_flow_runs_until_synthesis_gap():
    llm = CannedStructuredLLM(
        structured_map={
            "IntentUpdate": {"extracted_fields": dict(_INTENT_PLAN)},
            "SemanticComplexitySignals": dict(_SIGNALS_COMPLEX),
            "PlanDraft": dict(_DRAFT_COMPLEX),
        }
    )
    pipeline = _make_pipeline(llm)
    outcome = await pipeline.process("请规划北京三天，预算 3000", _run("s1"))

    # COMPLEX 执行成功，但行程合成（Phase D）未接入 → 明确 failed，不伪造行程
    assert outcome.kind is ResultKind.FAILED
    assert outcome.error_code == "TRIP_PLAN_SYNTHESIS_NOT_READY"
    assert outcome.data["graph_version"] == 1
    event_names = {event["event"] for event in outcome.events}
    assert "route_selected" in event_names
    assert "plan_created" in event_names
    assert "execution_completed" in event_names
    assert "task_completed" in event_names  # runtime 事件已映射落 run
    assert outcome.data["completed_tasks"] == 3


async def test_direct_question_answers_without_dates():
    llm = CannedStructuredLLM(
        structured_map={
            "IntentUpdate": {
                "extracted_fields": {
                    "task_type": "travel",
                    "planning_intent": False,
                    "destination": "北京",
                }
            },
            "SemanticComplexitySignals": {
                "estimated_subgoals": 1,
                "requires_external_information": False,
            },
        },
        direct_final="北京故宫周三开放时间为 8:30-17:00。",
    )
    pipeline = _make_pipeline(llm)
    outcome = await pipeline.process("北京故宫周三几点开放？", _run("s2"))

    assert outcome.kind is ResultKind.ANSWER
    assert "8:30" in outcome.message
    assert outcome.data["not_an_itinerary"] is True
    events = {event["event"] for event in outcome.events}
    assert "route_selected" in events and "answer_ready" in events


async def test_simple_weather_flow_answers_via_tool():
    llm = CannedStructuredLLM(
        structured_map={
            "IntentUpdate": {
                "extracted_fields": {
                    "task_type": "travel",
                    "planning_intent": False,
                    "destination": "北京",
                }
            },
            "SemanticComplexitySignals": {
                "estimated_subgoals": 1,
                "requires_external_information": True,
            },
            "PlanDraft": {
                "tasks": [
                    {
                        "id": "weather_now",
                        "goal": "Query CURRENT weather for Beijing",
                        "required_capabilities": ["weather_research"],
                        "dependencies": [],
                        "expected_output": "measured weather",
                        "completion_criteria": "measured",
                        "priority": 0,
                    }
                ]
            },
        }
    )
    pipeline = _make_pipeline(llm)
    outcome = await pipeline.process("北京现在天气怎么样？", _run("s3"))

    assert outcome.kind is ResultKind.ANSWER
    assert outcome.data["scope"] == "simple"
    events = {event["event"] for event in outcome.events}
    assert "execution_completed" in events


async def test_out_of_domain_is_typed_failure():
    llm = CannedStructuredLLM(
        structured_map={
            "IntentUpdate": {"extracted_fields": {"task_type": "non_travel"}}
        }
    )
    pipeline = _make_pipeline(llm)
    outcome = await pipeline.process("帮我写一首诗", _run("s4"))
    assert outcome.kind is ResultKind.FAILED
    assert outcome.error_code == "OUT_OF_DOMAIN"


async def test_missing_planning_facts_ask_clarification():
    llm = CannedStructuredLLM(
        structured_map={
            "IntentUpdate": {
                "extracted_fields": {"task_type": "travel", "planning_intent": True}
            }
        }
    )
    pipeline = _make_pipeline(llm)
    outcome = await pipeline.process("帮我规划一次旅行", _run("s5"))
    assert outcome.kind is ResultKind.CLARIFICATION
    assert outcome.questions
    fields = {question["field"] for question in outcome.questions}
    assert "destination" in fields and "travel_time" in fields
