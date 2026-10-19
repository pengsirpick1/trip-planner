"""Stable, safe error boundary for API, provider, tools and persistence."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AppError(Exception):
    code: str = "INTERNAL_ERROR"
    public_message: str = "服务暂时不可用，请稍后重试。"
    http_status: int = 500
    retryable: bool = False
    internal_context: dict[str, Any] = field(default_factory=dict)
    cause: Exception | None = None
    def __str__(self): return self.code

class AuthenticationError(AppError):
    def __init__(self, code="AUTH_INVALID", message="登录状态无效，请重新登录。"):
        super().__init__(code, message, 401, False)
class ResourceNotFoundError(AppError):
    def __init__(self): super().__init__("RESOURCE_NOT_FOUND", "请求的资源不存在。", 404, False)
class ProviderError(AppError): pass
class ProviderTimeoutError(ProviderError):
    def __init__(self, **kw): super().__init__("PROVIDER_TIMEOUT", "当前模型服务响应较慢，请稍后重试。", 503, True, **kw)
class ProviderRateLimitError(ProviderError):
    def __init__(self, **kw): super().__init__("PROVIDER_RATE_LIMITED", "当前模型服务繁忙，请稍后重试。", 503, True, **kw)
class ProviderAuthError(ProviderError):
    def __init__(self, **kw): super().__init__("PROVIDER_AUTH_FAILED", "当前模型服务暂时不可用，请稍后重试。", 503, False, **kw)
class ProviderQuotaError(ProviderError):
    def __init__(self, **kw): super().__init__("PROVIDER_QUOTA_EXHAUSTED", "当前模型服务暂时不可用，请稍后重试。", 503, False, **kw)
class ProviderNetworkError(ProviderError):
    def __init__(self, **kw): super().__init__("PROVIDER_NETWORK_ERROR", "当前模型服务连接异常，请稍后重试。", 503, True, **kw)
class ProviderUnavailableError(ProviderError):
    def __init__(self, **kw): super().__init__("PROVIDER_UNAVAILABLE", "当前模型服务暂时不可用，请稍后重试。", 503, True, **kw)
class ProviderInvalidResponseError(ProviderError):
    def __init__(self, **kw): super().__init__("PROVIDER_INVALID_RESPONSE", "当前模型服务返回异常，请稍后重试。", 503, False, **kw)
class ToolError(AppError): pass
class ToolTimeoutError(ToolError):
    def __init__(self, **kw): super().__init__("TOOL_TIMEOUT", "外部旅行服务响应较慢，请稍后重试。", 503, True, **kw)
class ToolNetworkError(ToolError):
    def __init__(self, **kw): super().__init__("TOOL_NETWORK_ERROR", "外部旅行服务连接异常，请稍后重试。", 503, True, **kw)
class ToolInvalidArgumentsError(ToolError):
    def __init__(self, **kw): super().__init__("TOOL_INVALID_ARGUMENTS", "旅行查询参数无效。", 400, False, **kw)
class ToolUpstreamError(ToolError):
    def __init__(self, **kw): super().__init__("TOOL_UPSTREAM_ERROR", "外部旅行服务暂时不可用。", 503, True, **kw)
class QuotaInsufficientError(AppError):
    def __init__(self): super().__init__("QUOTA_INSUFFICIENT", "当前可用额度不足，无法开始本次规划。", 429, False)
class QuotaPersistenceError(AppError):
    def __init__(self, **kw): super().__init__("QUOTA_PERSISTENCE_FAILED", "额度服务暂时不可用，请稍后重试。", 503, False, **kw)
class QuotaReconciliationRequiredError(AppError):
    def __init__(self): super().__init__("QUOTA_RECONCILIATION_REQUIRED", "上一次模型调用正在核对中，请稍后重试。", 503, False)
class TelemetryPersistenceError(AppError):
    def __init__(self, **kw): super().__init__("TELEMETRY_PERSISTENCE_FAILED", "调用结果正在核对中，请稍后重试。", 503, False, **kw)
class DatabaseUnavailableError(AppError):
    def __init__(self, **kw): super().__init__("DATABASE_UNAVAILABLE", "服务存储暂时不可用，请稍后重试。", 503, True, **kw)
class ConcurrentStateUpdateError(AppError):
    def __init__(self): super().__init__("CONCURRENT_STATE_UPDATE", "请求状态已更新，请刷新后重试。", 409, False)

def sanitize_exception(value: object) -> str:
    import re
    text = str(value)
    text = re.sub(r'(?i)(bearer\s+)[^\s,;]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(mysql(?:\+\w+)?://[^:]+:)[^@\s]+', r'\1[REDACTED]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', text)
    text = re.sub(r'(?i)(api[_-]?key|token|password)=([^\s&]+)', r'\1=[REDACTED]', text)
    return text[:1000]
