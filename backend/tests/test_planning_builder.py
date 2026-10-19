"""Phase B：规划（PlanBuilder 版本参数 / DAG 校验 / capability 约束）。"""

from __future__ import annotations

import pytest

from app.domain import PlanDraft, PlanTaskDraft, PlanningContext, UserIntent
from app.planning.builder import PlanBuilder


def _context(capabilities=None, **intent_overrides) -> PlanningContext:
    intent = UserIntent(
        session_id="sess-1",
        raw_query="plan trip",
        planning_intent=True,
        destination="北京",
        **intent_overrides,
    )
    return PlanningContext(
        session_id=intent.session_id,
        intent=intent,
        assumptions=[],
        available_capabilities=set(capabilities or ["weather_research", "itinerary_synthesis"]),
    )


def _draft():
    return PlanDraft(
        tasks=[
            PlanTaskDraft(
                id="research",
                goal="weather research",
                required_capabilities={"weather_research"},
                expected_output="findings",
                completion_criteria="done",
            ),
            PlanTaskDraft(
                id="synthesis",
                goal="synthesize",
                dependencies=["research"],
                required_capabilities={"itinerary_synthesis"},
                expected_output="itinerary",
                completion_criteria="integrated",
            ),
        ]
    )


def test_builder_default_version_is_v1():
    result = PlanBuilder().build(_draft(), _context())
    assert result.success
    assert result.graph.version == 1
    assert result.graph.graph_id == "sess-1-v1"


def test_builder_explicit_version():
    result = PlanBuilder().build(_draft(), _context(), version=3)
    assert result.success
    assert result.graph.version == 3
    assert result.graph.graph_id == "sess-1-v3"


def test_builder_rejects_invalid_version():
    result = PlanBuilder().build(_draft(), _context(), version=0)
    assert not result.success
    assert result.graph is None


def test_builder_rejects_unsupported_capability():
    context = _context(capabilities=["weather_research"])
    result = PlanBuilder().build(_draft(), context)
    assert not result.success
    assert any("unsupported capabilities" in error for error in result.errors)


def test_builder_rejects_missing_dependency():
    draft = PlanDraft(
        tasks=[
            PlanTaskDraft(
                id="a",
                goal="a",
                dependencies=["ghost"],
                required_capabilities={"weather_research"},
                expected_output="x",
                completion_criteria="x",
            )
        ]
    )
    result = PlanBuilder().build(draft, _context())
    assert not result.success
    assert any("missing dependency" in error for error in result.errors)


def test_builder_rejects_cycle():
    draft = PlanDraft(
        tasks=[
            PlanTaskDraft(
                id="a",
                goal="a",
                dependencies=["b"],
                required_capabilities={"weather_research"},
                expected_output="x",
                completion_criteria="x",
            ),
            PlanTaskDraft(
                id="b",
                goal="b",
                dependencies=["a"],
                required_capabilities={"weather_research"},
                expected_output="x",
                completion_criteria="x",
            ),
        ]
    )
    result = PlanBuilder().build(draft, _context())
    assert not result.success
    assert any("cycle" in error for error in result.errors)


def test_task_count_limit():
    tasks = [
        PlanTaskDraft(
            id=f"t{i}",
            goal="g",
            required_capabilities={"weather_research"},
            expected_output="x",
            completion_criteria="x",
        )
        for i in range(13)
    ]
    result = PlanBuilder(max_tasks=12).build(PlanDraft(tasks=tasks), _context())
    assert not result.success
    assert any("task count exceeds limit" in error for error in result.errors)


def test_light_profile_compiles_parallel_graph_and_prunes_irrelevant_nodes():
    context = _context(capabilities=[
        "place_research", "geospatial_reasoning", "transport_research", "accommodation_research",
        "budget_policy", "itinerary_synthesis",
    ], duration_days=1, budget=500)
    context.metadata = {"execution_profile": "light", "public_transit_supported": False}
    draft = PlanDraft(tasks=[
        PlanTaskDraft(id="forbidden_city", goal="research must-have", required_capabilities={"place_research"}, expected_output="poi", completion_criteria="done"),
        PlanTaskDraft(id="nearby", goal="research nearby walkable POIs", required_capabilities={"place_research", "geospatial_reasoning"}, expected_output="nearby", completion_criteria="done"),
        PlanTaskDraft(id="subway", goal="research subway transit", required_capabilities={"transport_research", "geospatial_reasoning"}, dependencies=["nearby"], expected_output="transit", completion_criteria="done"),
        PlanTaskDraft(id="hotel", goal="research hotel", required_capabilities={"accommodation_research"}, expected_output="hotel", completion_criteria="done"),
        PlanTaskDraft(id="budget", goal="research budget", required_capabilities={"place_research"}, dependencies=["subway"], expected_output="budget", completion_criteria="done"),
        PlanTaskDraft(id="synthesis", goal="synthesis", required_capabilities={"itinerary_synthesis"}, dependencies=["budget"], expected_output="plan", completion_criteria="done"),
    ])
    result = PlanBuilder().build(draft, context)
    assert result.success
    graph = result.graph
    assert [task.id for task in graph.tasks] == ["forbidden_city", "nearby", "budget_policy", "synthesis"]
    assert graph.task("forbidden_city").dependencies == []
    assert graph.task("nearby").dependencies == []
    assert graph.task("budget_policy").dependencies == []
    assert set(graph.task("synthesis").dependencies) == {"forbidden_city", "nearby", "budget_policy"}
    assert set(graph.metadata["pruned_task_ids"]) == {"subway", "hotel", "budget"}
