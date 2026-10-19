"""LLM 意图解析（迁自 travel_agent/agent/intent/llm_parser.py + planning_intent 扩展）。"""

import json

from ..domain import ConversationState, IntentUpdate, UserIntent
from .normalize import IntentNormalizer


class LLMIntentParser:
    def __init__(self, llm, normalizer: IntentNormalizer | None = None):
        self.llm = llm
        self.normalizer = normalizer or IntentNormalizer()

    async def parse(self, message: str, context: ConversationState | None = None,
                    memory_context: list[dict] | None = None) -> IntentUpdate:
        prior = context.intent.model_dump(mode="json") if context else {}
        active_plan = (context.active_plan if context else None) or {}
        plan_summary = {
            "city": active_plan.get("city"),
            "dates": [active_plan.get("start_date"), active_plan.get("end_date")],
            "days": [
                {"date": day.get("date"), "attractions": [a.get("name") for a in day.get("attractions", [])[:5]],
                 "hotel": (day.get("hotel") or {}).get("name")}
                for day in active_plan.get("days", [])[:7]
            ],
        } if active_plan else None
        schema = UserIntent.model_json_schema()
        for key in ("session_id", "raw_query"):
            schema["properties"].pop(key, None)
        schema.pop("required", None)
        schema["properties"]["task_type"] = {"enum": ["travel", "non_travel"]}
        schema["properties"]["planning_intent"] = {
            "type": "boolean",
            "description": (
                "true if the user asks for an actual itinerary/travel plan; "
                "false for informational, decision or single-aspect questions "
                "(no itinerary requested)"
            ),
        }
        prompt = (
            "Return a JSON IntentUpdate with extracted_fields, confidence, ambiguities, assumptions, "
            "correction_fields, and entity_resolutions. Extract explicit current-message facts into the corresponding "
            "canonical fields, including duration_days as an integer when a duration is given. "
            "Explicit facts must not appear only in assumptions. Assumptions are unconfirmed inferences. "
            "Preserve requested scope and exclusions in constraints, preferences, must_have and avoid. "
            "must_have must be a list of specific places or activities the user explicitly names "
            "as required (e.g. '故宫', '长城', '看升旗'); do NOT put whole sentences, time words "
            "('上午/下午'), quantities ('两个'), or conditions into must_have items. "
            "Use correction_fields for explicitly replaced fields; do not erase unrelated prior facts. "
            "Classify the overall user purpose: travel preparation, destination decisions and "
            "trip-related information requests are travel, including requests limited to one aspect "
            "of a trip. They do not need to request a complete itinerary. Only unrelated purposes "
            "have task_type=non_travel. "
            "Set planning_intent=true only when the user requests an itinerary/plan to be produced "
            "(explicitly or clearly by asking to plan/arrange/schedule a trip). Questions asking for "
            "weather, tickets, hours, comparisons, or feasibility are planning_intent=false; they do "
            "NOT require destination travel dates to be answerable. "
            "Do not plan or invent facts. "
            "When current_plan_summary is supplied, treat references such as 'day two', 'that hotel', "
            "'replace it', and explicit exclusions as edits to that plan. Extract the resulting "
            "constraints, avoid items, preferences, or must_have fields; do not discard existing intent. "
            "Resolve well-known place aliases, abbreviations, campuses, and landmarks before planning. "
            "Put the user wording and its canonical searchable name in entity_resolutions, for example "
            "{'北航':'北京航空航天大学'}; use the canonical name in extracted_fields.must_have/avoid "
            "when it is the referenced destination. Do not guess an entity when the alias is genuinely ambiguous; "
            "put that ambiguity in ambiguities instead. "
            "The extracted_fields keys and value types must follow this schema (omit unstated values): "
            + json.dumps(schema, ensure_ascii=False)
        )
        update = await self.llm.generate_structured(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": (
                    f"message={message}\nexisting={prior}\ncurrent_plan_summary={plan_summary}\n"
                    f"user_memory={memory_context or []}\n"
                    "User memory contains prior stable preferences only. Apply it as a soft default, "
                    "never as a replacement for explicit current-message requirements."
                )},
            ],
            IntentUpdate,
        )
        return self.normalizer.normalize(update)
