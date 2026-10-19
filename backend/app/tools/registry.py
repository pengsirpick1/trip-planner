"""工具注册表（迁自 travel_agent + Phase C：纯 JSON Schema 参数校验）。"""

import asyncio
import time
from typing import Any

from pydantic import ValidationError

from .base import ToolResult
from .validate import validate_arguments
from .diagnostics import diagnose
from ..errors import ToolInvalidArgumentsError, ToolNetworkError, ToolTimeoutError, ToolUpstreamError, sanitize_exception


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Any] = {}
        self._order: list[str] = []
        self.rate_limiter = None
        self.rate_limit = 90

    def register(self, tool, *, prepend: bool = False):
        name = tool.spec.name
        self.tools[name] = tool
        if name in self._order:
            self._order.remove(name)
        if prepend:
            self._order.insert(0, name)
        else:
            self._order.append(name)

    def resolve(self, name: str, allowed):
        if name not in self.tools:
            raise LookupError("UNKNOWN_TOOL")
        tool = self.tools[name]
        if name not in allowed:
            raise PermissionError("UNAUTHORIZED_TOOL")
        return tool

    async def call(self, name: str, raw_arguments: dict, allowed, context: dict) -> ToolResult:
        try:
            tool = self.resolve(name, allowed)
            if self.rate_limiter is not None:
                subject = str(context.get("user_id") or "anonymous")
                if not await self.rate_limiter.allow(f"tool:{subject}:{name}", self.rate_limit, 60):
                    return ToolResult(tool_call_id=context["tool_call_id"], tool_name=name, success=False, error="TOOL_RATE_LIMITED", metadata={"error_code":"TOOL_RATE_LIMITED"})
            spec = tool.spec
            if spec.input_model is not None:
                args = spec.input_model.model_validate(raw_arguments)
            elif spec.input_schema:
                errors = validate_arguments(raw_arguments or {}, spec.input_schema)
                if errors:
                    return ToolResult(
                        tool_call_id=context["tool_call_id"],
                        tool_name=name,
                        success=False,
                        error="INVALID_ARGUMENTS: " + "; ".join(errors),
                        metadata={'diagnostic': diagnose('; '.join(errors), layer='local_validation')},
                    )
                args = raw_arguments
            else:
                args = raw_arguments
            attempts = 0
            retry_wait = 0.0
            while True:
                started = time.monotonic()
                try:
                    result = await asyncio.wait_for(
                        tool.execute(args, context), timeout=spec.timeout_seconds
                    )
                except asyncio.TimeoutError:
                    result = ToolResult(
                        tool_call_id=context["tool_call_id"], tool_name=name,
                        success=False, error="TOOL_TIMEOUT",
                        metadata={'diagnostic': diagnose('Local tool deadline exceeded; upstream response not observed', layer='mcp_transport')},
                    )
                elapsed = time.monotonic() - started
                retryable = result.error == "TOOL_TIMEOUT" or (
                    (result.metadata.get("diagnostic") or {}).get("category") in {"network", "rate_limit"}
                )
                if result.success or not retryable or not spec.idempotent or attempts >= spec.max_retries:
                    result.latency_ms = round((result.latency_ms or 0) + elapsed * 1000, 2)
                    result.metadata.update({"attempts": attempts + 1, "retry_time_ms": round(retry_wait * 1000, 2)})
                    return result
                attempts += 1
                # A short bounded backoff protects a transient upstream failure without
                # allowing one MCP call to stall the whole task indefinitely.
                backoff = min(0.25 * attempts, 0.5)
                retry_wait += backoff
                await asyncio.sleep(backoff)
        except asyncio.TimeoutError:
            return ToolResult(
                tool_call_id=context["tool_call_id"], tool_name=name, success=False, error="TOOL_TIMEOUT",
                metadata={'diagnostic': diagnose('Local tool deadline exceeded; upstream response not observed', layer='mcp_transport')}
            )
        except (ValidationError, ValueError, TypeError) as error:
            return ToolResult(
                tool_call_id=context["tool_call_id"],
                tool_name=name,
                success=False,
                error="TOOL_INVALID_ARGUMENTS",
                metadata={'diagnostic': diagnose(error, layer='local_validation')},
            )
        except Exception as error:
            return ToolResult(
                tool_call_id=context["tool_call_id"], tool_name=name, success=False, error="TOOL_UPSTREAM_ERROR",
                metadata={"error_code": "TOOL_UPSTREAM_ERROR", "exception_type": type(error).__name__}
            )
