"""工具契约（迁自 travel_agent/agent/tools.py 拆分）。

Phase B 说明：ToolSpec.input_schema（纯 JSON Schema）已存在，但 ToolRegistry.call
当前只在有 pydantic input_model 时校验参数——动态 schema 验证缺口在 Phase C 与
MCPToolAdapter 一起补齐（见 migration_plan_phase_ab.md §9）。
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SideEffectLevel(str, Enum):
    READ_ONLY = "read_only"
    SIDE_EFFECTFUL = "side_effectful"


class ToolSpec(BaseModel):
    name: str
    description: str
    capabilities: set[str]
    input_model: type[BaseModel] | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    idempotent: bool = True
    side_effect_level: SideEffectLevel = SideEffectLevel.READ_ONLY
    timeout_seconds: float = 10
    max_retries: int = Field(default=1, ge=0, le=3)


class ToolResult(BaseModel):
    tool_call_id: str
    tool_name: str
    success: bool
    data: Any = None
    error: str | None = None
    source_references: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_ms: float = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
