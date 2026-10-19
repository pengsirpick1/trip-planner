"""DAG 运行时（迁自 travel_agent/agent/runtime/graph_runtime.py + Phase B 事件出口）。

调度语义（如实保留）：按 ready 批次 gather（快任务下游可能等待同批慢任务）；
每个任务完成后立即 checkpoint（_execute_and_save），不是等整批结束。
INVALIDATED 是终态，重规划必须走新版本图（Phase F）。
"""

import asyncio
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from ..domain import GraphExecutionState, Task, TaskResult, TaskStatus
from ..domain.budget import BudgetTracker
from .registry import ExecutorRegistry
from .state_store import StateStore
from .worker_pool import GlobalWorkerPool

log = logging.getLogger(__name__)

_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.READY, TaskStatus.SKIPPED, TaskStatus.INVALIDATED, TaskStatus.WAITING_FOR_USER},
    TaskStatus.READY: {TaskStatus.RUNNING, TaskStatus.SKIPPED, TaskStatus.INVALIDATED},
    TaskStatus.RUNNING: {TaskStatus.PENDING, TaskStatus.SUCCEEDED, TaskStatus.FAILED},
    TaskStatus.SUCCEEDED: {TaskStatus.INVALIDATED},
    TaskStatus.FAILED: {TaskStatus.INVALIDATED},
    TaskStatus.SKIPPED: {TaskStatus.INVALIDATED},
    TaskStatus.INVALIDATED: set(),
    TaskStatus.WAITING_FOR_USER: {TaskStatus.SUCCEEDED, TaskStatus.PENDING},
}

EventCallback = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class ExecutionOwnership:
    """Immutable tenant ownership carried through every checkpoint operation."""
    user_id: str
    run_id: str
    session_id: str


@dataclass(frozen=True)
class ExecutionContext:
    state: GraphExecutionState
    task_id: str | None = None
    budget: Any = None
    ownership: ExecutionOwnership | None = None

    @property
    def dependency_results(self):
        if not self.task_id:
            return {}
        task = self.state.graph.task(self.task_id)
        return {
            task_id: self.state.task_results[task_id]
            for task_id in task.dependencies
            if task_id in self.state.task_results
        }


