"""LLM 复杂度信号提取（迁自 travel_agent/agent/routing/llm_analyzer.py）。"""

from ..domain import ComplexitySignals, UserIntent
from .normalize import ComplexitySignalsNormalizer, SemanticComplexitySignals


class LLMComplexityAnalyzer:
    def __init__(self, llm):
        self.llm = llm
        self.normalizer = ComplexitySignalsNormalizer()

    async def analyze(self, intent: UserIntent, context: object | None = None) -> ComplexitySignals:
        prompt = (
            'Return JSON only, e.g. {"estimated_subgoals":3,"requires_external_information":true,'
            '"requires_multiple_information_domains":true,"requires_comparison":false,'
            '"requires_constraint_optimization":false,"requires_temporal_reasoning":true,'
            '"requires_geographic_reasoning":true,"requires_cross_result_synthesis":true,'
            '"dependency_depth_estimate":1,"uncertainty":0.2,"confidence":0.8}. '
            "requires_external_information means dynamic, real-time, changeable, or non-stable lookup "
            "(weather, current hours, live price); stable facts like heights, capitals, locations are "
            "false even if sources could be cited. estimated_subgoals counts meaningful independent "
            "work units, not wording. synthesis requires combining multiple independent results. "
            "Infer signals only; do not return an intent, route, tool, agent, or plan."
        )
        semantic = await self.llm.generate_structured(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": str(intent.model_dump(mode="json"))},
            ],
            SemanticComplexitySignals,
        )
        return self.normalizer.normalize(semantic)
