"""地图服务 API（Phase C：改接 GeoFacade → ToolRegistry → AMap MCP 只读工具）。

失败语义（审计 §9/§17）：
- 空查询结果 → HTTP 200 + success=True + data=[]（真实空结果，不是失败）
- 工具/Provider 故障 → HTTP 502 + success=False + error_code（不冒充空成功）
- AMap 未启用（无 AMAP_API_KEY / geo 未装配）→ HTTP 503 明确提示
不再使用会被空 {} 触发响应校验错误的强 response_model 包装。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter(prefix="/map", tags=["地图服务"])


def _geo(request: Request):
    geo = getattr(request.app.state, "geo", None)
    if geo is None:
        raise HTTPException(
            status_code=503,
            detail="AMap 地图工具未启用：请在 backend/.env 配置 AMAP_API_KEY（新链路不自动回退旧服务）",
        )
    return geo


class RoutePayload(BaseModel):
    origin_address: str
    destination_address: str
    origin_city: Optional[str] = None
    destination_city: Optional[str] = None
    route_type: str = "walking"


@router.get("/poi", summary="搜索POI")
async def search_poi(
    keywords: str = Query(..., description="搜索关键词", example="故宫"),
    city: str = Query(..., description="城市", example="北京"),
    citylimit: bool = Query(True, description="是否限制在城市范围内"),
    request: Request = None,
):
    result = await _geo(request).search_poi(keywords, city, citylimit)
    return _envelope(result)


@router.get("/weather", summary="查询天气")
async def get_weather(city: str = Query(..., description="城市", example="北京"), request: Request = None):
    result = await _geo(request).get_weather(city)
    return _envelope(result)


@router.post("/route", summary="规划路线")
async def plan_route(payload: RoutePayload, request: Request):
    result = await _geo(request).plan_route(
        origin_address=payload.origin_address,
        destination_address=payload.destination_address,
        origin_city=payload.origin_city,
        destination_city=payload.destination_city,
        route_type=payload.route_type,
    )
    return _envelope(result)


@router.get("/health", summary="健康检查")
async def health_check(request: Request):
    geo = getattr(request.app.state, "geo", None)
    if geo is None:
        raise HTTPException(status_code=503, detail="AMap 地图工具未启用")
    return {
        "status": "healthy",
        "service": "map-service",
        "provider": geo.provider,
        "enabled_tools": len(geo.allowed),
    }


def _envelope(result: dict):
    if not result.get("success"):
        raise HTTPException(
            status_code=502,
            detail=f"{result.get('message', '调用失败')}",
        )
    return {
        "success": True,
        "message": result.get("message"),
        "data": result.get("data"),
        "kind": "tool_result",
        "provider": result.get("provider"),
        "source": result.get("source"),
    }
