"""Small, serialisable latency counters for one task execution."""

from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass
class NodeMetrics:
    """Accumulates monotonic durations without mixing in wall-clock timestamps."""

    started: float
    llm_time_seconds: float = 0.0
    tool_wait_time_seconds: float = 0.0
    retry_time_seconds: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0

    @classmethod
    def start(cls) -> "NodeMetrics":
        return cls(started=time.monotonic())

    def snapshot(self, usage: dict[str, int] | None = None) -> dict:
        usage = usage or {}
        return {
            "llm_time_ms": round(self.llm_time_seconds * 1000, 2),
            "tool_wait_time_ms": round(self.tool_wait_time_seconds * 1000, 2),
            "retry_time_ms": round(self.retry_time_seconds * 1000, 2),
            "total_time_ms": round((time.monotonic() - self.started) * 1000, 2),
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
