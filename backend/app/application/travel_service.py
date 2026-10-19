"""旅行 Agent 应用服务（Phase A 边界版）。

职责边界（审计 §3/§14）：
- 本服务是 API → 应用层的唯一入口，保存 Run/事件，产出**类型化结果**（discriminator：
  clarification | answer | trip_plan | failed），不再使用 `success=True` 假成功包装。
- Phase A 尚不迁移 M1~M5 核心（Phase B 迁入），因此没有注入处理器时，
  消息会返回显式 failed（INTENT_ENGINE_NOT_READY），**绝不伪造澄清/行程**。
- Phase B 起注入 intent_handler/routing/planning 处理器，把真实阶段接到本服务。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field

from ..config import Settings
from .events import event_sink


class RunStatus(str, Enum):
    """一个 run 的生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ResultKind(str, Enum):
    """类型化结果判别器：clarification | answer | trip_plan | failed。"""

    CLARIFICATION = "clarification"
    ANSWER = "answer"
    TRIP_PLAN = "trip_plan"
    FAILED = "failed"


class RunEvent(BaseModel):
    """run 事件（后续 Phase G 直接供给 SSE /events）。"""

    seq: int
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunRecord(BaseModel):
    """一次 run 的持久化记录。"""

    run_id: str
    # 仅用于服务端的归属校验，不暴露在旅行结果 API 中。
    user_id: Optional[str] = Field(default=None, exclude=True)
    session_id: Optional[str] = None
    status: RunStatus = RunStatus.PENDING
    kind: Optional[ResultKind] = None
    message: str = ""
    error_code: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    events: list[RunEvent] = Field(default_factory=list)
    conversation: Optional[dict[str, Any]] = Field(default=None, exclude=True)


@dataclass
class StageOutcome:
    """一次阶段处理的结果（应用层内部的统一返回载体）。"""

    kind: ResultKind
    message: str = ""
    data: Optional[dict[str, Any]] = None
    questions: list[dict[str, Any]] = field(default_factory=list)
    error_code: Optional[str] = None
    assumptions: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


# 处理器契约：Phase B 迁入 IntentService 等真实阶段后，用一个适配器实现它。
class MessageProcessor(Protocol):
    async def process(self, message: str, run: RunRecord) -> StageOutcome: ...


class RunRepository(Protocol):
    async def create(self, record: RunRecord) -> None: ...
    async def update(self, record: RunRecord) -> None: ...
    async def get(self, run_id: str) -> Optional[RunRecord]: ...
    async def list_events(self, run_id: str) -> list[RunEvent]: ...


class RunNotFoundError(LookupError):
    pass


