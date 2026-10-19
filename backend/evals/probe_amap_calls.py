"""AMap 真实调用结构探测：POI/天气/路线（需要 backend/.env 的 AMAP_API_KEY）。"""

from __future__ import annotations

import asyncio
import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

CASES = [
    ("maps_text_search", {"keywords": "故宫", "city": "北京"}),
    ("maps_weather", {"city": "北京"}),
    (
        "maps_direction_walking_by_address",
        {"origin_address": "天安门", "destination_address": "故宫", "origin_city": "北京", "destination_city": "北京"},
    ),
    ("maps_geo", {"address": "天安门"}),
]


async def main():
    from app.config import Settings
    from app.tools.mcp.amap import build_amap_adapter

    settings = Settings()
    adapter = build_amap_adapter(settings)
    await adapter.start()
    for name, args in CASES:
        result = await adapter.execute(name, args, {"tool_call_id": f"probe-{name}"})
        text = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
        print(json.dumps(
            {"tool": name, "success": result.success, "error": result.error,
             "data_type": type(result.data).__name__,
             "data_head": json.dumps(result.data, ensure_ascii=False)[:500] if result.success else None},
            ensure_ascii=False,
        ), flush=True)
    await adapter.close()


if __name__ == "__main__":
    asyncio.run(main())
