from typing import Protocol

from ..domain import ComplexitySignals, UserIntent


class ComplexityAnalyzer(Protocol):
    async def analyze(self, intent: UserIntent, context: object | None = None) -> ComplexitySignals: ...