class GraphRuntime:
    """运行经过校验的任务 DAG；依赖失败的下游任务转为 SKIPPED。"""

    def __init__(
        self,
        registry: ExecutorRegistry,
        state_store: StateStore | None = None,
        event_callback: EventCallback | None = None,
        worker_pool: GlobalWorkerPool | None = None,
    ):
        self.registry = registry
        self.store = state_store
        self.event_callback = event_callback
        self.worker_pool = worker_pool
        self._callback = ContextVar('runtime_callback', default=None)
        self._tracker = ContextVar('runtime_tracker', default=None)
        self._ownership = ContextVar('runtime_ownership', default=None)

    async def run(
        self,
        graph,
        session_id: str = "default",
        initial_state: GraphExecutionState | None = None,
        event_callback: EventCallback | None = None,
        budget=None,
        *,
        parallel_ready_nodes: bool = True,
        ownership: ExecutionOwnership | None = None,
    ) -> GraphExecutionState:
        """Execute a validated graph.

        ``parallel_ready_nodes`` defaults to the production scheduler semantics:
        every ready batch is awaited with ``asyncio.gather``.  The optional
        sequential mode exists for deterministic scheduler comparisons and
        diagnostics; it uses the same graph, task transitions, executors and
        checkpoint boundaries, only changing the execution order within a
        ready batch.
        """
        graph.validate_graph()
        state = initial_state or GraphExecutionState(
            session_id=session_id,
            graph=graph.model_copy(deep=True),
            task_statuses={t.id: TaskStatus.PENDING for t in graph.tasks},
            current_status="running",
        )
        state.current_status = "running"
        # Phase F：单次 run 的执行预算护栏（vN/vN+1 各自独立核算）
        tracker = BudgetTracker(budget) if budget is not None else None
        callback_token = self._callback.set(event_callback)
        tracker_token = self._tracker.set(tracker)
        ownership_token = self._ownership.set(ownership)
        try:
            self._emit("session_started", session_id=session_id)
            self._emit("graph_created", graph_id=graph.graph_id, graph_version=graph.version)
            await self._save(state)
            while True:
                if tracker is not None and tracker.exceeded:
                    state.metadata["budget_exceeded"] = tracker.exceeded_reason
                    self._emit("budget_exceeded", session_id=session_id, reason=tracker.exceeded_reason)
                    break
                self._skip_blocked_tasks(state)
                ready = self._ready_tasks(state)
                if not ready:
                    break
                approval = next((task for task in ready if task.task_type == "human_approval"), None)
                if approval is not None:
                    self._transition(state, approval.id, TaskStatus.WAITING_FOR_USER)
                    state.current_status = "waiting_for_user"
                    state.metadata["pending_approval"] = {"task_id": approval.id, "options": ["approve", "revise"]}
                    self._emit("human_approval_requested", task_id=approval.id, options=["approve", "revise"])
                    await self._save(state)
                    return state
                for task in ready:
                    self._transition(state, task.id, TaskStatus.READY)
                    self._transition(state, task.id, TaskStatus.RUNNING)
                    self._emit(
                        "task_started", task_id=task.id, goal=task.goal,
                        dependencies=task.dependencies,
                        capabilities=sorted(task.required_capabilities),
                    )
                await self._save(state)
                if parallel_ready_nodes:
                    # A graph cannot monopolise every global slot just because
                    # it has a wide ready layer.  Its profile provides a local
                    # budget while the shared pool provides cross-Run fairness.
                    per_run_limit = int((state.graph.metadata or {}).get("worker_concurrency_budget") or len(ready))
                    gate = asyncio.Semaphore(max(1, per_run_limit))
                    async def execute_limited(task):
                        async with gate:
                            await self._execute_and_save(task, state)
                    await asyncio.gather(*(execute_limited(task) for task in ready))
                else:
                    for task in ready:
                        await self._execute_and_save(task, state)
                await self._save(state)
            state.current_status = (
                "succeeded"
                if all(s is TaskStatus.SUCCEEDED for s in state.task_statuses.values())
                else "failed"
            )
            if tracker is not None:
                state.metadata["budget"] = tracker.snapshot()
            state.updated_at = datetime.now(UTC)
            self._emit("graph_completed", session_id=session_id, status=state.current_status)
            await self._save(state)
            return state
        finally:
            self._callback.reset(callback_token)
            self._tracker.reset(tracker_token)
            self._ownership.reset(ownership_token)

    async def resume(self, state: GraphExecutionState, *, ownership: ExecutionOwnership | None = None) -> GraphExecutionState:
        """同版本 checkpoint 经 RecoveryService 对账后恢复。"""
        if state.current_status in {"succeeded", "failed", "waiting_for_user"}:
            return state
        return await self.run(state.graph, state.session_id, initial_state=state, ownership=ownership)

    async def approve(self, state: GraphExecutionState, *, ownership: ExecutionOwnership | None = None) -> GraphExecutionState:
        """Resolve the explicit HumanApproval node and follow its approved edge."""
        pending = state.metadata.get("pending_approval") or {}
        task_id = pending.get("task_id")
        if state.current_status != "waiting_for_user" or not task_id:
            raise ValueError("graph is not waiting for human approval")
        self._transition(state, task_id, TaskStatus.SUCCEEDED)
        state.task_results[task_id] = TaskResult(task_id=task_id, success=True, output="approved by user", executor_id="human")
        state.metadata["approval_decision"] = "approved"
        state.metadata.pop("pending_approval", None)
        state.current_status = "pending"
        self._emit("conditional_edge_selected", source_task_id=task_id, condition="approved")
        return await self.run(state.graph, state.session_id, initial_state=state, ownership=ownership)

    def _ready_tasks(self, state):
        ready = [
            t
            for t in state.graph.tasks
            if state.task_statuses[t.id] is TaskStatus.PENDING
            and all(
                state.task_statuses[d] is TaskStatus.SUCCEEDED for d in t.dependencies
            )
        ]
        for task in ready:
            self._emit("task_ready", task_id=task.id)
        return ready

    def _skip_blocked_tasks(self, state):
        for task in state.graph.tasks:
            if state.task_statuses[task.id] is TaskStatus.PENDING and any(
                state.task_statuses[d] in {TaskStatus.FAILED, TaskStatus.SKIPPED, TaskStatus.INVALIDATED}
                for d in task.dependencies
            ):
                self._transition(state, task.id, TaskStatus.SKIPPED)
                state.task_results[task.id] = TaskResult(
                    task_id=task.id,
                    success=False,
                    error="skipped because a dependency did not succeed",
                )
                self._emit("task_skipped", task_id=task.id)

    async def _execute(self, task: Task, state) -> TaskResult:
        try:
            executor = self.registry.resolve(task.required_capabilities, task.assigned_agent)
            self._emit("executor_selected", task_id=task.id, executor_id=executor.executor_id, assigned_agent=task.assigned_agent)
            context = ExecutionContext(state, task.id, budget=self._tracker.get(), ownership=self._ownership.get())
            if self.worker_pool is None:
                result = await executor.execute(task, context)
            else:
                result = await self.worker_pool.submit(
                    run_id=state.session_id,
                    task_id=task.id,
                    capabilities=task.required_capabilities,
                    operation=lambda: executor.execute(task, context),
                    on_started=lambda worker_id: self._emit(
                        "worker_assigned", task_id=task.id, worker_id=worker_id,
                        capabilities=sorted(task.required_capabilities),
                    ),
                )
            if result.task_id != task.id:
                raise ValueError(
                    f"executor returned result for {result.task_id!r}, expected {task.id!r}"
                )
            return result
        except Exception as error:
            self._emit("task_failed_internal", task_id=task.id, error=str(error))
            return TaskResult(
                task_id=task.id,
                success=False,
                error=str(error),
                finished_at=datetime.now(UTC),
            )

    async def _execute_and_save(self, task, state):
        result = await self._execute(task, state)
        self._record_attempt(state, task, result)
        # 每个任务完成后立即持久化，即使并行对端被中断
        await self._save(state)

    def _record_attempt(self, state, task, result):
        state.task_attempts.setdefault(task.id, []).append(result)
        state.agent_local_states[task.id] = {"agent_id": task.assigned_agent, "task_id": task.id,
                                             "dependency_task_ids": list(task.dependencies),
                                             "observation_count": len((result.metadata or {}).get("tool_results") or []),
                                             "sources": list((result.metadata or {}).get("sources") or [])}
        state.task_results[task.id] = result
        if result.success:
            self._transition(state, task.id, TaskStatus.SUCCEEDED)
            self._emit("task_succeeded", task_id=task.id)
        elif len(state.task_attempts[task.id]) <= task.max_retries:
            self._transition(state, task.id, TaskStatus.PENDING)
            self._emit("task_retry", task_id=task.id, retry_count=len(state.task_attempts[task.id]))
        else:
            self._transition(state, task.id, TaskStatus.FAILED)
            self._emit(
                "task_failed",
                task_id=task.id,
                error=result.error,
                tool_errors=self._tool_errors(result),
            )

    @staticmethod
    def _tool_errors(result: TaskResult) -> list[str]:
        """把任务失败时真实工具级错误带回 run 事件（避免只看到笼统的 max steps/executor error）。"""
        metadata = (result.metadata or {}).get("tool_results") or []
        errors = []
        for item in metadata:
            if isinstance(item, dict) and not item.get("success"):
                text = item.get("error") or ""
                if text:
                    errors.append(f"{item.get('tool_name')}: {text[:160]}")
            if len(errors) >= 5:
                break
        return errors

    def _transition(self, state, task_id, next_status):
        current = state.task_statuses[task_id]
        if next_status not in _TRANSITIONS[current]:
            raise RuntimeError(f"invalid task transition: {current.value} -> {next_status.value}")
        state.task_statuses[task_id] = next_status
        state.updated_at = datetime.now(UTC)

    async def _save(self, state):
        if self.store:
            ownership = self._ownership.get()
            if ownership is None:
                await self.store.save(state)
            else:
                await self.store.save(state, user_id=ownership.user_id, run_id=ownership.run_id)
            self._emit("state_saved", session_id=state.session_id)

    def _emit(self, event: str, **fields: Any) -> None:
        log.info(event, extra={"event": event, **fields})
        callback = self._callback.get() or self.event_callback
        if callback is not None:
            callback(event, fields)
