"""Phase C：MCP 工具层（白名单/解析/错误语义/JSON Schema 校验/GeoFacade）。"""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.services.geo_facade import GeoFacade
from app.tools.base import ToolResult, ToolSpec
from app.tools.mcp.adapter import MCPToolAdapter, MCPToolHandle
from app.tools.mcp.amap import build_amap_adapter, build_amap_env
from app.tools.mcp.pool import MCPToolPool
from app.tools.registry import ToolRegistry
from app.tools.validate import validate_arguments


# ---- JSON Schema 参数校验（纯 schema 工具） ----


class _SchemaTool:
    def __init__(self, name="place_search", input_schema=None):
        self.spec = ToolSpec(
            name=name,
            description="test",
            capabilities={"place_research"},
            input_model=None,
            input_schema=input_schema or {
                "type": "object",
                "required": ["keywords", "city"],
                "properties": {
                    "keywords": {"type": "string", "minLength": 1},
                    "city": {"type": "string"},
                    "limit": {"type": "integer"},
                    "citylimit": {"type": "boolean"},
                },
            },
        )
        self.calls = []

    async def execute(self, arguments, context):
        self.calls.append(arguments)
        return ToolResult(
            tool_call_id=context["tool_call_id"], tool_name=self.spec.name, success=True, data=[]
        )


async def test_registry_validates_pure_schema_arguments():
    registry = ToolRegistry()
    tool = _SchemaTool()
    registry.register(tool)
    bad = await registry.call(
        "place_search", {"city": "北京"}, {"place_search"}, {"tool_call_id": "c1"}
    )
    assert bad.success is False
    assert bad.error.startswith("INVALID_ARGUMENTS")
    assert "keywords" in bad.error
    wrong_type = await registry.call(
        "place_search",
        {"keywords": "故宫", "city": "北京", "limit": "很多"},
        {"place_search"},
        {"tool_call_id": "c2"},
    )
    assert wrong_type.success is False
    assert "limit" in wrong_type.error
    ok = await registry.call(
        "place_search",
        {"keywords": "故宫", "city": "北京", "limit": 10, "citylimit": True},
        {"place_search"},
        {"tool_call_id": "c3"},
    )
    assert ok.success is True
    assert len(tool.calls) == 1


def test_validate_arguments_direct():
    schema = {
        "type": "object",
        "required": ["a"],
        "properties": {
            "a": {"type": "integer"},
            "b": {"enum": ["x", "y"]},
        },
    }
    errors = validate_arguments({"a": "nope"}, schema)
    assert errors and any("a" in error for error in errors)
    errors2 = validate_arguments({"a": 1, "b": "z"}, schema)
    assert any("b" in error for error in errors2)
    assert validate_arguments({"a": 1, "b": "x"}, schema) == []


# ---- MCPToolAdapter：白名单 + 解析 + 错误语义 ----


class _FakeMCPClient:
    def __init__(self, server_tools: dict, call_results: dict | None = None, errors: dict | None = None):
        self.server_tools = server_tools  # name -> input_schema
        self.call_results = call_results or {}
        self.errors = errors or {}
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def list_tools(self):
        return [
            type("T", (), {"name": name, "description": f"desc {name}", "input_schema": schema})()
            for name, schema in self.server_tools.items()
        ]

    async def call_tool(self, name, arguments):
        if name in self.errors:
            from app.tools.mcp.client import JsonRpcError

            raise JsonRpcError(-32000, self.errors[name])
        return self.call_results.get(name, {"content": [{"type": "text", "text": "[]"}]})


