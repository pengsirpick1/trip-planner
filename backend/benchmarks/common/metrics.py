from __future__ import annotations

import math
import statistics
from typing import Iterable


def summary(values: Iterable[float]) -> dict[str, float | None]:
    values = list(values)
    if not values:
        return {key: None for key in ("mean", "p50", "p95", "min", "max", "stdev")}
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "mean": round(statistics.fmean(values), 3),
        "p50": round(statistics.median(values), 3),
        "p95": round(ordered[index], 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "stdev": round(statistics.stdev(values), 3) if len(values) > 1 else 0.0,
    }


def reduction(baseline: float, candidate: float) -> float | None:
    if baseline <= 0:
        return None
    return round((1 - candidate / baseline) * 100, 3)


def speedup(baseline: float, candidate: float) -> float | None:
    if candidate <= 0:
        return None
    return round(baseline / candidate, 3)


def estimate_tokens(text: str) -> int:
    """Stable offline proxy: four UTF-8 bytes/characters per token on average.

    It is deliberately reported as an estimate, never provider token usage.
    """
    return math.ceil(len(text.encode("utf-8")) / 4)

