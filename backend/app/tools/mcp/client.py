"""MCP stdio 客户端（最小实现，JSON-RPC 2.0 over 换行分隔 stdio）。

设计说明：
- 只实现生产需要的子集：initialize / tools/list / tools/call / notifications/initialized。
- 每个请求自增 id；响应/通知按行读取；支持并发请求排队（同一时刻单请求）足够 ReAct 使用。
- 不解析任意 MCP 资源/提示词；本项目的 MCP 只承载只读工具。
- 连接生命周期由 adapter 负责（ensure_started/close），进程句柄不会泄漏。

Error 语义（审计 §9）：
- 进程无法启动 / 握手失败 → StdioMCPClientError（上层转 ToolResult MCP_CONNECT_FAILED）
- JSON-RPC error → JsonRpcError / MCPServerError
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-03-26"


class StdioMCPClientError(RuntimeError):
    """进程/传输层错误。"""


class JsonRpcError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPServerError(RuntimeError):
    """tools/call 返回 isError 或缺少预期结构。"""

    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result


@dataclass
class MCPToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: Optional[dict[str, Any]] = None


class StdioMCPClient:
    """管理单个 MCP server 子进程（如 uvx amap-mcp-server）。"""

    def __init__(
        self,
        server_command: list[str],
        *,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
        request_timeout_seconds: float = 20.0,
        startup_timeout_seconds: float = 30.0,
    ):
        self.server_command = list(server_command)
        self.env = env
        self.cwd = cwd
        self.request_timeout = request_timeout_seconds
        self.startup_timeout = startup_timeout_seconds
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._stderr_lines: list[str] = []
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._started = False
        self._start_lock = asyncio.Lock()
        self._server_info: dict[str, Any] = {}
        self._reader_error = None

    # ---- 生命周期 ----

    async def start(self) -> None:
        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *self.server_command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self.env,
                    cwd=self.cwd,
                    limit=4 * 1024 * 1024,
                )
            except (OSError, ValueError) as error:
                raise StdioMCPClientError(
                    f"无法启动 MCP server {' '.join(self.server_command)}: {error}"
                ) from error

            self._reader_error = None
            self._reader_task = asyncio.create_task(self._read_loop())
            self._reader_task.add_done_callback(self._reader_done)
            self._stderr_task = asyncio.create_task(self._stderr_loop())
            try:
                await asyncio.wait_for(self._rpc("initialize", {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "travel-agent-backend", "version": "0.1.0"},
                }), timeout=self.startup_timeout)
            except Exception as error:
                tail = "\n".join(self._stderr_lines[-8:])
                await self.close()
                suffix = f"\nserver stderr:\n{tail}" if tail else ""
                raise StdioMCPClientError(f"MCP initialize 失败: {error}{suffix}") from error
            # initialized 通知（server 靠它进入就绪）
            await self._send_notification("notifications/initialized", {})
            self._started = True
            log.info("MCP client started: %s (server=%s)", self.server_command, self._server_info)

    async def close(self) -> None:
        self._started = False
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stderr_task = None
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
        if proc is not None and proc.stdin is not None:
            proc.stdin.close()
        if proc is not None:
            for reader in (proc.stdout, proc.stderr):
                if reader is not None:
                    await reader.read()
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(StdioMCPClientError("MCP client closed"))
        self._pending.clear()

    @property
    def started(self) -> bool:
        return self._started

    # ---- MCP 调用 ----

    async def list_tools(self) -> list[MCPToolInfo]:
        result = await self._rpc("tools/list", {})
        tools = result.get("tools", [])
        return [
            MCPToolInfo(
                name=tool.get("name", ""),
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {}),
                annotations=tool.get("annotations"),
            )
            for tool in tools
            if tool.get("name")
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if not isinstance(result, dict):
            raise MCPServerError(f"tools/call 响应格式错误: {type(result).__name__}")
        if result.get("isError"):
            content = self._extract_text(result.get("content", []))
            raise MCPServerError(f"MCP tool {name} returned isError: {content or '无错误信息'}", result)
        return result

    @staticmethod
    def _extract_text(content: list[Any]) -> str:
        parts = []
        for item in content or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts).strip()

    # ---- JSON-RPC 原语 ----

    def _reader_done(self, task):
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._reader_error = StdioMCPClientError(f'MCP reader failed ({type(error).__name__}); local transport could not decode the server response')
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(self._reader_error)

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._reader_error is not None:
            raise self._reader_error
        if self._proc is None or self._proc.stdin is None or self._proc.stdin.is_closing():
            raise StdioMCPClientError("MCP server 进程不可用（未启动或已退出）")
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        message = json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            ensure_ascii=False,
        )
        try:
            self._proc.stdin.write((message + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        except asyncio.TimeoutError as error:
            self._pending.pop(request_id, None)
            raise StdioMCPClientError(f"MCP 请求超时: {method}") from error
        finally:
            self._pending.pop(request_id, None)

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None or self._proc.stdin.is_closing():
            raise StdioMCPClientError("MCP server 进程不可用")
        message = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params}, ensure_ascii=False
        )
        self._proc.stdin.write((message + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def _stderr_loop(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        reader = self._proc.stderr
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr_lines.append(text)

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        reader = self._proc.stdout
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                # MCP 规范要求 UTF-8，但部分 Windows 上的第三方 MCP server 会把
                # 含中文的工具响应按本机代码页（GB18030）写入 stdout。
                try:
                    text = line.decode("utf-8")
                except UnicodeDecodeError:
                    text = line.decode("gb18030")
                    log.warning("MCP server emitted a GB18030 protocol line; accepted for Windows compatibility")
                payload = json.loads(text)
            except (UnicodeDecodeError, json.JSONDecodeError):
                log.warning("MCP server emitted a non-JSON protocol line (%d bytes); ignored", len(line))
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("method"):
                # 服务端请求/通知：本项目不需要，忽略或回复空
                continue
            request_id = payload.get("id")
            if request_id is None:
                continue
            future = self._pending.get(request_id)
            if future is None or future.done():
                continue
            if "error" in payload and payload["error"] is not None:
                error = payload["error"]
                future.set_exception(JsonRpcError(error.get("code", -1), error.get("message", "unknown"), error.get("data")))
            else:
                self._handle_server_info(payload.get("result"))
                future.set_result(payload.get("result", {}))
        # stdout EOF：server 已退出；立刻让所有待处理请求失败，而不是挂到超时
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(StdioMCPClientError("MCP server 进程退出（stdout EOF）"))

    def _handle_server_info(self, result: Any) -> None:
        if isinstance(result, dict) and isinstance(result.get("serverInfo"), dict):
            self._server_info = result["serverInfo"]
