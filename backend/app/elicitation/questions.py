"""澄清问题生成（迁自 travel_agent/agent/elicitation/questions.py）。"""

from ..domain import ClarificationQuestion, RequirementAnalysis, UserIntent


class ClarificationGenerator:
    _TEMPLATES = {
        "destination": ("你这次最想去哪里？", ["国内城市", "邻近国家或地区", "还没想好"]),
        "travel_time": ("大概打算什么时候出发、玩几天？", ["周末两天", "3–5 天", "日期已确定"]),
    }

    def generate(self, intent: UserIntent, analysis: RequirementAnalysis) -> list[ClarificationQuestion]:
        questions = []
        for priority, field in enumerate(analysis.blocking_fields[:2], start=1):
            if field not in self._TEMPLATES:
                continue
            text, options = self._TEMPLATES[field]
            questions.append(
                ClarificationQuestion(
                    id=f"clarify-{field}",
                    field=field,
                    question=text,
                    options=options,
                    priority=priority,
                    reason="Required to make a materially useful travel plan.",
                )
            )
        return questions
