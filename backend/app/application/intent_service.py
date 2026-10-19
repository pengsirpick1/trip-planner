"""意图阶段服务（迁自 travel_agent/agent/app/intent_service.py）。"""

import logging
import inspect

from ..domain import (
    ClarificationRecord,
    ConversationState,
    IntentTurnResult,
    IntentTurnStatus,
    UserIntent,
)
from ..elicitation.analysis import RequirementAnalyzer
from ..elicitation.questions import ClarificationGenerator
from ..intent.merge import merge_intent

log = logging.getLogger(__name__)


class IntentService:
    def __init__(
        self,
        parser,
        analyzer: RequirementAnalyzer | None = None,
        question_generator: ClarificationGenerator | None = None,
    ):
        self.parser = parser
        self.analyzer = analyzer or RequirementAnalyzer()
        self.question_generator = question_generator or ClarificationGenerator()

    async def handle_message(
        self,
        message: str,
        state: ConversationState | None = None,
        session_id: str = "default",
        memory_context: list[dict] | None = None,
    ) -> tuple[ConversationState, IntentTurnResult]:
        self._trace("intent_parse_started")
        # 旧测试/第三方 Parser 仍可实现两参数 parse；生产 LLM parser 接收长期记忆。
        parameters = inspect.signature(self.parser.parse).parameters.values()
        supports_memory = "memory_context" in inspect.signature(self.parser.parse).parameters or any(
            item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters
        )
        if supports_memory:
            update = await self.parser.parse(message, state, memory_context=memory_context or [])
        else:
            update = await self.parser.parse(message, state)
        self._trace("intent_update_extracted", fields=sorted(update.extracted_fields))
        # Entity normalisation shares the same LLM pass as intent parsing: no
        # additional round trip before the planner or MCP researcher starts.
        resolutions = {
            str(alias).strip(): str(canonical).strip()
            for alias, canonical in update.entity_resolutions.items()
            if str(alias).strip() and str(canonical).strip()
        }
        for field in ("must_have", "avoid", "destination_candidates"):
            values = update.extracted_fields.get(field)
            if isinstance(values, list):
                update.extracted_fields[field] = [resolutions.get(str(value).strip(), value) for value in values]
        base_intent = state.intent if state else UserIntent(session_id=session_id, raw_query=message)
        intent = merge_intent(base_intent, update)
        if resolutions:
            intent.metadata["entity_aliases"] = {
                **intent.metadata.get("entity_aliases", {}),
                **resolutions,
            }
        # 记忆是可被本轮明确需求覆盖的软偏好，供 Planner 读取；它不是事实来源。
        if memory_context:
            intent.metadata["long_term_memory"] = memory_context
        if state:
            intent.raw_query = f'{state.intent.raw_query}\n用户补充：{message}'
        self._trace("intent_merged")
        analysis = self.analyzer.analyze(intent, update.ambiguities)
        self._trace("requirement_analysis_completed", ready=analysis.ready)
        if intent.task_type and intent.task_type != "travel":
            status, questions = IntentTurnStatus.OUT_OF_DOMAIN, []
        elif analysis.conflicts:
            status, questions = IntentTurnStatus.CONFLICT, []
            self._trace("intent_conflict_detected", conflicts=analysis.conflicts)
        elif analysis.ready:
            status, questions = IntentTurnStatus.READY, []
            self._trace("intent_ready")
        else:
            status, questions = (
                IntentTurnStatus.NEEDS_CLARIFICATION,
                self.question_generator.generate(intent, analysis),
            )
            self._trace("clarification_generated", count=len(questions))
        history = [
            record.model_copy(deep=True)
            for record in (state.clarification_history if state else [])
        ]
        if state and history:
            history[-1].user_answer = message
        if questions:
            history.append(ClarificationRecord(questions=questions))
        next_state = ConversationState(
            intent=intent,
            clarification_history=history,
            active_plan=state.active_plan if state else None,
            plan_revision=state.plan_revision if state else 0,
        )
        return next_state, IntentTurnResult(
            intent=intent,
            status=status,
            questions=questions,
            assumptions=[*update.assumptions, *analysis.assumptions],
            analysis=analysis,
            explicit_fields=dict(update.extracted_fields),
        )

    @staticmethod
    def _trace(event, **fields):
        log.info(event, extra={"event": event, **fields})
