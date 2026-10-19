"""结构化 Planner（迁自 travel_agent/agent/planning/llm_structured.py）。

职责边界：只向模型要声明式 PlanDraft；不把 executor/agent/tool/runtime 状态给模型。
PlanBuilder 负责把 draft 转成安全 DAG。
"""

from __future__ import annotations

import json

from ..domain import PlanDraft, PlanningContext, UserIntent
from .base import Planner


class LLMStructuredPlanner(Planner):
    def __init__(self, llm) -> None:
        self.llm = llm

    async def plan(self, intent: UserIntent, context: PlanningContext) -> PlanDraft:
        system_prompt = (
            "You are the decomposition component of a travel-planning system. "
            "Return JSON for a PlanDraft, never a travel answer. A task is a meaningful unit of "
            "work, not a user prompt, tool call, agent, executor, API request, or execution result.\n\n"
            "The top-level JSON object must contain `tasks` directly; do not wrap it in a `PlanDraft` "
            "key. For every task include: id, goal, task_type, required_capabilities, dependencies, "
            "expected_output, completion_criteria, integer priority, and assigned_agent. Use only the supplied "
            "capability names exactly; never invent a capability. Decompose complex requests into "
            "relevant, independent research streams plus a synthesis task when results must be "
            "combined. Keep research dependencies empty except spatial_route_validation, which may depend on "
            "POI/hotel candidates: the deterministic Graph Compiler owns the final dependency edges. "
            "Do not serialise unrelated research. Avoid both a single giant task and one task per "
            "attraction, meal, or minor detail. Select assigned_agent from agent_profiles; do not select an executor, tool, API, "
            "tool-call sequence, or final answer. Dependencies may reference only task ids in this "
            "PlanDraft."
        )
        system_prompt += (
            '\nDo not add current weather research for a future trip unless the user asks for current weather. '
            'Current measurements cannot validate a future travel date. Prefer one research domain per task. '
            'Unknown ticket prices, bookings and transport costs must remain unknown; do not invent a budget. '
            "\n\nUse non-empty string identifiers such as `places_research` and "
            "`itinerary_synthesis`. Never use numeric IDs: `id` and every value in `dependencies` "
            'must be strings. Example: {"tasks":[{"id":"places_research",'
            '"goal":"Research relevant places","required_capabilities":["place_research"],'
            '"dependencies":[],"expected_output":"place findings",'
            '"completion_criteria":"relevant options covered","priority":0},'
            '{"id":"itinerary_synthesis","goal":"Integrate findings",'
            '"required_capabilities":["itinerary_synthesis"],"dependencies":["places_research"],'
            '"expected_output":"integrated itinerary","completion_criteria":"findings used",'
            '"priority":1}]}'
        )
        system_prompt += (
            "\nCreate hotel_research only for multi-day trips or explicit accommodation requests. "
            "Do not create public-transit/subway research when public_transit_supported is false, and do not "
            "create budget research because budget_policy is deterministic. "
            "For itinerary planning with accommodation, create an independent hotel_research task "
            "using accommodation_research and geospatial_reasoning. It must use POI and route evidence "
            "to rank candidates by commute, and report reasons for selection or rejection. Never claim "
            "live prices, room inventory, or booking availability. The synthesis task must depend on it."
        )
        request = {
            "output_schema": PlanDraft.model_json_schema(),
            "intent": intent.model_dump(mode="json"),
            "assumptions": context.assumptions,
            "available_capabilities": sorted(context.available_capabilities),
            "planning_metadata": context.metadata,
            "agent_profiles": context.metadata.get("agent_profiles", {}),
        }
        return await self.llm.generate_structured(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
            PlanDraft,
        )
