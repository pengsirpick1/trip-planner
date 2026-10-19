from typing import Protocol

from ..domain import Task, TaskResult


class ExecutionContext(Protocol):
    state: object
    dependency_results: dict[str, TaskResult]


class Executor(Protocol):
    executor_id: str

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult: ...
