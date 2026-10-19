"""旅行规划 API 路由（Phase A 兼容层）。

迁移策略（决策 D1+D5，双轨 + ENABLE_LEGACY_PLANNER）：
- enable_legacy_planner=True ：临时保留旧的固定四 Agent 实现（标记 deprecated，行为不变，
  包括其 fallback）；仅在显式开关下可触达。
- enable_legacy_planner=False（默认）：本端点转发到 TravelApplicationService，返回
  **类型化结果**（kind: trip_plan | clarification | answer | failed），任何路径都不得
  返回占位景点/虚构坐标/`success=True` 假行程。
- 健康检查不再实例化 Agent（旧实现访问不存在的 `agent.agent`，必现 503）。
"""

from __future__ import annotations

import asyncio
import warnings
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from ...config import get_settings
from ...models.schemas import TripRequest
from ...application import ResultKind, RunRecord
from ...identity import AuthenticatedUser
from ..auth_dependencies import current_user

router = APIRouter(prefix="/trip", tags=["旅行规划"])

_LEGACY_DEPRECATION = "该入口（ENABLE_LEGACY_PLANNER=true）已弃用，将由 /api/travel/runs 替代"


def _config_for(http_request: Request):
    """优先使用 create_app(settings) 注入的配置；兜底全局配置（兼容非工厂挂载）。"""
    state_settings = getattr(http_request.app.state, "settings", None)
    return state_settings if state_settings is not None else get_settings()


@router.post("/plan", summary="生成旅行计划（兼容入口）")
async def plan_trip(request: TripRequest, http_request: Request,
                    user: AuthenticatedUser | None = Depends(current_user)):
    """
    兼容 /api/trip/plan。

    - legacy=true → 旧四 Agent 实现（标记 deprecated）。
    - legacy=false → 转发 TravelApplicationService，返回 typed envelope：
      success/message/data + kind/error_code；失败为显式类型化失败。
    """
    cfg = _config_for(http_request)

    if cfg.enable_legacy_planner:
        return await _legacy_plan_trip(request)

    travel = getattr(http_request.app.state, "travel_app", None)
    if travel is None:
        raise HTTPException(status_code=503, detail="Travel engine 未装配")

    message = _trip_request_to_message(request)
    record = await travel.run_single_plan(
        message, session_id=f"trip:{request.city}:{request.start_date}",
        user_id=user.id if user else None,
    )
    return _typed_trip_response(record)


@router.get("/health", summary="健康检查")
async def health_check(http_request: Request):
    """健康检查：反映真实装配状态，不再实例化四 Agent。"""
    cfg = _config_for(http_request)
    travel = getattr(http_request.app.state, "travel_app", None)
    return {
        "status": "healthy",
        "service": "trip-planner",
        "travel_engine": travel is not None,
        "llm_configured": cfg.llm_configured,
        "legacy_enabled": cfg.enable_legacy_planner,
    }


# ============ 内部 ============

def _trip_request_to_message(request: TripRequest) -> str:
    parts = [
        f"请为我规划一次 {request.city} 的旅行。",
        f"日期：{request.start_date} 至 {request.end_date}，共 {request.travel_days} 天。",
        f"交通方式：{request.transportation}。住宿偏好：{request.accommodation}。",
    ]
    if request.preferences:
        parts.append("偏好：" + "、".join(request.preferences) + "。")
    if request.free_text_input:
        parts.append("补充要求：" + request.free_text_input)
    return "".join(parts)


def _typed_trip_response(record: RunRecord) -> Dict[str, Any]:
    """把类型化 RunRecord 转成 /api/trip/plan 兼容的 envelope。"""
    success = record.kind is ResultKind.TRIP_PLAN
    return {
        "success": success,
        "message": record.message or ("" if success else "生成旅行计划失败"),
        "data": record.data if success else None,
        "kind": record.kind.value if record.kind else None,
        "error_code": record.error_code,
    }


async def _legacy_plan_trip(request: TripRequest) -> Dict[str, Any]:
    """旧固定四 Agent 路径：仅 ENABLE_LEGACY_PLANNER=true 时使用。"""
    warnings.warn(_LEGACY_DEPRECATION, DeprecationWarning, stacklevel=2)

    def _run_sync() -> Any:
        # 延迟导入：默认（legacy=false）不加载 hello_agents 相关代码
        from ...agents.trip_planner_agent import get_trip_planner_agent

        agent = get_trip_planner_agent()
        return agent.plan_trip(request)

    try:
        trip_plan = await asyncio.to_thread(_run_sync)
        plan_data = trip_plan.model_dump(mode="json") if hasattr(trip_plan, "model_dump") else trip_plan
        return {
            "success": True,
            "message": "旅行计划生成成功（legacy，已弃用）",
            "data": plan_data,
            "kind": ResultKind.TRIP_PLAN.value,
            "error_code": None,
        }
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"生成旅行计划失败(legacy): {error}")
