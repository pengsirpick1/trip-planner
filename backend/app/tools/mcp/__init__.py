"""工具层 MCP（自研最小 stdio JSON-RPC 客户端，不依赖 hello_agents 黑盒）。"""

from .adapter import MCPToolAdapter, MCPToolHandle
from .pool import MCPPoolToolHandle, MCPToolPool
from .client import (
    JsonRpcError,
    MCPServerError,
    StdioMCPClient,
    StdioMCPClientError,
)

__all__ = [
    "JsonRpcError",
    "MCPToolAdapter",
    "MCPToolHandle",
    "MCPPoolToolHandle",
    "MCPToolPool",
    "MCPServerError",
    "StdioMCPClient",
    "StdioMCPClientError",
]
