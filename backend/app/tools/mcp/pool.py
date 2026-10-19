"""MCP 工具连接池。

一个远端 Provider 的并发不能通过无限加连接解决：同一个 API key 仍共享配额。
本池让多个 stdio MCP 实例承载慢请求，同时以全局 QPS、共享缓存和请求合并守住
Provider 配额。每个租出的 adapter 一次只处理一个请求，避免假定 MCP server 线程安全。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from ..base import ToolResult, ToolSpec


class MCPToolPool:
    def __init__(self, adapters: list[Any], *, max_qps: float = 1.5,
                 cache_ttl_seconds: float = 600.0, cache_max_entries: int = 200,
                 no_cache_tools: Optional[set[str]] = None) -> None:
        if not adapters:
            raise ValueError("MCPToolPool 至少需要一个 adapter")
        if max_qps <= 0:
            raise ValueError("max_qps 必须大于 0")
        self.adapters = list(adapters)
        self.max_qps = float(max_qps)
        self.cache_ttl_seconds = float(cache_ttl_seconds)
        self.cache_max_entries = int(cache_max_entries)
        self.no_cache_tools = set(no_cache_tools or {"maps_weather"})
        self._available: asyncio.Queue[Any] = asyncio.Queue()
        self._rate_lock = asyncio.Lock()
        self._cache_lock = asyncio.Lock()
        self._next_slot = 0.0
        self._cache: dict[tuple[str, str], tuple[float, ToolResult]] = {}
        self._inflight: dict[tuple[str, str], asyncio.Future] = {}
        self._tools: dict[str, ToolSpec] = {}
        self._started = False

    @property
    def pool_size(self) -> int:
        return len(self.adapters)

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    async def start(self) -> list[ToolSpec]:
        if self._started:
            return list(self._tools.values())
        # 并行建立相互独立的 stdio MCP 子进程；任一失败立即关闭已经成功的实例。
        try:
            discovered = await asyncio.gather(*(adapter.start() for adapter in self.adapters))
        except Exception:
            await self.close()
            raise
        canonical: dict[str, ToolSpec] = {}
        for specs in discovered:
            for spec in specs:
                canonical.setdefault(spec.name, spec)
        self._tools = canonical
        while not self._available.empty():
            self._available.get_nowait()
        for adapter in self.adapters:
            self._available.put_nowait(adapter)
        self._started = True
        return list(self._tools.values())

    async def close(self) -> None:
        self._started = False
        await asyncio.gather(*(adapter.close() for adapter in self.adapters), return_exceptions=True)
        self._tools.clear()
        self._cache.clear()
        for future in self._inflight.values():
            if not future.done():
                future.set_exception(RuntimeError("MCP tool pool closed"))
        self._inflight.clear()
        while not self._available.empty():
            self._available.get_nowait()

    async def _reserve_rate_slot(self) -> None:
        """只锁定排期，不在锁中等待网络响应，允许不同连接重叠处理慢请求。"""
        async with self._rate_lock:
            now = time.monotonic()
            wait = max(0.0, self._next_slot - now)
            self._next_slot = max(now, self._next_slot) + 1.0 / self.max_qps
        if wait:
            await asyncio.sleep(wait)

    def _cache_get(self, key: tuple[str, str], tool_call_id: str) -> ToolResult | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires, result = entry
        if time.monotonic() > expires:
            self._cache.pop(key, None)
            return None
        cached = result.model_copy(deep=True)
        cached.tool_call_id = tool_call_id
        cached.metadata["cached"] = True
        cached.metadata["cache_layer"] = "mcp_pool"
        return cached

    async def execute(self, tool_name: str, arguments: dict[str, Any], context: dict) -> ToolResult:
        if not self._started:
            raise RuntimeError("MCP tool pool 未启动")
        if tool_name not in self._tools:
            return ToolResult(tool_call_id=context.get("tool_call_id", ""), tool_name=tool_name,
                              success=False, error="UNKNOWN_MCP_TOOL")
        key = (tool_name, json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False))
        cacheable = tool_name not in self.no_cache_tools
        leader = True
        future: asyncio.Future | None = None
        if cacheable:
            async with self._cache_lock:
                cached = self._cache_get(key, context.get("tool_call_id", ""))
                if cached is not None:
                    return cached
                future = self._inflight.get(key)
                if future is None:
                    future = asyncio.get_running_loop().create_future()
                    self._inflight[key] = future
                else:
                    leader = False
            if not leader:
                result = await future
                shared = result.model_copy(deep=True)
                shared.tool_call_id = context.get("tool_call_id", "")
                shared.metadata["request_coalesced"] = True
                return shared
        adapter = await self._available.get()
        try:
            await self._reserve_rate_slot()
            result = await adapter.execute(tool_name, arguments, context)
            if cacheable and result.success:
                async with self._cache_lock:
                    if len(self._cache) >= self.cache_max_entries:
                        self._cache.pop(next(iter(self._cache)), None)
                    self._cache[key] = (time.monotonic() + self.cache_ttl_seconds, result.model_copy(deep=True))
                    if future is not None and not future.done():
                        future.set_result(result.model_copy(deep=True))
            elif future is not None and not future.done():
                future.set_result(result.model_copy(deep=True))
            return result
        except Exception as error:
            if future is not None and not future.done():
                future.set_exception(error)
            raise
        finally:
            self._available.put_nowait(adapter)
            if cacheable:
                async with self._cache_lock:
                    self._inflight.pop(key, None)


class MCPPoolToolHandle:
    """将池中按名称路由的 MCP 工具注册到现有 ToolRegistry。"""

    def __init__(self, pool: MCPToolPool, spec: ToolSpec) -> None:
        self.pool = pool
        self.spec = spec

    async def execute(self, arguments: Any, context: dict) -> ToolResult:
        payload = arguments.model_dump() if hasattr(arguments, "model_dump") else arguments
        return await self.pool.execute(self.spec.name, payload or {}, context)
