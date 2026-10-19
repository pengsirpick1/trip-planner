from dataclasses import dataclass

from ..executors.base import Executor
from .errors import MissingCapabilityError


@dataclass
class Registration:
    executor: Executor
    capabilities: set[str]
    agent_ids: set[str]


class ExecutorRegistry:
    def __init__(self):
        self._items: list[Registration] = []

    @property
    def capabilities(self) -> set[str]:
        caps: set[str] = set()
        for item in self._items:
            caps |= set(item.capabilities)
        return caps

    def register(self, executor, capabilities, *, agent_ids=None, prepend: bool = False):
        registration = Registration(executor, set(capabilities), set(agent_ids or []))
        if prepend:
            self._items.insert(0, registration)
        else:
            self._items.append(registration)

    def resolve(self, capabilities, assigned_agent: str | None = None):
        if assigned_agent:
            for item in self._items:
                if assigned_agent in item.agent_ids and set(capabilities) <= item.capabilities:
                    return item.executor
        for item in self._items:
            if set(capabilities) <= item.capabilities:
                return item.executor
        raise MissingCapabilityError(f"no executor supports capabilities: {sorted(capabilities)}")
