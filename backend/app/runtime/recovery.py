"""同图 Task-boundary 恢复（迁自 travel_agent/agent/runtime/recovery.py）。

边界（审计 G14）：RecoveryService 只支持“同图同版本”的恢复（RUNNING→PENDING 重试）。
恢复 vN 与发布 vN+1（局部重规划）是不同操作；后者在 Phase F 实现。
"""

from ..domain import GraphExecutionState, TaskStatus
from .state_store import CheckpointNotFoundError


class RecoveryService:
    def __init__(self, store):
        self.store = store

    async def recover(self, session_id: str, graph):
        state = await self.store.load(session_id)
        if state is None:
            raise CheckpointNotFoundError(session_id)
        if state.graph.graph_id != graph.graph_id or state.graph.version != graph.version:
            raise ValueError("checkpoint graph identity/version mismatch")
        if state.current_status in {"succeeded", "failed"}:
            return state
        recovered = 0
        for task_id, status in state.task_statuses.items():
            if status is TaskStatus.RUNNING:
                state.task_statuses[task_id] = TaskStatus.PENDING
                state.metadata.setdefault("recovery", {}).setdefault("recovered_task_ids", []).append(task_id)
                recovered += 1
        state.metadata.setdefault("recovery", {})["recovery_attempts"] = (
            state.metadata.get("recovery", {}).get("recovery_attempts", 0) + 1
        )
        state.current_status = "pending"
        return state
