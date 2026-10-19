"""Phase B 真实联调冒烟（opt-in，不参与 default pytest）。

运行：
    cd backend
    RUN_ONLINE_TESTS=1 python evals/run_online_smoke.py

装配：唯一生产入口 bootstrap.build_travel_app（真实 M1→M5 + DeepSeek + weather 工具），
与测试/产品完全同一套装配；绝不 fake。
用例：DIRECT 事实问答 / SIMPLE 单城天气（真实 weather 工具）/ COMPLEX 双城天气对比
（真实图执行，行程合成 Phase D 未接入 → 应返回 TRIP_PLAN_SYNTHESIS_NOT_READY）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

CASES = [
    {
        "name": "direct_factual",
        "message": "从东京坐新干线到京都大约需要多长时间？回答事实即可，不需要行程规划。",
    },
    {
        "name": "simple_weather_tool",
        "message": "请用工具查询 Tokyo 现在的气温和降水，给出实测数值。不需要行程。",
    },
    {
        "name": "complex_weather_compare",
        "message": (
            "对比东京和京都现在的天气并综合给出穿衣建议：分别查询两地当前气温和降水，"
            "比较两地天气。不要预测未来，不需要景点餐饮酒店交通研究。"
        ),
    },
    {
        "name": "amap_poi_research",
        "message": (
            "请用地图工具查询北京故宫博物院（POI）的地址和所在行政区，并给出游玩小提示。"
            "不需要完整行程规划。"
        ),
    },
    {
        "name": "complex_trip_plan_beijing",
        "message": (
            "请为我规划 2026-10-01 北京一日游：上午游览故宫，之后选故宫周边步行可达的 2 个点，"
            "交通用地铁，预算 500 元。需要用地图工具查询真实地点。"
        ),
    },
]


def _summarize(record: dict) -> dict:
    return {
        "run_id": record.get("run_id"),
        "kind": record.get("kind"),
        "status": record.get("status"),
        "error_code": record.get("error_code"),
        "message_head": (record.get("message") or "")[:160],
        "data": record.get("data"),
        "question_count": len(record.get("questions") or []) if isinstance(record.get("questions"), list) else None,
    }


async def _run_one(service, case: dict) -> dict:
    started = time.perf_counter()
    record = await service.run_single_plan(case["message"], session_id=case["name"])
    return {
        "name": case["name"],
        "message": case["message"],
        "record": record.model_dump(mode="json"),
        "events": [
            {"event": event.event, "payload": event.payload}
            for event in record.events
        ],
        "seconds": round(time.perf_counter() - started, 2),
    }


async def main() -> dict:
    if os.getenv("RUN_ONLINE_TESTS") != "1":
        raise SystemExit("ONLINE SMOKE SKIPPED (RUN_ONLINE_TESTS != 1)")
    from app.config import Settings, validate_core_config
    from app.bootstrap import build_travel_app

    settings = Settings()
    missing = validate_core_config(settings)
    if missing:
        raise SystemExit(f"missing config: {missing}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder = BACKEND / "evals" / "results" / ("smoke_" + stamp)
    folder.mkdir(parents=True, exist_ok=True)

    service = build_travel_app(settings)
    amap_enabled = settings.amap_api_key is not None and bool(settings.amap_api_key)
    selected = [case for case in CASES if case["name"] == os.getenv("SMOKE_CASE")] if os.getenv("SMOKE_CASE") else CASES
    try:
        await service.start()  # 可选组件：AMap MCP 连接（若已配置 Key）
        results = []
        for case in selected:
            start = time.perf_counter()
            try:
                item = await _run_one(service, case)
            except Exception as error:
                item = {"name": case["name"], "error": f"{type(error).__name__}: {error}"}
            item["seconds"] = item.get("seconds", round(time.perf_counter() - start, 2))
            results.append(item)
            print(json.dumps({"case": item["name"], "summary": _summarize(item.get("record", {})),
                              "error": item.get("error"), "seconds": item["seconds"]},
                             ensure_ascii=False), flush=True)
    finally:
        await service.close()

    report = {
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "amap_enabled": amap_enabled,
        "timestamp": stamp,
        "results": results,
    }
    path = folder / "result.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result_file": str(path)}, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    report = asyncio.run(main())
    raise SystemExit(0)
