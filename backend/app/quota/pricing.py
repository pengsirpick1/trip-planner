from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
@dataclass(frozen=True)
class Pricing:
    version: str; provider: str; model: str; input_points_per_million: int; output_points_per_million: int; minimum_reserve_points: int = 1; reserve_safety_factor: Decimal = Decimal('1.20')
DEFAULT_PRICING = Pricing('v1','openai_compatible','default',1000,2000)
class PricingRegistry:
    def __init__(self, entries=None): self.entries=entries or {('openai_compatible','default'): DEFAULT_PRICING}
    def get(self, provider, model): return self.entries.get((provider, model)) or self.entries[(provider,'default')]
def points(pricing, prompt_tokens, completion_tokens):
    raw=Decimal(prompt_tokens)*pricing.input_points_per_million/Decimal(1_000_000)+Decimal(completion_tokens)*pricing.output_points_per_million/Decimal(1_000_000)
    return int(raw.to_integral_value(rounding=ROUND_CEILING))
