"""旅行展示领域（Phase D 内部模型：Synthesizer 的结构化输出，未知值保留 None）。"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SAttraction(BaseModel):
    name: str = Field(min_length=1)
    address: str = ""
    poi_id: Optional[str] = None
    location_str: Optional[str] = None  # 仅允许来自证据坐标池的 "lng,lat"；否则必为 None
    visit_duration: Optional[int] = None
    description: str = ""
    category: Optional[str] = None
    ticket_price: Optional[int] = None
    source: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)


class SMeal(BaseModel):
    type: str = "meal"  # breakfast/lunch/dinner/snack
    name: str = ""
    description: str = ""
    estimated_cost: Optional[int] = None


class SHotel(BaseModel):
    name: str = ""
    address: str = ""
    location_str: Optional[str] = None
    price_range: Optional[str] = None
    rating: Optional[str] = None
    estimated_cost: Optional[int] = None


class SHotelCandidate(BaseModel):
    """Evidence-backed lodging candidate; never represents a live OTA quote."""
    name: str = ""
    address: str = ""
    rating: Optional[str] = None
    distance_summary: str = ""
    commute_summary: str = ""
    selection_reason: str = ""
    rejection_reason: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)


class SDay(BaseModel):
    date: str
    day_index: int
    description: str = ""
    transportation: str = ""
    accommodation: str = ""
    hotel: Optional[SHotel] = None
    attractions: list[SAttraction] = Field(default_factory=list)
    meals: list[SMeal] = Field(default_factory=list)


class SWeather(BaseModel):
    date: str
    day_weather: Optional[str] = None
    night_weather: Optional[str] = None
    day_temp: Optional[int] = None
    night_temp: Optional[int] = None
    kind: Optional[str] = None
    source: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)


class SBudget(BaseModel):
    total_attractions: Optional[int] = None
    total_hotels: Optional[int] = None
    total_meals: Optional[int] = None
    total_transportation: Optional[int] = None
    total: Optional[int] = None


class SynthesizedPlan(BaseModel):
    """Synthesizer 的输出契约（只含语义内容；溯源/修订号由装配层附加）。"""

    city: str = Field(min_length=1)
    start_date: str
    end_date: str
    days: list[SDay] = Field(min_length=1)
    overall_suggestions: str = ""
    weather_info: list[SWeather] = Field(default_factory=list)
    budget: Optional[SBudget] = None
    hotel_candidates: list[SHotelCandidate] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
