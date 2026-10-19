from typing import Protocol

from ..domain import ConversationState, IntentUpdate


class IntentParser(Protocol):
    async def parse(self, message: str, context: ConversationState | None = None) -> IntentUpdate: ...
