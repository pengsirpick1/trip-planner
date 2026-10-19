"""集中测试替身：仅供测试显式注入，生产装配永不 import 本目录。"""

from __future__ import annotations

import json
from typing import Any

from app.tools.base import ToolResult, ToolSpec


class FakeMessageProcessor:
    """让 TravelApplicationService 产出可控类型化结果的处理器替身。"""

    def __init__(self, outcome):
        self.outcome = outcome

    async def process(self, message: str, run) -> Any:
        return self.outcome


class CannedStructuredLLM:
    """确定性 LLM 替身。

    - generate_structured：按 response_model.__name__ 查 structured_map；
      value 可为 dict（经 pydantic 解析）或已构造实例。
    - decide：按工具/依赖情况确定性推进，绝不联网：
      有 tools 且无上游依赖且还没有工具观察 → 先发一次 weather 工具调用；
      有上游依赖（下游合成）→ 直接 final；
      否则返回 final（DIRECT 路径）。
    """

    def __init__(self, structured_map=None, direct_final: str = "北京今日晴，适合出行。"):
        self.structured_map = structured_map or {}
        self.direct_final = direct_final
        self.structured_calls: list[str] = []
        self.decide_calls = 0

    async def generate_structured(self, messages, response_model):
        name = response_model.__name__
        self.structured_calls.append(name)
        value = self.structured_map[name]
        if isinstance(value, dict):
            return response_model(**value)
        return value

    async def decide(self, task, observations, *, messages=None, tools=(), dependency_results=None):
        self.decide_calls += 1
        if tools:
            has_observation = any(getattr(o, "success", False) for o in observations)
            if dependency_results:
                return {"final": f"{getattr(task, 'id', 'task')}: 综合完成", "usage": {}}
            if not has_observation:
                arguments = json.dumps({"location": "Beijing"})
                call = {
                    "id": f"{getattr(task, 'id', 't')}-0",
                    "type": "function",
                    "function": {"name": "weather", "arguments": arguments},
                }
                return {
                    "tool_calls": [call],
                    "assistant_message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [call],
                    },
                    "usage": {},
                }
            return {"final": f"{getattr(task, 'id', 'task')}: 25C，天气晴朗", "usage": {}}
        return {"final": self.direct_final, "usage": {}}


class FakeWeatherTool:
    """无网络天气替身（测试/离线用）。"""

    def __init__(self):
        self.spec = ToolSpec(
            name="weather",
            description="read-only current weather (fake)",
            capabilities={"weather_research"},
        )
        self.calls: list[dict] = []

    async def execute(self, arguments, context):
        self.calls.append(arguments)
        location = getattr(arguments, "location", None) or (arguments or {}).get("location", "unknown")
        return ToolResult(
            tool_call_id=context["tool_call_id"],
            tool_name="weather",
            success=True,
            data={"location": location, "temperature_c": 25.0},
            source_references=["fake://weather"],
            metadata={"provider": "fake"},
        )
