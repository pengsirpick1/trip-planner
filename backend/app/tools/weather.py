"""只读天气工具（迁自 travel_agent/agent/weather.py）。

能力边界：只查当前天气；对指定 forecast_date 的请求明确拒绝
（UNSUPPORTED_DATE），绝不把当前数据冒充未来预报。
"""

import asyncio
import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime, date

from pydantic import BaseModel, Field

from .base import SideEffectLevel, ToolResult, ToolSpec


class WeatherInput(BaseModel):
    location: str = Field(min_length=1)
    forecast_date: date | None = None


class WeatherData(BaseModel):
    location: str
    forecast_date: date | None = None
    temperature_c: float | None = None
    precipitation_mm: float | None = None
    provider: str = "open-meteo"
    retrieved_at: datetime


class OpenMeteoWeatherTool:
    spec = ToolSpec(
        name="weather",
        description=(
            "Read-only CURRENT temperature and precipitation by city. "
            "Omit forecast_date; dated forecasts are not supported."
        ),
        capabilities={"weather_research"},
        input_model=WeatherInput,
        side_effect_level=SideEffectLevel.READ_ONLY,
        idempotent=True,
        timeout_seconds=8,
    )

    async def execute(self, args, context):
        if args.forecast_date is not None:
            return ToolResult(
                tool_call_id=context["tool_call_id"],
                tool_name="weather",
                success=False,
                error=(
                    "UNSUPPORTED_DATE: only current conditions are available; "
                    "do not present them as a dated forecast"
                ),
            )
        query = urllib.parse.urlencode({"name": args.location, "count": 1, "format": "json"})

        def fetch(url: str):
            with urllib.request.urlopen(url, timeout=6) as response:
                return json.loads(response.read())

        try:
            geo = await asyncio.to_thread(
                fetch, "https://geocoding-api.open-meteo.com/v1/search?" + query
            )
            if not geo.get("results"):
                return ToolResult(
                    tool_call_id=context["tool_call_id"],
                    tool_name="weather",
                    success=False,
                    error=(
                        "LOCATION_NOT_FOUND: try the city's English or local official name; "
                        "do not invent coordinates"
                    ),
                )
            item = geo["results"][0]
            data = await asyncio.to_thread(
                fetch,
                "https://api.open-meteo.com/v1/forecast?latitude="
                f"{item['latitude']}&longitude={item['longitude']}"
                "&current=temperature_2m,precipitation",
            )
            current = data.get("current", {})
            if current.get("temperature_2m") is None or current.get("precipitation") is None:
                return ToolResult(
                    tool_call_id=context["tool_call_id"],
                    tool_name="weather",
                    success=False,
                    error="INCOMPLETE_WEATHER_DATA",
                )
            weather = WeatherData(
                location=item["name"],
                forecast_date=None,
                temperature_c=current["temperature_2m"],
                precipitation_mm=current["precipitation"],
                retrieved_at=datetime.now(UTC),
            )
            return ToolResult(
                tool_call_id=context["tool_call_id"],
                tool_name="weather",
                success=True,
                data=weather.model_dump(mode="json"),
                source_references=["https://open-meteo.com"],
                metadata={
                    "provider": "open-meteo",
                    "retrieved_at": weather.retrieved_at.isoformat(),
                },
            )
        except Exception as error:
            return ToolResult(
                tool_call_id=context["tool_call_id"],
                tool_name="weather",
                success=False,
                error=str(error),
            )
