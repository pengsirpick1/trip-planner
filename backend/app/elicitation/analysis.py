"""需求分析（迁自 travel_agent/agent/elicitation/analysis.py + Phase B DIRECT 修复）。

Phase B 修复（审计 G1 / D4）：destination 与 travel_time 只对需要完整行程
（planning_intent != False）的请求作为 blocking 字段；
planning_intent=False 的信息/决策类问题不再被日期或目的地阻断。
"""

from ..domain import RequirementAnalysis, UserIntent


class RequirementAnalyzer:
    """对结构化 intent 的确定性策略，绝不直接处理自然语言消息。"""

    def analyze(self, intent: UserIntent, ambiguities: list[str] | None = None) -> RequirementAnalysis:
        if intent.task_type and intent.task_type != "travel":
            return RequirementAnalysis(
                ready=False,
                clarification_needed=False,
                conflicts=["This request is outside the travel domain."],
            )
        conflicts = [item for item in (ambiguities or []) if item.startswith("invalid ")]
        if intent.duration_days and intent.start_date and intent.end_date:
            inclusive_days = (intent.end_date - intent.start_date).days + 1
            if inclusive_days != intent.duration_days:
                conflicts.append("duration_days conflicts with the supplied date range")
        if conflicts:
            return RequirementAnalysis(ready=False, clarification_needed=True, conflicts=conflicts)

        # DIRECT/信息类问题：不需要完整行程要素即可回答（Phase B）
        if intent.planning_intent is False:
            assumptions = []
            if not intent.budget:
                assumptions.append("Budget is flexible and can be refined during planning.")
            return RequirementAnalysis(
                ready=True,
                missing_fields=[],
                blocking_fields=[],
                assumptions=assumptions,
                clarification_needed=False,
            )

        destinations = bool(intent.destination or intent.destination_candidates)
        has_time = bool(intent.duration_days or (intent.start_date and intent.end_date))
        missing = [
            field
            for field, present in (("destination", destinations), ("travel_time", has_time))
            if not present
        ]
        assumptions = []
        if not intent.budget:
            assumptions.append("Budget is flexible and can be refined during planning.")
        return RequirementAnalysis(
            ready=not missing,
            missing_fields=missing,
            blocking_fields=missing,
            assumptions=assumptions,
            clarification_needed=bool(missing),
        )
