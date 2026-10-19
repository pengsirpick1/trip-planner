"""路由阶段服务（迁自 travel_agent/agent/app/routing_service.py）。"""

import logging

from ..domain import IntentTurnResult, IntentTurnStatus, RouteDecision
from ..routing.errors import RoutingProhibitedError
from ..routing.policy import profile_for_trip_scope

log = logging.getLogger(__name__)


class RoutingService:
    def __init__(self, analyzer, router):
        self.analyzer = analyzer
        self.router = router

    async def route(self, intent_turn: IntentTurnResult) -> RouteDecision:
        if intent_turn.status is not IntentTurnStatus.READY:
            log.info(
                "route_rejected_due_to_intent_status",
                extra={"event": "route_rejected_due_to_intent_status", "status": intent_turn.status.value},
            )
            raise RoutingProhibitedError(f"routing requires READY intent, got {intent_turn.status.value}")
        log.info("complexity_analysis_started", extra={"event": "complexity_analysis_started"})
        signals = await self.analyzer.analyze(intent_turn.intent)
        log.info(
            "complexity_analysis_completed",
            extra={"event": "complexity_analysis_completed", "signals": signals.model_dump()},
        )
        decision = self.router.route(signals)
        profile = profile_for_trip_scope(decision, intent_turn.intent)
        if profile is not decision.execution_profile:
            decision = decision.model_copy(update={
                "execution_profile": profile,
                "metadata": {**decision.metadata, "profile_adjusted_by": "trip_scope_v1",
                             "max_research_agents": profile.max_research_agents},
            })
        log.info(
            "route_decided",
            extra={
                "event": "route_decided",
                "route": decision.route.value,
                "complexity_score": decision.complexity_score,
                "confidence": decision.confidence,
            },
        )
        return decision
