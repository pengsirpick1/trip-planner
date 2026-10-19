"""地图/POI 门面：把 ToolRegistry（含 AMap MCP 只读工具）暴露给 /api/map、/api/poi。

设计（对齐审计失败语义）：
- 空查询结果（[]）≠ 解析失败 ≠ Provider 故障：分别给 success=True+[] / success=False(PARSE/UNKNOWN) / success=False(错误码)。
- 不再用会因空 {} 触发响应校验错误的强 response_model 包装（map.py 旧 bug）。
- 结果带 provider 与来源标注，绝不冒充其它源数据。
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from ..tools.base import ToolResult
from ..tools.registry import ToolRegistry


class GeoFacade:
    def __init__(
        self,
        registry: ToolRegistry,
        allowed_tools: Optional[set[str]] = None,
        *,
        provider: str = "amap",
    ):
        self.registry = registry
        self.allowed = set(allowed_tools) if allowed_tools is not None else set()
        self.provider = provider

    # ---- 通用调用 ----

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        tool_call_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if tool_name not in self.allowed:
            return {
                "success": False,
                "message": f"工具 {tool_name} 未启用",
                "data": None,
                "kind": "failed",
                "error_code": "TOOL_NOT_ENABLED",
                "provider": self.provider,
            }
        if not self.registry.tools:
            return {
                "success": False,
                "message": "地图工具未装配（AMAP_API_KEY 未配置或 MCP 未启动）",
                "data": None,
                "kind": "failed",
                "error_code": "MAP_TOOLS_UNAVAILABLE",
                "provider": self.provider,
            }
        context = {"tool_call_id": tool_call_id or f"geo-{uuid.uuid4().hex[:8]}"}
        result: ToolResult = await self.registry.call(tool_name, arguments, self.allowed, context)
        if not result.success:
            return {
                "success": False,
                "message": f"调用 {tool_name} 失败: {result.error}",
                "data": None,
                "kind": "failed",
                "error_code": (result.error or "TOOL_ERROR").split(":")[0].strip(),
                "provider": self.provider,
                "detail": result.error,
            }
        return {
            "success": True,
            "message": f"调用 {tool_name} 成功",
            "data": result.data,
            "kind": "tool_result",
            "error_code": None,
            "provider": self.provider,
            "source": result.source_references,
        }

    # ---- 业务方法（按能力命名，供路由复用） ----

    async def search_poi(
        self, keywords: str, city: str, citylimit: bool = True
    ) -> dict[str, Any]:
        return await self.call_tool(
            "maps_text_search",
            {"keywords": keywords, "city": city, "citylimit": str(citylimit).lower()},
        )

    async def get_weather(self, city: str) -> dict[str, Any]:
        return await self.call_tool("maps_weather", {"city": city})

    async def plan_route(
        self,
        origin_address: str,
        destination_address: str,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        route_type: str = "walking",
    ) -> dict[str, Any]:
        tool_map = {
            "walking": "maps_direction_walking_by_address",
            "driving": "maps_direction_driving_by_address",
            "transit": "maps_direction_transit_integrated_by_address",
        }
        tool_name = tool_map.get(route_type, "maps_direction_walking_by_address")
        arguments: dict[str, Any] = {
            "origin_address": origin_address,
            "destination_address": destination_address,
        }
        if origin_city:
            arguments["origin_city"] = origin_city
        if destination_city:
            arguments["destination_city"] = destination_city
        return await self.call_tool(tool_name, arguments)

    async def geocode(self, address: str, city: Optional[str] = None) -> dict[str, Any]:
        arguments: dict[str, Any] = {"address": address}
        if city:
            arguments["city"] = city
        return await self.call_tool("maps_geo", arguments)

    async def get_poi_detail(self, poi_id: str) -> dict[str, Any]:
        return await self.call_tool("maps_search_detail", {"id": poi_id})