def _text_result(text: str, is_error: bool = False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _make_adapter(fake_client, allowed=None):
    allowed_map = allowed or {
        "maps_text_search": {"capability": "place_research"},
        "maps_weather": {"capability": "weather_research"},
        "hidden_dangerous": {"capability": "place_research"},
    }
    return MCPToolAdapter(fake_client, allowed=allowed_map, server_label="amap")


async def test_adapter_registers_only_whitelist_found_tools():
    client = _FakeMCPClient(
        {
            "maps_text_search": {"type": "object", "properties": {}},
            "maps_weather": {"type": "object", "properties": {}},
            "extra_unlisted": {"type": "object", "properties": {}},
        }
    )
    adapter = _make_adapter(client, allowed={"maps_text_search": {"capability": "place_research"}})
    specs = await adapter.start()
    names = {spec.name for spec in specs}
    assert names == {"maps_text_search"}
    assert client.started
    await adapter.close()
    assert client.closed


async def test_adapter_parses_success_content_json():
    client = _FakeMCPClient(
        {"maps_text_search": {}},
        call_results={"maps_text_search": _text_result('[{"id":"1","name":"故宫"}]')},
    )
    adapter = _make_adapter(client)
    await adapter.start()
    result = await adapter.execute("maps_text_search", {"keywords": "故宫"}, {"tool_call_id": "x"})
    assert result.success is True
    assert result.data == [{"id": "1", "name": "故宫"}]
    assert result.source_references == ["mcp://amap/maps_text_search"]


async def test_adapter_empty_result_is_not_success():
    client = _FakeMCPClient({"maps_text_search": {}}, call_results={"maps_text_search": _text_result("")})
    adapter = _make_adapter(client)
    await adapter.start()
    result = await adapter.execute("maps_text_search", {}, {"tool_call_id": "x"})
    assert result.success is False
    assert "EMPTY_RESULT" in result.error


async def test_adapter_parse_failure_is_not_success():
    client = _FakeMCPClient(
        {"maps_text_search": {}},
        call_results={"maps_text_search": _text_result("<html>not json</html>")},
    )
    adapter = _make_adapter(client)
    await adapter.start()
    result = await adapter.execute("maps_text_search", {}, {"tool_call_id": "x"})
    assert result.success is False
    assert "PARSE_FAILED" in result.error


async def test_adapter_provider_rpc_error_classified():
    client = _FakeMCPClient(
        {"maps_weather": {}},
        errors={"maps_weather": "上游限流"},
    )
    adapter = _make_adapter(client, allowed={"maps_weather": {"capability": "weather_research"}})
    await adapter.start()
    result = await adapter.execute("maps_weather", {"city": "北京"}, {"tool_call_id": "x"})
    assert result.success is False
    assert "MCP_RPC_ERROR" in result.error


async def test_adapter_unknown_tool_rejected_without_client_call():
    client = _FakeMCPClient({})
    adapter = _make_adapter(client)
    await adapter.start()
    result = await adapter.execute("not_allowed_tool", {}, {"tool_call_id": "x"})
    assert result.success is False
    assert result.error == "UNKNOWN_MCP_TOOL"


# ---- AMap factory ----


def test_amap_env_requires_key():
    settings = Settings(llm_api_key="k", llm_base_url="http://x", llm_model="m", amap_api_key="")
    with pytest.raises(RuntimeError, match="AMAP_API_KEY"):
        build_amap_env(settings)


def test_amap_env_contains_mapped_key():
    settings = Settings(llm_api_key="k", llm_base_url="http://x", llm_model="m", amap_api_key="secret-amap")
    env = build_amap_env(settings)
    assert env["AMAP_MAPS_API_KEY"] == "secret-amap"
    adapter = build_amap_adapter(settings, server_command=["echo", "amap-mcp-server"])
    assert adapter.server_label == "amap"
    assert adapter.client.server_command == ["echo", "amap-mcp-server"]


# ---- GeoFacade ----


class _StubAmapTool:
    def __init__(self, name, capability, data=None):
        self.spec = ToolSpec(name=name, description=name, capabilities={capability})
        self.data = data if data is not None else [{"id": "p1", "name": "故宫"}]
        self.calls = []

    async def execute(self, arguments, context):
        self.calls.append(arguments)
        return ToolResult(
            tool_call_id=context["tool_call_id"],
            tool_name=self.spec.name,
            success=True,
            data=self.data,
            source_references=[f"mcp://amap/{self.spec.name}"],
        )


async def test_geo_facade_search_poi_success():
    registry = ToolRegistry()
    tool = _StubAmapTool("maps_text_search", "place_research")
    registry.register(tool)
    facade = GeoFacade(registry, allowed_tools={"maps_text_search"})
    result = await facade.search_poi("故宫", "北京")
    assert result["success"] is True
    assert result["kind"] == "tool_result"
    assert result["data"][0]["name"] == "故宫"
    assert tool.calls[0]["city"] == "北京"
    assert tool.calls[0]["citylimit"] == "true"


async def test_geo_facade_unavailable_when_empty_registry():
    facade = GeoFacade(ToolRegistry(), allowed_tools={"maps_text_search"})
    result = await facade.search_poi("故宫", "北京")
    assert result["success"] is False
    assert result["error_code"] == "MAP_TOOLS_UNAVAILABLE"


async def test_geo_facade_tool_not_enabled():
    registry = ToolRegistry()
    registry.register(_StubAmapTool("maps_text_search", "place_research"))
    facade = GeoFacade(registry, allowed_tools=set())
    result = await facade.get_weather("北京")
    assert result["success"] is False
    assert result["error_code"] == "TOOL_NOT_ENABLED"

# ---- 方案一：结果缓存 + 限流退避（2026-09-06 新增） ----


class _QueueClient:
    """按 name 返回预设结果队列并计数（用于缓存/重试断言）。"""

    def __init__(self, server_tools: dict, results: dict):
        self.server_tools = server_tools
        self.results = results
        self.calls: dict[str, int] = {name: 0 for name in results}
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def list_tools(self):
        return [
            type("T", (), {"name": name, "description": name, "input_schema": {}})()
            for name in self.server_tools
        ]

    async def call_tool(self, name, arguments):
        self.calls[name] += 1
        queue = self.results.get(name) or []
        if queue:
            return queue.pop(0)
        return {"content": [{"type": "text", "text": "[]"}]}


def _adapter_with(client, allowed=None):
    from app.tools.mcp.adapter import MCPToolAdapter

    return MCPToolAdapter(
        client,
        allowed=allowed
        or {
            "maps_text_search": {"capability": "place_research"},
            "maps_weather": {"capability": "weather_research"},
        },
        server_label="amap",
    )


async def test_adapter_caches_repeated_readonly_query():
    client = _QueueClient(
        {"maps_text_search": {}},
        {"maps_text_search": [_text_result('[{"id":"p1","name":"故宫"}]')]},
    )
    adapter = _adapter_with(client)
    await adapter.start()
    first = await adapter.execute("maps_text_search", {"keywords": "故宫", "city": "北京"}, {"tool_call_id": "a"})
    second = await adapter.execute("maps_text_search", {"keywords": "故宫", "city": "北京"}, {"tool_call_id": "b"})
    assert first.success and second.success
    assert client.calls["maps_text_search"] == 1  # 第二次命中缓存，不再消耗上游配额
    assert second.metadata.get("cached") is True
    await adapter.close()


async def test_adapter_rate_limit_retries_then_succeeds():
    limited = _text_result('{"status":"0","infocode":"CUQPS_HAS_EXCEEDED_THE_LIMIT","info":"..."}')
    ok = _text_result('[{"id":"p1","name":"故宫"}]')
    client = _QueueClient(
        {"maps_text_search": {}},
        {"maps_text_search": [limited, ok]},
    )
    adapter = _adapter_with(client)
    adapter.rate_backoff_base = 0.01  # 加速离线测试
    await adapter.start()
    result = await adapter.execute("maps_text_search", {"keywords": "故宫", "city": "北京"}, {"tool_call_id": "a"})
    assert result.success is True
    assert client.calls["maps_text_search"] == 2
    assert result.metadata.get("attempts") == 2
    assert len(result.metadata.get("retry_errors") or []) == 1
    await adapter.close()


async def test_weather_tool_is_not_cached():
    client = _QueueClient(
        {"maps_weather": {}},
        {"maps_weather": [_text_result('{"city":"北京市","forecasts":[]}'), _text_result('{"city":"北京市","forecasts":[]}')]},
    )
    adapter = _adapter_with(client)
    await adapter.start()
    await adapter.execute("maps_weather", {"city": "北京"}, {"tool_call_id": "a"})
    await adapter.execute("maps_weather", {"city": "北京"}, {"tool_call_id": "b"})
    assert client.calls["maps_weather"] == 2  # 天气结果易变，不缓存
    await adapter.close()


# ---- MCP 连接池：连接并发、全局限流、请求合并 ----


class _SlowAdapter:
    def __init__(self, name):
        self.name = name
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self.closed = False
        self.spec = ToolSpec(name="maps_text_search", description="search", capabilities={"place_research"})

    async def start(self):
        return [self.spec]

    async def close(self):
        self.closed = True

    async def execute(self, tool_name, arguments, context):
        import asyncio
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.03)
            return ToolResult(tool_call_id=context["tool_call_id"], tool_name=tool_name,
                              success=True, data={"adapter": self.name, "query": arguments})
        finally:
            self.active -= 1


