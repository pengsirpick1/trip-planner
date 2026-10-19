"""规划阶段服务（迁自 travel_agent/agent/app/planning_service.py + SIMPLE 支持）。

Phase B：SIMPLE 路由也用“小任务图”走 Runtime（图上界由 PlanBuilder 控制），
与 COMPLEX 同构执行，避免三分支的固定假结构；PlanningService 仍不执行任何工具。
"""

import logging

from ..domain import ExecutionRoute, PlanningContext, PlanningResult, RouteDecision, UserIntent
from ..domain.planning import PlanTaskDraft

log = logging.getLogger(__name__)


class PlanningProhibitedError(ValueError):
    pass


_PLANNABLE_ROUTES = {ExecutionRoute.COMPLEX, ExecutionRoute.SIMPLE}


class PlanningService:
    def __init__(self, planner, builder, registry=None):
        self.planner = planner
        self.builder = builder
        self.registry = registry

    async def plan(
        self,
        intent: UserIntent,
        decision: RouteDecision,
        context: PlanningContext,
        version: int = 1,
    ) -> PlanningResult:
        if decision.route not in _PLANNABLE_ROUTES:
            raise PlanningProhibitedError("planning requires COMPLEX or SIMPLE route")
        log.info("planning_started", extra={"event": "planning_started"})
        try:
            draft = await self.planner.plan(intent, context)
        except Exception as error:
            return PlanningResult(success=False, errors=[f"planner output failure: {error}"],
                metadata={'diagnostics': [error.diagnostic]} if getattr(error, 'diagnostic', None) else {})
        needs_accommodation = (intent.duration_days or 0) >= 2 or any(
            term in intent.raw_query for term in ("酒店", "住宿", "住哪", "住在", "民宿", "青旅")
        )
        if (intent.planning_intent is not False and needs_accommodation
                and "accommodation_research" in context.available_capabilities):
            hotel = next((task for task in draft.tasks if "accommodation_research" in task.required_capabilities), None)
            if hotel is None:
                hotel = PlanTaskDraft(
                    id="hotel_research", goal=f"研究{intent.destination or '目的地'}内符合住宿偏好的酒店候选及通勤",
                    required_capabilities={"accommodation_research", "geospatial_reasoning"},
                    dependencies=[], expected_output="带路线通勤证据的酒店候选排序与淘汰理由",
                    completion_criteria="至少检索候选并核验到主要游览区的通勤；不声称实时房价或库存", priority=0,
                )
                draft.tasks.append(hotel)
            for task in draft.tasks:
                if "itinerary_synthesis" in task.required_capabilities and hotel.id not in task.dependencies:
                    task.dependencies.append(hotel.id)
        log.info("planning_output_received", extra={"event": "planning_output_received"})
        result = self.builder.build(draft, context, version=version)
        if result.success and self.registry is not None:
            for task in result.graph.tasks:
                try:
                    self.registry.resolve(task.required_capabilities)
                except LookupError as error:
                    return PlanningResult(success=False, errors=[f'Unexecutable task {task.id}: {error}'])
        log.info(
            "plan_validated" if result.success else "plan_validation_failed",
            extra={"event": "plan_validated" if result.success else "plan_validation_failed",
                   "errors": result.errors},
        )
        return result
