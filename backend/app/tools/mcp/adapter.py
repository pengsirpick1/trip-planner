"""MCPToolAdapter：把 MCP server 发现的工具接入 ToolRegistry（审计 §9 Option B）。

职责：
- 工具发现后按白名单注册，绝不自动开放全部 MCP tools；
- 白名单工具默认只读（side_effect_level=READ_ONLY），sensitive 一律不暴露；
- 解析 content / isError / JSON-RPC error，区分：空查询结果 vs 解析失败 vs Provider 故障；
- ToolResult 保留结构化 data 与 source_references（来源=mcp://<server>/<tool>）；
- 统一连接生命周期（start/close）与超时。
"""

from __future__ import annotations

import json
import asyncio
import time
import logging
from datetime import UTC, datetime
from typing import Any, Optional

from ..base import SideEffectLevel, ToolResult, ToolSpec
from ..diagnostics import diagnose, redact

log = logging.getLogger(__name__)


class MCPToolAdapter:
    """包住一个 StdioMCPClient 的、按白名单暴露只读工具的工具对象集合。"""

    def __init__(
        self,
        client,
        *,
        allowed: dict[str, dict[str, Any]],
        server_label: str = "mcp",
        default_description: str = "Read-only MCP tool",
    ):
        self.client = client
        self.server_label = server_label
        self.allowed = allowed  # mcp_tool_name -> {capability: str}
        self.default_description = default_description
        self._tools: dict[str, ToolSpec] = {}
        self._started = False
        self._call_lock = asyncio.Lock()
        # 方案一：客户端限流（QPS）+ 结果缓存 + 限流退避
        self.max_qps: float = 1.5          # ≤1.5 请求/秒，远离 CUQPS 触发线
        self.rate_retries: int = 2          # 限流最多额外重试 2 次
        self.rate_backoff_base: float = 1.0 # 1s, 2s
        self.cache_ttl_seconds: float = 600.0
        self.cache_max_entries: int = 200
        self._next_slot: float = 0.0
        self._cache: dict[tuple[str, str], tuple[float, ToolResult]] = {}
        # 不缓存的结果易变的只读工具（如天气：10 分钟内可能变化，命中旧值会误导）
        self._no_cache_tools: set[str] = {"maps_weather"}

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    # ---- 生命周期 ----

    async def start(self) -> list[ToolSpec]:
        """连接并发现工具；只注册白名单命中的工具。"""
        if self._started:
            return list(self._tools.values())
        await self.client.start()
        discovered = {tool.name: tool for tool in await self.client.list_tools()}
        self._tools = {}
        missing = []
        for name, meta in self.allowed.items():
            info = discovered.get(name)
            if info is None:
                missing.append(name)
                continue
            capability = meta.get("capabilities", meta.get("capability", name))
            capabilities = {capability} if isinstance(capability, str) else set(capability)
            description = info.description or self.default_description
            self._tools[name] = ToolSpec(
                name=name,
                description=description,
                capabilities=capabilities,
                input_model=None,
                input_schema=dict(info.input_schema or {}),
                idempotent=True,
                side_effect_level=SideEffectLevel.READ_ONLY,
                timeout_seconds=max(60.0, float(meta.get("timeout_seconds", 20.0))),
            )
        if missing:
            log.warning(
                "MCP server (%s) 未发现白名单工具: %s；已注册 %d 个",
                self.server_label,
                sorted(missing),
                len(self._tools),
            )
        self._started = True
        return list(self._tools.values())

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    # ---- 工具注册（给 ToolRegistry） ----

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    async def execute(self, tool_name: str, arguments: dict[str, Any], context: dict) -> ToolResult:
        # 全 adapter 串行执行：节流 + 缓存 + 退避都在同一把锁内，杜绝并发突发
        async with self._call_lock:
            cache_key = (tool_name, json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False))
            cached = None if tool_name in self._no_cache_tools else self._cache_get(cache_key)
            if cached is not None:
                return cached
            attempts = 0
            retry_history: list[dict[str, Any]] = []
            while True:
                await self._throttle()
                result = await self._execute_once(tool_name, arguments, context)
                diagnostic = result.metadata.get('diagnostic', {})
                if (
                    not result.success
                    and diagnostic.get('category') == 'rate_limit'
                    and attempts < self.rate_retries
                ):
                    attempts += 1
                    retry_history.append(diagnostic)
                    await asyncio.sleep(self.rate_backoff_base * (2 ** (attempts - 1)))
                    continue
                if attempts:
                    result.metadata['attempts'] = attempts + 1
                if retry_history:
                    result.metadata['retry_errors'] = retry_history
                if result.success and tool_name not in self._no_cache_tools:
                    self._cache_put(cache_key, result)
                return result

    async def _throttle(self) -> None:
        now = time.monotonic()
        wait = self._next_slot - now
        if wait > 0:
            await asyncio.sleep(wait)
            now = time.monotonic()
        self._next_slot = max(now, self._next_slot) + (1.0 / self.max_qps)

    def _cache_get(self, key: tuple[str, str]) -> Optional[ToolResult]:
        item = self._cache.get(key)
        if item is None:
            return None
        expires, result = item
        if time.monotonic() > expires:
            self._cache.pop(key, None)
            return None
        cached = result.model_copy(deep=True)
        cached.metadata['cached'] = True
        cached.metadata['cache_hit_seconds'] = round(time.monotonic() - (expires - self.cache_ttl_seconds), 2)
        return cached

    def _cache_put(self, key: tuple[str, str], result: ToolResult) -> None:
        if len(self._cache) >= self.cache_max_entries:
            try:
                self._cache.pop(next(iter(self._cache)))
            except (StopIteration, KeyError):
                pass
        self._cache[key] = (time.monotonic() + self.cache_ttl_seconds, result.model_copy(deep=True))

    async def _execute_once(self, tool_name: str, arguments: dict[str, Any], context: dict) -> ToolResult:
        spec = self._tools.get(tool_name)
        if spec is None:
            return ToolResult(
                tool_call_id=context.get("tool_call_id", ""),
                tool_name=tool_name,
                success=False,
                error="UNKNOWN_MCP_TOOL",
            )
        started = datetime.now(UTC)
        try:
            result = await self.client.call_tool(tool_name, arguments or {})
            data, error = self._parse_content(tool_name, result)
            finished = datetime.now(UTC)
            if error is not None:
                return ToolResult(
                    tool_call_id=context.get("tool_call_id", ""),
                    tool_name=tool_name,
                    success=False,
                    error=error,
                    source_references=[f"mcp://{self.server_label}/{tool_name}"],
                    started_at=started,
                    finished_at=finished,
                    metadata={"mcp_server": self.server_label, "raw_content": redact(self._raw_text(result)),
                              "diagnostic": diagnose(error, layer='upstream_service' if error.startswith('UPSTREAM_') else 'mcp_tool',
                                                     code=data.get('infocode') if isinstance(data, dict) else None)},
                    data=data,
                )
            return ToolResult(
                tool_call_id=context.get("tool_call_id", ""),
                tool_name=tool_name,
                success=True,
                data=data,
                source_references=[f"mcp://{self.server_label}/{tool_name}"],
                started_at=started,
                finished_at=finished,
                latency_ms=round((finished - started).total_seconds() * 1000, 2),
                metadata={"mcp_server": self.server_label, "provider": self.server_label,
                          "retrieved_at": finished.isoformat(), "raw_content": redact(self._raw_text(result))},
            )
        except Exception as error:
            from .client import JsonRpcError, StdioMCPClientError
            layer = 'mcp_transport' if isinstance(error, StdioMCPClientError) else ('mcp_protocol' if isinstance(error, JsonRpcError) else 'mcp_tool')
            finished = datetime.now(UTC)
            return ToolResult(
                tool_call_id=context.get("tool_call_id", ""),
                tool_name=tool_name,
                success=False,
                error=self._classify_error(error),
                source_references=[f"mcp://{self.server_label}/{tool_name}"],
                started_at=started,
                finished_at=finished,
                latency_ms=round((finished - started).total_seconds() * 1000, 2),
                metadata={"mcp_server": self.server_label,
                          "diagnostic": diagnose(error, layer=layer, code=getattr(error, 'code', None))},
            )

    # ---- 解析 ----

    @staticmethod
    def _raw_text(result: dict[str, Any]) -> str:
        parts = []
        for item in result.get("content", []) or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts).strip()

    def _parse_content(self, tool_name: str, result: dict[str, Any]) -> tuple[Any, Optional[str]]:
        text = self._raw_text(result)
        if result.get('isError'):
            return None, f'MCP_TOOL_ERROR: {redact(text)}'
        if isinstance(result.get('structuredContent'), (dict, list)):
            parsed = result['structuredContent']
            if isinstance(parsed, dict) and str(parsed.get('status')) == '0':
                return parsed, f"UPSTREAM_ERROR: {parsed.get('infocode')} {parsed.get('info')}"
            return parsed, None
        if not text:
            return None, "EMPTY_RESULT: MCP 返回空内容（无查询结果，非解析失败）"
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # 非 JSON：尝试结构化的结构化文本？AMap 类工具返回 JSON 文本；
            # 非 JSON 视为未知/Provider 数据 → 不冒充成功
            return None, f"PARSE_FAILED: 返回内容不是可解析 JSON (前120字符: {text[:120]!r})"
        if isinstance(parsed, dict) and str(parsed.get('status')) == '0':
            return parsed, f"UPSTREAM_ERROR: {parsed.get('infocode', '')} {parsed.get('info', parsed.get('error', 'request rejected'))}"
        if isinstance(parsed, dict) and (parsed.get('success') is False or parsed.get('error')):
            return parsed, f"MCP_TOOL_ERROR: {parsed.get('error', 'request rejected')}"
        return parsed, None

    @staticmethod
    def _classify_error(error: Exception) -> str:
        from .client import JsonRpcError, MCPServerError, StdioMCPClientError

        if isinstance(error, StdioMCPClientError):
            return f"MCP_CONNECT_FAILED: {error}"
        if isinstance(error, JsonRpcError):
            return f"MCP_RPC_ERROR_{error.code}: {error.message}"
        if isinstance(error, MCPServerError):
            return f"MCP_TOOL_ERROR: {error}"
        return f"MCP_UNEXPECTED: {type(error).__name__}: {error}"


class MCPToolHandle:
    """把 adapter 的一个白名单工具暴露成 ToolRegistry 可注册的工具对象。"""

    def __init__(self, adapter: MCPToolAdapter, spec: ToolSpec):
        self.adapter = adapter
        self.spec = spec

    async def execute(self, arguments: Any, context: dict) -> ToolResult:
        payload = arguments
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        return await self.adapter.execute(self.spec.name, payload or {}, context)