async def test_mcp_pool_overlaps_slow_requests_on_separate_connections():
    import asyncio
    first, second = _SlowAdapter("a"), _SlowAdapter("b")
    pool = MCPToolPool([first, second], max_qps=1_000_000)
    await pool.start()
    try:
        one, two = await asyncio.gather(
            pool.execute("maps_text_search", {"keywords": "故宫"}, {"tool_call_id": "one"}),
            pool.execute("maps_text_search", {"keywords": "颐和园"}, {"tool_call_id": "two"}),
        )
        assert one.success and two.success
        assert first.calls == 1 and second.calls == 1
        assert first.max_active == 1 and second.max_active == 1
    finally:
        await pool.close()


async def test_mcp_pool_coalesces_identical_readonly_requests():
    import asyncio
    adapter = _SlowAdapter("only")
    pool = MCPToolPool([adapter], max_qps=1_000_000)
    await pool.start()
    try:
        one, two = await asyncio.gather(
            pool.execute("maps_text_search", {"keywords": "故宫"}, {"tool_call_id": "one"}),
            pool.execute("maps_text_search", {"keywords": "故宫"}, {"tool_call_id": "two"}),
        )
        assert one.success and two.success
        assert adapter.calls == 1
        assert two.metadata.get("request_coalesced") is True
    finally:
        await pool.close()
