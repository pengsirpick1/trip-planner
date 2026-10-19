"""AMap MCP 工具映射（Phase C）。

- server_command: uvx amap-mcp-server（版本建议固定，Phase H 收敛到锁文件）
- 环境变量：AMAP_MAPS_API_KEY ← settings.amap_api_key（只传给 MCP 子进程，不进 VITE_*）
- capability 映射：景点/POI 研究 → place_research；天气 → weather_research；
  地理编码/路线 → geospatial_reasoning。
- 坐标体系注意（审计 §8）：AMap 使用 GCJ-02；Open-Meteo 返回 WGS-84。
  本模块不做自动转换；跨源混用必须显式标注坐标系。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

from ...config import Settings
from .adapter import MCPToolAdapter
from .client import StdioMCPClient
from .pool import MCPToolPool

# AMap MCP 工具 → capability（能力白名单；未列出的工具绝不注册）
AMAP_CAPABILITY_MAP: dict[str, dict[str, Any]] = {
    "maps_text_search": {"capabilities": ["place_research", "accommodation_research"], "timeout_seconds": 15.0},
    "maps_search_detail": {"capabilities": ["place_research", "accommodation_research"], "timeout_seconds": 15.0},
    "maps_weather": {"capability": "weather_research", "timeout_seconds": 15.0},
    "maps_geo": {"capability": "geospatial_reasoning", "timeout_seconds": 15.0},
    "maps_direction_walking_by_address": {
        "capabilities": ["geospatial_reasoning", "transport_research", "accommodation_research"],
        "timeout_seconds": 20.0,
    },
    "maps_direction_driving_by_address": {
        "capabilities": ["geospatial_reasoning", "transport_research", "accommodation_research"],
        "timeout_seconds": 20.0,
    },
    "maps_direction_transit_integrated_by_address": {
        "capabilities": ["geospatial_reasoning", "transport_research", "accommodation_research"],
        "timeout_seconds": 25.0,
    },
}

AMAP_TOOL_CAPABILITIES = set().union(
    *(set(meta.get("capabilities") or [meta["capability"]]) for meta in AMAP_CAPABILITY_MAP.values())
)


class AmapConfigError(RuntimeError):
    pass


def build_amap_env(settings: Settings) -> dict[str, str]:
    """MCP 子进程环境 = 当前环境 + AMap key + 可写的项目级 uv 缓存。"""
    if not settings.amap_api_key:
        raise AmapConfigError("AMAP_API_KEY 未配置（写入 backend/.env），无法启动 AMap MCP")
    env = dict(os.environ)
    env["AMAP_MAPS_API_KEY"] = settings.amap_api_key
    # Windows 用户级 uv cache 可能被权限策略锁定；独立缓存避免 MCP 因启动器缓存失败退出。
    cache_dir = Path(settings.resolve_db_path()).parent / ".uv-mcp-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    env["UV_CACHE_DIR"] = str(cache_dir)
    python_dir = Path(settings.resolve_db_path()).parent / ".uv-mcp-python"
    python_dir.mkdir(parents=True, exist_ok=True)
    env["UV_PYTHON_INSTALL_DIR"] = str(python_dir)
    tool_dir = Path(settings.resolve_db_path()).parent / ".uv-mcp-tools"
    tool_dir.mkdir(parents=True, exist_ok=True)
    env["UV_TOOL_DIR"] = str(tool_dir)
    # 当前服务已由一个可用解释器运行；显式指定它，避免 uv 扫描无权限的用户 PATH 项。
    env["UV_PYTHON"] = os.environ.get("UV_PYTHON") or sys.executable
    return env


def build_amap_adapter(
    settings: Settings,
    *,
    server_command: Optional[list[str]] = None,
    allowed: Optional[dict[str, dict[str, Any]]] = None,
) -> MCPToolAdapter:
    """构建 AMap MCPToolAdapter（未连接；连接发生在 adapter.start()）。"""
    env = build_amap_env(settings)
    client = StdioMCPClient(
        server_command or ["uvx", "amap-mcp-server"],
        env=env,
        request_timeout_seconds=40.0,
        startup_timeout_seconds=60.0,
    )
    return MCPToolAdapter(
        client,
        allowed=allowed if allowed is not None else dict(AMAP_CAPABILITY_MAP),
        server_label="amap",
        default_description="Read-only AMap MCP tool",
    )


def build_amap_pool(
    settings: Settings,
    *,
    server_command: Optional[list[str]] = None,
    allowed: Optional[dict[str, dict[str, Any]]] = None,
    adapter_factory=build_amap_adapter,
) -> MCPToolPool:
    """创建多个独立 MCP 连接，但以全局配额约束同一高德 Key。"""
    pool_size = max(1, min(int(settings.amap_mcp_pool_size), 4))
    adapters: list[MCPToolAdapter] = []
    seen: set[int] = set()
    for _ in range(pool_size):
        adapter = adapter_factory(settings, server_command=server_command, allowed=allowed)
        # 注入测试替身时可能每次返回同一实例；不能让同一连接被同时租出。
        if id(adapter) in seen:
            break
        seen.add(id(adapter))
        # 池统一做 QPS 和缓存；adapter 仍用它自身的锁保护单个 stdio 连接。
        adapter.max_qps = 1_000_000.0
        adapters.append(adapter)
    return MCPToolPool(adapters, max_qps=settings.amap_max_qps)
