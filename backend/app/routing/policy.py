"""确定性复杂度路由（迁自 travel_agent/agent/routing/policy.py）。

职责边界：Router 只消费结构化信号，不看原始 query，不选 Tool/Agent，不调用 Planner。
"""

from ..domain import ComplexitySignals, ExecutionProfile, ExecutionRoute, RouteDecision, UserIntent


class ComplexityRouter:
    def route(self, signals: ComplexitySignals) -> RouteDecision:
        score, reasons = self._score(signals)
        complex_required = (
            signals.estimated_subgoals >= 3
            or signals.dependency_depth_estimate >= 2
            or (signals.requires_multiple_information_domains and signals.requires_cross_result_synthesis)
            or (signals.requires_constraint_optimization and signals.requires_comparison)
            or (
                signals.requires_cross_result_synthesis
                and (signals.requires_temporal_reasoning or signals.requires_geographic_reasoning)
            )
        )
        if complex_required:
            route = ExecutionRoute.COMPLEX
            reasons.insert(0, "Task structure requires decomposition or cross-result synthesis.")
        elif (
            not signals.requires_external_information
            and not signals.requires_constraint_optimization
            and not signals.requires_cross_result_synthesis
        ):
            route = ExecutionRoute.DIRECT
            reasons.insert(0, "Single factual objective has no meaningful decomposition.")
        else:
            route = ExecutionRoute.SIMPLE
            reasons.insert(0, "Single bounded objective may need data, but no task graph.")
        profile = self._profile(route, score, signals)
        return RouteDecision(
            route=route,
            complexity_score=score,
            reasons=reasons,
            signals=signals,
            confidence=signals.confidence,
            execution_profile=profile,
            metadata={"policy": "v2", "complexity_trigger": complex_required,
                      "max_research_agents": profile.max_research_agents},
        )

    @staticmethod
    def _profile(route: ExecutionRoute, score: int, signals: ComplexitySignals) -> ExecutionProfile:
        if route is ExecutionRoute.DIRECT:
            return ExecutionProfile.DIRECT
        if route is ExecutionRoute.SIMPLE:
            return ExecutionProfile.LIGHT
        if (
            signals.estimated_subgoals >= 5
            or signals.dependency_depth_estimate >= 3
            or (score >= 8 and signals.requires_multiple_information_domains and signals.requires_constraint_optimization)
        ):
            return ExecutionProfile.DEEP
        return ExecutionProfile.STANDARD

    @staticmethod
    def _score(signals: ComplexitySignals) -> tuple[int, list[str]]:
        score = max(0, signals.estimated_subgoals - 1)
        reasons: list[str] = []
        checks = [
            (signals.requires_external_information, "requires external information"),
            (signals.requires_multiple_information_domains, "requires multiple information domains"),
            (signals.requires_comparison, "requires comparison"),
            (signals.requires_constraint_optimization, "requires constraint optimization"),
            (signals.requires_temporal_reasoning, "requires temporal reasoning"),
            (signals.requires_geographic_reasoning, "requires geographic reasoning"),
            (signals.requires_cross_result_synthesis, "requires cross-result synthesis"),
        ]
        for present, reason in checks:
            if present:
                score += 1
                reasons.append(reason)
        score += signals.dependency_depth_estimate
        if signals.dependency_depth_estimate:
            reasons.append(f"dependency depth estimate: {signals.dependency_depth_estimate}")
        return score, reasons


def profile_for_trip_scope(decision: RouteDecision, intent: UserIntent) -> ExecutionProfile:
    """用结构化行程规模校正 LLM 复杂度估计，限制实际 Research Agent 数量。"""
    if decision.route is ExecutionRoute.DIRECT or intent.planning_intent is False:
        return ExecutionProfile.DIRECT
    destinations = {item for item in [intent.destination, *intent.destination_candidates] if item}
    days = intent.duration_days or 0
    # 跨城市、长行程或大量硬性地点才值得启用最大的研究图。
    if len(destinations) >= 2 or days >= 5 or len(intent.must_have) >= 5:
        return ExecutionProfile.DEEP
    # 单城市一日游只需锚点/附近景点两个并行 Evidence 流，预算使用确定性节点。
    if days == 1 and len(destinations) <= 1 and len(intent.must_have) <= 3:
        return ExecutionProfile.LIGHT
    if 2 <= days <= 4:
        return ExecutionProfile.STANDARD
    return decision.execution_profile
