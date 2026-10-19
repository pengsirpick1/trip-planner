"""确定性行程评估器（Phase E）。

职责边界（审计 §11）：
- 只读：不调用工具、不修改计划、不执行任务、不接触 LLM（语义评估留 Phase 扩展）。
- 状态推导：存在 high 级别问题 → FAIL；存在 warning 级 → INDETERMINATE（无法验证≠通过）；
  其余 PASS。info 不影响结论。
- 质量失败 ≠ Provider 故障（后者走 INTERNAL/EXECUTION 错误链）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from ..domain import GraphExecutionState, UserIntent
from ..domain.evaluation import (
    EvaluationIssue,
    EvaluationStatus,
    IssueSeverity,
    PlanEvaluationResult,
    RepairScope,
)
from ..models import schemas

# 允许的 issue code（确定性检查用）
MUST_HAVE_MISSING = "MUST_HAVE_MISSING"
MUST_HAVE_AMBIGUOUS = "MUST_HAVE_AMBIGUOUS"
BUDGET_VIOLATION = "BUDGET_VIOLATION"
BUDGET_UNVERIFIED = "BUDGET_UNVERIFIED"
SCHEDULE_CONFLICT = "SCHEDULE_CONFLICT"
UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
INCOMPLETE_PLAN = "INCOMPLETE_PLAN"
MOBILITY_CONSTRAINT_VIOLATION = "MOBILITY_CONSTRAINT_VIOLATION"

_ISSUE_CODES = {
    MUST_HAVE_MISSING,
    MUST_HAVE_AMBIGUOUS,
    BUDGET_VIOLATION,
    BUDGET_UNVERIFIED,
    SCHEDULE_CONFLICT,
    UNSUPPORTED_CLAIM,
    INCOMPLETE_PLAN,
    MOBILITY_CONSTRAINT_VIOLATION,
}


class DeterministicTripEvaluator:
    evaluator_version = "deterministic-v1"

    async def evaluate(
        self,
        intent: UserIntent,
        plan: schemas.TripPlan,
        state: Optional[GraphExecutionState] = None,
    ) -> PlanEvaluationResult:
        issues: list[EvaluationIssue] = []
        self._check_incomplete(plan, issues)
        self._check_schedule(intent, plan, issues)
        if intent.destination and intent.destination.removesuffix('市') != plan.city.removesuffix('市'):
            issues.append(EvaluationIssue(code=INCOMPLETE_PLAN, severity=IssueSeverity.HIGH,
                message=f'目的地不一致：用户 {intent.destination}，行程 {plan.city}',
                plan_field_paths=['city'], repair_scope=RepairScope.SYNTHESIS))
        self._check_must_have(intent, plan, issues)
        self._check_budget(intent, plan, issues)
        self._check_unsupported_claims(plan, issues)
        if state is not None:
            from ..domain.provenance import build_evidence_index
            index = build_evidence_index(state)
            for day in plan.days:
                for attraction in day.attractions:
                    if any(eid not in index for eid in attraction.evidence_ids):
                        issues.append(EvaluationIssue(code=UNSUPPORTED_CLAIM, severity=IssueSeverity.HIGH,
                            message=f'{attraction.name} 包含不存在的证据引用', repair_scope=RepairScope.SYNTHESIS))
            for issue in issues:
                if issue.repair_scope is RepairScope.TASK:
                    cap = 'place_research' if issue.code in {MUST_HAVE_MISSING, INCOMPLETE_PLAN, UNSUPPORTED_CLAIM} else None
                    issue.affected_task_ids = [t.id for t in state.graph.tasks
                        if (cap in t.required_capabilities if cap else 'itinerary_synthesis' not in t.required_capabilities)]

        status = EvaluationStatus.PASS
        if any(issue.severity is IssueSeverity.HIGH for issue in issues):
            status = EvaluationStatus.FAIL
        elif any(issue.severity is IssueSeverity.WARNING for issue in issues):
            status = EvaluationStatus.INDETERMINATE
        return PlanEvaluationResult(
            status=status,
            issues=issues,
            evaluator_version=self.evaluator_version,
            evaluated_plan_revision=plan.plan_revision,
            metadata={
                "issue_counts": {
                    code: sum(1 for issue in issues if issue.code == code)
                    for code in sorted(_ISSUE_CODES)
                },
            },
        )

    # ---- 检查项 ----

    def _check_incomplete(self, plan: schemas.TripPlan, issues: list[EvaluationIssue]) -> None:
        if not plan.days:
            issues.append(
                EvaluationIssue(
                    code=INCOMPLETE_PLAN,
                    severity=IssueSeverity.HIGH,
                    message="行程没有任何天数安排",
                    plan_field_paths=["days"],
                    repair_scope=RepairScope.SYNTHESIS,
                )
            )
            return
        attraction_count = sum(len(day.attractions) for day in plan.days)
        if attraction_count == 0:
            issues.append(
                EvaluationIssue(
                    code=INCOMPLETE_PLAN,
                    severity=IssueSeverity.HIGH,
                    message="行程天数齐全但没有任何景点安排，内容未满足目的",
                    plan_field_paths=["days[*].attractions"],
                    repair_scope=RepairScope.TASK,
                )
            )
        if not (plan.overall_suggestions or "").strip():
            issues.append(
                EvaluationIssue(
                    code=INCOMPLETE_PLAN,
                    severity=IssueSeverity.WARNING,
                    message="缺少总体建议",
                    plan_field_paths=["overall_suggestions"],
                    repair_scope=RepairScope.SYNTHESIS,
                )
            )

    def _check_schedule(self, intent: UserIntent, plan: schemas.TripPlan, issues: list[EvaluationIssue]) -> None:
        try:
            start = date.fromisoformat(plan.start_date)
            end = date.fromisoformat(plan.end_date)
        except ValueError:
            issues.append(
                EvaluationIssue(
                    code=SCHEDULE_CONFLICT,
                    severity=IssueSeverity.HIGH,
                    message=f"日期格式非法: {plan.start_date}~{plan.end_date}",
                    plan_field_paths=["start_date", "end_date"],
                    repair_scope=RepairScope.SYNTHESIS,
                )
            )
            return
        expected_days = (end - start).days + 1
        if (intent.start_date and intent.start_date != start) or (intent.end_date and intent.end_date != end):
            issues.append(EvaluationIssue(code=SCHEDULE_CONFLICT, severity=IssueSeverity.HIGH,
                message='行程起止日期与用户指定日期不一致', plan_field_paths=['start_date', 'end_date'],
                repair_scope=RepairScope.SYNTHESIS))
        if expected_days != len(plan.days):
            issues.append(
                EvaluationIssue(
                    code=SCHEDULE_CONFLICT,
                    severity=IssueSeverity.HIGH,
                    message=f"日期跨度 {expected_days} 天与行程 {len(plan.days)} 天不一致",
                    plan_field_paths=["days", "end_date"],
                    repair_scope=RepairScope.SYNTHESIS,
                )
            )
        if intent.duration_days and intent.duration_days != len(plan.days):
            issues.append(
                EvaluationIssue(
                    code=SCHEDULE_CONFLICT,
                    severity=IssueSeverity.HIGH,
                    message=f"用户要求 {intent.duration_days} 天，行程为 {len(plan.days)} 天",
                    plan_field_paths=["days"],
                    repair_scope=RepairScope.SYNTHESIS,
                )
            )
        seen_dates: set[str] = set()
        for position, day in enumerate(plan.days):
            if day.date != (start + timedelta(days=position)).isoformat():
                issues.append(EvaluationIssue(code=SCHEDULE_CONFLICT, severity=IssueSeverity.HIGH,
                    message=f'第 {position + 1} 天日期 {day.date} 不在正确的连续日期上',
                    plan_field_paths=[f'days[{position}].date'], repair_scope=RepairScope.SYNTHESIS))
            if day.date in seen_dates:
                issues.append(
                    EvaluationIssue(
                        code=SCHEDULE_CONFLICT,
                        severity=IssueSeverity.HIGH,
                        message=f"重复日期 {day.date}",
                        plan_field_paths=[f"days[{position}].date"],
                        repair_scope=RepairScope.SYNTHESIS,
                    )
                )
            seen_dates.add(day.date)
            if day.day_index != position:
                issues.append(
                    EvaluationIssue(
                        code=SCHEDULE_CONFLICT,
                        severity=IssueSeverity.WARNING,
                        message=f"days[{position}] day_index={day.day_index} 与数组位置不一致",
                        plan_field_paths=[f"days[{position}].day_index"],
                        repair_scope=RepairScope.SYNTHESIS,
                    )
                )

    def _check_must_have(self, intent: UserIntent, plan: schemas.TripPlan, issues: list[EvaluationIssue]) -> None:
        if not intent.must_have:
            return
        attraction_names = [attr.name or "" for day in plan.days for attr in day.attractions]

        def is_ordered_abbreviation(token: str, name: str) -> bool:
            """Accept common short Chinese place names only when every character
            appears in order in a scheduled POI name (北航 → 北京航空航天大学).

            This is deliberately constrained to 2–4 CJK characters so ordinary
            long requirements still need exact evidence and cannot become a false
            positive through fuzzy matching.
            """
            if not (2 <= len(token) <= 4 and all('\u4e00' <= char <= '\u9fff' for char in token)):
                return False
            cursor = 0
            for char in token:
                cursor = name.find(char, cursor)
                if cursor < 0:
                    return False
                cursor += 1
            return True

        def is_scheduled(token: str) -> bool:
            token = token.lower().strip()
            return any(
                token in name.lower()
                or is_ordered_abbreviation(token, name.lower())
                for name in attraction_names
                if name
            )

        assumptions_text = " ".join(plan.assumptions or []).lower()
        for token in intent.must_have:
            token_lower = token.lower()
            if not is_scheduled(token_lower):
                attraction_count = sum(len(day.attractions) for day in plan.days)
                # 长句/复合要求的 must_have 无法逐字核对：
                # 若计划已含目的地与景点，降级为“待人工核实”而不是误判缺失
                if len(token) >= 12 and attraction_count > 0 and intent.destination:
                    issues.append(
                        EvaluationIssue(
                            code=MUST_HAVE_AMBIGUOUS,
                            severity=IssueSeverity.WARNING,
                            message=(
                                f"必去要求为复合长句，无法精确核对：{token}；"
                                f"行程已含 {plan.city} 的 {attraction_count} 个景点，请人工确认覆盖"
                            ),
                            evidence=[f"intent.must_have={token}"],
                            plan_field_paths=["days[*].attractions"],
                            repair_scope=RepairScope.TASK,
                        )
                    )
                    continue
                issues.append(
                    EvaluationIssue(
                        code=MUST_HAVE_MISSING,
                        severity=IssueSeverity.HIGH,
                        message=f"用户必去/必须包含项未在行程中体现: {token}",
                        evidence=[f"intent.must_have={token}"],
                        plan_field_paths=["days[*].attractions", "overall_suggestions"],
                        repair_scope=RepairScope.TASK,
                    )
                )

    def _check_budget(self, intent: UserIntent, plan: schemas.TripPlan, issues: list[EvaluationIssue]) -> None:
        if not intent.budget:
            return
        budget = plan.budget
        if budget is None or budget.total is None:
            issues.append(
                EvaluationIssue(
                    code=BUDGET_UNVERIFIED,
                    severity=IssueSeverity.WARNING,
                    message="用户给了预算但行程未提供可验证预算汇总",
                    evidence=[f"intent.budget={intent.budget}"],
                    plan_field_paths=["budget"],
                    repair_scope=RepairScope.TASK,
                )
            )
            return
        if budget.total > intent.budget:
            issues.append(
                EvaluationIssue(
                    code=BUDGET_VIOLATION,
                    severity=IssueSeverity.HIGH,
                    message=f"预算超限：计划 {budget.total} > 用户上限 {intent.budget}（差 {budget.total - intent.budget}）",
                    evidence=[f"intent.budget={intent.budget}", f"plan.budget.total={budget.total}"],
                    plan_field_paths=["budget.total"],
                    repair_scope=RepairScope.TASK,
                )
            )

    def _check_unsupported_claims(self, plan: schemas.TripPlan, issues: list[EvaluationIssue]) -> None:
        assumptions_text = " ".join(plan.assumptions or []).lower()
        for day in plan.days:
            for position, attr in enumerate(day.attractions):
                has_evidence = bool(attr.evidence_ids)
                name_covered = (attr.name or "").lower() in assumptions_text
                if not has_evidence and not name_covered:
                    issues.append(
                        EvaluationIssue(
                            code=UNSUPPORTED_CLAIM,
                            severity=IssueSeverity.WARNING,
                            message=f"景点缺少证据引用且未在假设中说明: {attr.name}",
                            plan_field_paths=[f"days[{day.day_index}].attractions[{position}]"],
                            affected_task_ids=[],
                            repair_scope=RepairScope.TASK,
                        )
                    )
