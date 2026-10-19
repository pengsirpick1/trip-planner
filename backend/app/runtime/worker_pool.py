"""Bounded, fair worker slots for graph task execution.

The graph remains the source of task dependencies.  This pool only decides
*when* an already-ready task may consume local process capacity.  It is shared
by all GraphRuntime instances in one application process, so two independent
conversations cannot create an unbounded number of ReAct coroutines.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass
class WorkerSlot:
    worker_id: str
    capabilities: frozenset[str]


@dataclass
class _QueuedTask:
    run_id: str
    task_id: str
    capabilities: frozenset[str]
    operation: Callable[[], Awaitable[T]]
    future: asyncio.Future[T]
    context: contextvars.Context
    on_started: Callable[[str], None] | None = None


class GlobalWorkerPool:
    """A bounded, round-robin queue of capability-aware reusable worker slots.

    Slots are deliberately process-local: this project is a local To-C
    deployment.  Durable state remains in MySQL/checkpoints, therefore a pool
    failure affects in-flight work but never becomes the source of truth.
    """

    def __init__(self, slots: list[WorkerSlot], *, max_queue_size: int = 64):
        if not slots:
            raise ValueError("at least one worker slot is required")
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be positive")
        self.slots = slots
        self.max_queue_size = max_queue_size
        self._capacity = asyncio.Semaphore(max_queue_size)
        self._condition = asyncio.Condition()
        self._by_run: OrderedDict[str, deque[_QueuedTask]] = OrderedDict()
        self._workers: list[asyncio.Task[None]] = []
        self._closed = False

    @classmethod
    def homogeneous(cls, *, size: int, capabilities: set[str], max_queue_size: int = 64) -> "GlobalWorkerPool":
        if size < 1:
            raise ValueError("worker pool size must be positive")
        supported = frozenset(capabilities)
        return cls([WorkerSlot(f"local-worker-{index + 1}", supported) for index in range(size)], max_queue_size=max_queue_size)

    async def start(self) -> None:
        if self._workers:
            return
        if self._closed:
            raise RuntimeError("worker pool is closed")
        self._workers = [asyncio.create_task(self._worker(slot), name=f"agent-{slot.worker_id}") for slot in self.slots]

    def add_capabilities(self, capabilities: set[str]) -> None:
        """Make newly registered startup capabilities available to generic slots."""
        added = frozenset(capabilities)
        for slot in self.slots:
            slot.capabilities = slot.capabilities | added

    async def close(self) -> None:
        self._closed = True
        async with self._condition:
            for queue in self._by_run.values():
                while queue:
                    queued = queue.popleft()
                    if not queued.future.done():
                        queued.future.set_exception(RuntimeError("worker pool stopped before task execution"))
                    self._capacity.release()
            self._by_run.clear()
            self._condition.notify_all()
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def submit(
        self,
        *,
        run_id: str,
        task_id: str,
        capabilities: set[str],
        operation: Callable[[], Awaitable[T]],
        on_started: Callable[[str], None] | None = None,
    ) -> T:
        """Queue one ready node and wait for a compatible slot.

        Capacity is acquired before insertion, providing backpressure instead
        of accepting unlimited work during a local burst.
        """
        requested = frozenset(capabilities)
        if not any(requested <= slot.capabilities for slot in self.slots):
            raise ValueError(f"no worker slot supports capabilities: {sorted(requested)}")
        await self._capacity.acquire()
        try:
            await self.start()
            loop = asyncio.get_running_loop()
            queued = _QueuedTask(run_id, task_id, requested, operation, loop.create_future(), contextvars.copy_context(), on_started)
            async with self._condition:
                if self._closed:
                    raise RuntimeError("worker pool is closed")
                self._by_run.setdefault(run_id, deque()).append(queued)
                self._condition.notify_all()
            return await queued.future
        except BaseException:
            # A task removed by a worker releases capacity there.  This path is
            # only for failed admission before the queue owns the permit.
            if 'queued' not in locals():
                self._capacity.release()
            raise

    async def _worker(self, slot: WorkerSlot) -> None:
        while True:
            queued = await self._take(slot)
            if queued is None:
                return
            self._capacity.release()
            if queued.future.cancelled():
                continue
            try:
                if queued.on_started:
                    queued.context.run(queued.on_started, slot.worker_id)
                # ContextVars such as event sink and LLM call attribution belong
                # to the submitting Run, never to this long-lived worker task.
                child = queued.context.run(asyncio.create_task, queued.operation())
                result = await child
            except BaseException as error:
                if not queued.future.done():
                    queued.future.set_exception(error)
            else:
                if not queued.future.done():
                    queued.future.set_result(result)

    async def _take(self, slot: WorkerSlot) -> _QueuedTask | None:
        async with self._condition:
            while True:
                if self._closed:
                    return None
                for run_id in list(self._by_run):
                    queue = self._by_run[run_id]
                    match_index = next((i for i, item in enumerate(queue) if item.capabilities <= slot.capabilities), None)
                    if match_index is None:
                        continue
                    queued = queue[match_index]
                    del queue[match_index]
                    # Round-robin across independent conversations.  A busy run
                    # moves to the back after it receives one worker slot.
                    self._by_run.move_to_end(run_id)
                    if not queue:
                        del self._by_run[run_id]
                    return queued
                await self._condition.wait()
