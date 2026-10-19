"""能力目录（迁自 travel_agent/agent/planning/capabilities.py）。"""

TRANSPORT_RESEARCH = "transport_research"
ACCOMMODATION_RESEARCH = "accommodation_research"
PLACE_RESEARCH = "place_research"
FOOD_RESEARCH = "food_research"
WEATHER_RESEARCH = "weather_research"
GEOSPATIAL_REASONING = "geospatial_reasoning"
BUDGET_REASONING = "budget_reasoning"
ITINERARY_SYNTHESIS = "itinerary_synthesis"

DEFAULT_CAPABILITIES = {
    TRANSPORT_RESEARCH,
    ACCOMMODATION_RESEARCH,
    PLACE_RESEARCH,
    FOOD_RESEARCH,
    WEATHER_RESEARCH,
    GEOSPATIAL_REASONING,
    BUDGET_REASONING,
    ITINERARY_SYNTHESIS,
}