class TravelApplicationService:
    """旅行 Agent 应用服务：API 只与它对话，不直接触碰 Agent 内部。"""

    def __init__(
        self,
        repository: RunRepository,
        *,
        settings: Optional[Settings] = None,
        processor: Optional[MessageProcessor] = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.processor = processor
        self._starters: list[Any] = []
        self._closers: list[Any] = []
        # 由 bootstrap 附加的可选能力句柄（如 AMap MCP adapter），供路由层调用
        self.geo = None
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._background_tasks: set[asyncio.Task] = set()

    def register_lifecycle(self, *, start=None, close=None, geo=None) -> None:
        """bootstrap 注入异步 start/close 回调与地图能力句柄。"""
        if start is not None:
            self._starters.append(start)
        if close is not None:
            self._closers.append(close)
        if geo is not None:
            self.geo = geo

    # ---- 生命周期 ----

    async def start(self) -> None:
        """启动需要异步连接的组件（如 AMap MCP）；失败即显式报错，不静默降级。"""
        for starter in self._starters:
            await starter()

    async def close(self) -> None:
        """关闭底层资源（Provider/MCP 句柄）。"""
        # Graceful shutdown waits for accepted Agent runs instead of silently
        # cancelling work just because a browser disconnected.
        if self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)
        for closer in reversed(self._closers):
            await closer()
        closer = getattr(self.repository, "close", None)
        if closer is not None:
            result = closer()
            if hasattr(result, "__await__"):
                await result

    # ---- 查询 ----

    async def get_run(self, run_id: str, *, user_id: Optional[str] = None) -> RunRecord:
        cache = getattr(self, "context_cache", None)
        key = f"agent:run:{user_id}:{run_id}" if user_id else None
        if cache is not None and key:
            cached = await cache.get_json(key)
            if cached is not None:
                return RunRecord.model_validate(cached)
        # MySQL production repository intentionally exposes only this scoped lookup.
        if hasattr(self.repository, "get_for_user"):
            record = await self.repository.get_for_user(user_id, run_id) if user_id else None
        else:
            record = await self.repository.get(run_id)
        # 以 404 处理跨用户访问，避免泄露 run 是否存在。
        if record is None or (user_id is not None and record.user_id != user_id):
            raise RunNotFoundError(run_id)
        if cache is not None and key:
            await cache.set_json(key, record.model_dump(mode="json"), getattr(self, "context_cache_ttl", 120))
        return record

    async def list_events(self, run_id: str, *, user_id: Optional[str] = None) -> list[RunEvent]:
        await self.get_run(run_id, user_id=user_id)  # 校验 run 存在/归属
        if hasattr(self.repository, "list_events_for_user"):
            return await self.repository.list_events_for_user(user_id, run_id) if user_id else []
        return await self.repository.list_events(run_id)

    async def get_agent_trace(self, run_id: str, *, user_id: Optional[str] = None) -> dict[str, Any]:
        """返回可展示、已脱敏的 Agent 执行记录，不包含模型推理或工具原始内容。"""
        run = await self.get_run(run_id, user_id=user_id)
        events = await self.list_events(run_id, user_id=user_id)
        route = next((e.payload for e in events if e.event == "route_selected"), None)
        state = None
        runtime = getattr(self.processor, "runtime", None)
        store = getattr(runtime, "store", None)
        if store is not None:
            state = await store.load(run_id, user_id=user_id)

        graph = None
        if state is not None:
            tasks = []
            for task in state.graph.tasks:
                result = state.task_results.get(task.id)
                metadata = result.metadata if result else {}
                tool_summary = []
                for item in (metadata.get("tool_results") or []):
                    if not isinstance(item, dict):
                        continue
                    tool_summary.append({
                        "tool_name": item.get("tool_name") or item.get("name") or "工具调用",
                        "success": bool(item.get("success")),
                        "source": item.get("source") or item.get("provider"),
                        "latency_ms": item.get("latency_ms"),
                        "error": (item.get("error") or "")[:240] or None,
                    })
                tasks.append({
                    "id": task.id, "goal": task.goal, "dependencies": task.dependencies,
                    "capabilities": sorted(task.required_capabilities),
                    "assigned_agent": task.assigned_agent,
                    "agent_local_state": state.agent_local_states.get(task.id),
                    "status": state.task_statuses.get(task.id).value if task.id in state.task_statuses else "pending",
                    "executor_id": result.executor_id if result else None,
                    "tool_call_count": len(tool_summary), "tool_summary": tool_summary,
                    "metrics": metadata.get("metrics") or None,
                    "sources": list(dict.fromkeys(metadata.get("sources") or []))[:12],
                    "error": (result.error[:240] if result and result.error else None),
                })
            # Graph Compiler 的决策属于可验证执行元数据。把它随 Trace 返回，
            # 让前端能够区分「实际运行的 Agent」和「在编译阶段被裁剪的候选节点」。
            graph_metadata = state.graph.metadata or {}
            graph = {
                "graph_id": state.graph.graph_id,
                "version": state.graph.version,
                "status": state.current_status,
                "tasks": tasks,
                "execution_profile": graph_metadata.get("execution_profile"),
                "max_research_agents": graph_metadata.get("max_research_agents"),
                "research_task_budget": graph_metadata.get("research_task_budget", graph_metadata.get("max_research_agents")),
                "worker_concurrency_budget": graph_metadata.get("worker_concurrency_budget"),
                "graph_compiler": graph_metadata.get("graph_compiler"),
                "pruned_task_ids": graph_metadata.get("pruned_task_ids", []),
                "public_transit_supported": graph_metadata.get("public_transit_supported"),
                # 这是配置的连接池容量与全局限流，不等同于本轮真实并发数。
                "tool_pool": (
                    {
                        "provider": "amap_mcp",
                        "pool_size": self.settings.amap_mcp_pool_size,
                        "max_qps": self.settings.amap_max_qps,
                    }
                    if self.settings is not None
                    else None
                ),
            }
        return {
            "run_id": run_id, "status": run.status.value, "route": route, "graph": graph,
            "evaluation": (run.data or {}).get("evaluation"),
            "timeline": [e.model_dump(mode="json") for e in events
                         if e.event not in {"state_saved", "session_started", "graph_created"}],
        }

    # ---- 写入 ----

    async def create_run(self, session_id: Optional[str] = None, *, user_id: Optional[str] = None) -> RunRecord:
        if hasattr(self.repository, "get_for_user") and not user_id:
            raise PermissionError("authenticated user is required for MySQL runs")
        record = RunRecord(
            run_id=uuid.uuid4().hex[:16],
            user_id=user_id,
            session_id=session_id,
            status=RunStatus.PENDING,
        )
        record = self._append_event(record, "run_created", {"session_id": session_id, "authenticated": user_id is not None})
        await self.repository.create(record)
        return record

    async def handle_message(self, run_id: str, message: str, *, user_id: Optional[str] = None) -> RunRecord:
        cache = getattr(self, "context_cache", None)
        if cache is not None and user_id:
            await cache.invalidate(f"agent:run:{user_id}:{run_id}")
        lock = self._run_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            return await self._handle_message(run_id, message, user_id=user_id)

    async def submit_message(self, run_id: str, message: str, *, user_id: Optional[str] = None) -> RunRecord:
        """Accept a durable run and execute it independently of the HTTP request."""
        run = await self.get_run(run_id, user_id=user_id)
        task = asyncio.create_task(self.handle_message(run_id, message, user_id=user_id), name=f"travel-run:{run_id}")
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return run

    async def _handle_message(self, run_id: str, message: str, *, user_id: Optional[str] = None) -> RunRecord:
        """处理一条用户消息，更新 run 状态并持久化。"""
        cache = getattr(self, "context_cache", None)
        run = await self.get_run(run_id, user_id=user_id)
        run = self._append_event(run, "message_received", {"length": len(message)})
        run.status = RunStatus.RUNNING
        run.updated_at = datetime.now(UTC)
        await self.repository.update(run)

        queue = asyncio.Queue()
        published = []

        async def persist_events():
            while True:
                event = await queue.get()
                if event is None:
                    return
                published.append(event)
                updated = self._append_event(run, event.get('event', 'stage_event'), event.get('payload', {}))
                run.events = updated.events
                await self.repository.update(run)

        worker = asyncio.create_task(persist_events())
        token = event_sink.set(queue.put_nowait)
        try:
            outcome = await self._process(message, run)
        except Exception as error:  # Provider/内部异常 → 类型化失败，绝不假成功
            from ..quota import QuotaInsufficient, QuotaReconciliationRequired
            if isinstance(error, QuotaInsufficient):
                outcome = StageOutcome(kind=ResultKind.FAILED, message="当前可用额度不足，无法开始本次规划。", error_code="QUOTA_INSUFFICIENT")
            elif isinstance(error, QuotaReconciliationRequired):
                outcome = StageOutcome(kind=ResultKind.FAILED, message="上一次模型调用的账务结果待核对，请稍后重试。", error_code="QUOTA_RECONCILIATION_REQUIRED")
            else:
                outcome = StageOutcome(
                    kind=ResultKind.FAILED,
                    message=f"处理失败：{type(error).__name__}: {error}",
                    error_code="INTERNAL_ERROR",
                    data={'diagnostics': [error.diagnostic]} if getattr(error, 'diagnostic', None) else None,
                )
        finally:
            event_sink.reset(token)
            queue.put_nowait(None)
            await worker

        run.status = RunStatus.COMPLETED if outcome.kind is not ResultKind.FAILED else RunStatus.FAILED
        run.kind = outcome.kind
        run.message = outcome.message
        run.error_code = outcome.error_code
        if outcome.questions:
            # 澄清问题随 run 返回（客户端据此渲染提问表单）
            run.data = {"questions": outcome.questions, "assumptions": outcome.assumptions}
        else:
            run.data = outcome.data
        # Persist the accepted plan separately from the transient response.  A
        # later question can therefore use the same plan even after `run.data`
        # has been replaced by an explanatory answer or clarification payload.
        if outcome.kind is ResultKind.TRIP_PLAN and isinstance(outcome.data, dict):
            conversation = dict(run.conversation or {})
            conversation["active_plan"] = outcome.data
            conversation["plan_revision"] = int(outcome.data.get("plan_revision") or 1)
            run.conversation = conversation
        run.updated_at = datetime.now(UTC)
        # 阶段产生的过程事件（task_started/task_completed/...）先落库
        for event in ([] if published else outcome.events):
            run = self._append_event(
                run, event.get("event", "stage_event"), event.get("payload", {})
            )
        run = self._append_event(
            run,
            "stage_completed",
            {
                "kind": outcome.kind.value,
                "error_code": outcome.error_code,
                "question_count": len(outcome.questions),
            },
        )
        await self.repository.update(run)
        # _handle_message reads the existing record before applying the
        # mutation.  That read may refill the disposable Redis snapshot, so a
        # pre-write invalidation alone is insufficient.  Clear it again only
        # after the authoritative repository write succeeds.
        if cache is not None and user_id:
            await cache.invalidate(f"agent:run:{user_id}:{run_id}")
        return run

    async def run_single_plan(
        self,
        message: str,
        *,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> RunRecord:
        """同步兼容入口（/api/trip/plan 转发用）：建 run → 处理 → 返回结果。"""
        run = await self.create_run(session_id=session_id, user_id=user_id)
        return await self.handle_message(run.run_id, message, user_id=user_id)

    # ---- 内部 ----

    async def _process(self, message: str, run: RunRecord) -> StageOutcome:
        if self.processor is None:
            return StageOutcome(
                kind=ResultKind.FAILED,
                message=(
                    "行程规划引擎尚未接入（迁移 Phase B 中）。"
                    "该请求没有被伪造的占位行程或假数据替代。"
                ),
                error_code="INTENT_ENGINE_NOT_READY",
            )
        return await self.processor.process(message, run)

    @staticmethod
    def _append_event(run: RunRecord, event: str, payload: dict[str, Any]) -> RunRecord:
        events = list(run.events)
        events.append(
            RunEvent(
                seq=len(events) + 1,
                event=event,
                payload=payload,
            )
        )
        return run.model_copy(update={"events": events})


# ---- 供未来阶段导入的处理器契约占位 ----
Processor = MessageProcessor
