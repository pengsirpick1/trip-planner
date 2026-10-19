"""Deterministic reserve estimates kept outside Provider adapters."""
from __future__ import annotations
from decimal import Decimal, ROUND_CEILING

def reserve_estimate(pricing, prompt_tokens: int, completion_tokens: int) -> int:
    raw = (Decimal(int(prompt_tokens)) * pricing.input_points_per_million
           + Decimal(int(completion_tokens)) * pricing.output_points_per_million) / Decimal(1_000_000)
    return max(pricing.minimum_reserve_points,
               int((raw * pricing.reserve_safety_factor).to_integral_value(rounding=ROUND_CEILING)))
