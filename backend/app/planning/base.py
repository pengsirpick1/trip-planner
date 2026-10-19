from typing import Protocol

from ..domain import PlanDraft, PlanningContext, UserIntent


class Planner(Protocol):
    async def plan(self, intent: UserIntent, context: PlanningContext) -> PlanDraft: ...
