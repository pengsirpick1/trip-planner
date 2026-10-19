"""探测 AMap MCP server 实际暴露的工具清单与 schema（只读握手，无需 Key）。

用途：验证 tools/mcp/amap.py 的白名单映射与真实 server 是否一致（Phase C）。
运行：cd backend && python evals/probe_amap_tools.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


async def main():
    from app.config import Settings
    from app.tools.mcp.client import StdioMCPClient

    settings = Settings()
    env = dict(os.environ)
    if settings.amap_api_key:
        env["AMAP_MAPS_API_KEY"] = settings.amap_api_key

    client = StdioMCPClient(
        ["uvx", "amap-mcp-server"],
        env=env,
        startup_timeout_seconds=90.0,
        request_timeout_seconds=30.0,
    )
    try:
        await client.start()
        tools = await client.list_tools()
        print(f"discovered {len(tools)} tools")
        for tool in tools:
            props = (tool.input_schema or {}).get("properties", {})
            required = (tool.input_schema or {}).get("required", [])
            print(json.dumps({
                "name": tool.name,
                "description_head": (tool.description or "")[:80],
                "params": sorted(props.keys()),
                "required": required,
            }, ensure_ascii=False))
        path = os.path.join(BACKEND, "evals", "results", "amap_tools_probe.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                [
                    {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                    for t in tools
                ],
                fh,
                ensure_ascii=False,
                indent=2,
            )
        print("saved:", path)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
