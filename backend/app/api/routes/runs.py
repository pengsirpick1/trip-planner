"""旅行 Run API：异步运行入口（Phase A 边界版）。

契约：
- POST   /api/travel/runs                       建 run（可选带首条消息）
- GET    /api/travel/runs/{run_id}              查 run（类型化 kind 判别）
- POST   /api/travel/runs/{run_id}/messages     追加消息
- GET    /api/travel/runs/{run_id}/events       事件列表（Phase G 转 SSE，带 seq 可续读）

响应中的 kind 判别器取值：clarification | answer | trip_plan | failed。
"""

from __future__ import annotations

from typing import Optional

import asyncio
import json
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...application import RunEvent, RunRecord
from ...application.travel_service import RunNotFoundError
from ...identity import AuthenticatedUser
from ..auth_dependencies import current_user
from ...errors import ResourceNotFoundError

router = APIRouter(prefix="/travel/runs", tags=["travel-runs"])


def _get_travel_app(request: Request):
    travel = getattr(request.app.state, "travel_app", None)
    if travel is None:
        from ...errors import DatabaseUnavailableError
        raise DatabaseUnavailableError()
    return travel


class CreateRunRequest(BaseModel):
    message: Optional[str] = Field(default=None, description="首条消息（可选）")
    session_id: Optional[str] = Field(default=None, description="会话标识（可选）")


class MessageRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户消息")

class AcceptedRun(BaseModel):
    run_id: str
    status: str = "accepted"


@router.post("", response_model=RunRecord, status_code=201, summary="创建一次运行")
async def create_run(payload: CreateRunRequest, request: Request, user: AuthenticatedUser | None = Depends(current_user)):
    """创建 run；若带 message 则立即处理并返回类型化结果。"""
    travel = _get_travel_app(request)
    run = await travel.create_run(session_id=payload.session_id, user_id=user.id if user else None)
    if payload.message:
        run = await travel.handle_message(run.run_id, payload.message, user_id=user.id if user else None)
    return run


@router.get("/{run_id}", response_model=RunRecord, summary="查询运行")
async def get_run(run_id: str, request: Request, user: AuthenticatedUser | None = Depends(current_user)):
    travel = _get_travel_app(request)
    try:
        return await travel.get_run(run_id, user_id=user.id if user else None)
    except RunNotFoundError:
        raise ResourceNotFoundError()


@router.post("/{run_id}/messages", response_model=RunRecord, summary="向运行追加消息")
async def send_message(run_id: str, payload: MessageRequest, request: Request, user: AuthenticatedUser | None = Depends(current_user)):
    travel = _get_travel_app(request)
    try:
        return await travel.handle_message(run_id, payload.message, user_id=user.id if user else None)
    except RunNotFoundError:
        raise ResourceNotFoundError()

@router.post("/{run_id}/messages/async", response_model=AcceptedRun, status_code=202, summary="后台执行消息")
async def submit_message(run_id: str, payload: MessageRequest, request: Request, user: AuthenticatedUser | None = Depends(current_user)):
    """Returns promptly; subscribe to /stream for durable progress events."""
    travel = _get_travel_app(request)
    try:
        await travel.submit_message(run_id, payload.message, user_id=user.id if user else None)
        return AcceptedRun(run_id=run_id)
    except RunNotFoundError:
        raise ResourceNotFoundError()

@router.get("/{run_id}/stream", summary="SSE execution events with seq resume")
async def stream_events(run_id: str, request: Request, after_seq: int = 0, user: AuthenticatedUser | None = Depends(current_user)):
    """Replay MySQL-backed events after ``after_seq`` then tail until completion.

    No per-client event queue is retained: slow/reconnected clients always
    re-read small persisted event records by sequence number.
    """
    travel = _get_travel_app(request)
    owner = user.id if user else None
    try:
        await travel.get_run(run_id, user_id=owner)
    except RunNotFoundError:
        raise ResourceNotFoundError()
    async def generate():
        cursor = max(0, after_seq)
        while True:
            if await request.is_disconnected(): return
            events = await travel.list_events(run_id, user_id=owner)
            for event in events:
                if event.seq <= cursor: continue
                cursor = event.seq
                payload = event.model_dump(mode="json")
                yield f"id: {cursor}\nevent: {event.event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            run = await travel.get_run(run_id, user_id=owner)
            if run.status.value in {"completed", "failed"}:
                yield f"event: stream_completed\ndata: {json.dumps({'run_id': run_id, 'status': run.status.value, 'last_seq': cursor})}\n\n"
                return
            # This is a bounded database tail, not an unbounded in-memory queue.
            await asyncio.sleep(0.5)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})


@router.get("/{run_id}/events", response_model=list[RunEvent], summary="查询运行事件")
async def list_events(run_id: str, request: Request, user: AuthenticatedUser | None = Depends(current_user)):
    travel = _get_travel_app(request)
    try:
        return await travel.list_events(run_id, user_id=user.id if user else None)
    except RunNotFoundError:
        raise ResourceNotFoundError()


@router.get("/{run_id}/trace", summary="查询 Agent 可验证执行轨迹")
async def get_agent_trace(run_id: str, request: Request, user: AuthenticatedUser | None = Depends(current_user)):
    travel = _get_travel_app(request)
    try:
        return await travel.get_agent_trace(run_id, user_id=user.id if user else None)
    except RunNotFoundError:
        raise ResourceNotFoundError()
