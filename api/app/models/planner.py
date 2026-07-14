from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas import Plan

NEO4J_RELATION_KEYS = frozenset({
    "Country",
    "Region",
    "City",
    "District",
    "Attraction",
    "Activity",
    "Airport",
    "TransportHub",
    "Route",
    "TravelTheme",
    "Season",
})


class PlannerEntities(BaseModel):
    cities: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    theme: list[str] = Field(default_factory=list)
    audience: list[str] = Field(default_factory=list)
    budget: str | None = None
    duration: str | None = None


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: str = "factoid"
    prompt_language: str = "en"
    entities: PlannerEntities = Field(default_factory=PlannerEntities)
    assumptions: list[str] = Field(default_factory=list)
    plan: Plan
    search_queries: list[str] = Field(default_factory=list)
    is_hotel_search: bool = False
    hotel_search_query: str = ""
    is_flight_search: bool = False
    flight_search_query: str = ""
    neo4j_data: dict[str, str] = Field(default_factory=dict)


def _sanitize_neo4j_data(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): value
        for key, value in raw.items()
        if isinstance(value, str) and value in NEO4J_RELATION_KEYS
    }


def parse_planner_output(data: dict[str, Any]) -> PlannerOutput:
    sanitized = {**data, "neo4j_data": _sanitize_neo4j_data(data.get("neo4j_data"))}
    if not sanitized.get("is_hotel_search"):
        sanitized["hotel_search_query"] = ""
    if not sanitized.get("is_flight_search"):
        sanitized["flight_search_query"] = ""
    return PlannerOutput.model_validate(sanitized)


def planner_state_update(planner: PlannerOutput, raw: str) -> dict[str, Any]:
    dump = planner.model_dump()
    return {
        "planner": dump,
        "intent": planner.intent,
        "prompt_language": planner.prompt_language,
        "entities": dump["entities"],
        "assumptions": planner.assumptions,
        "plan": dump["plan"],
        "search_queries": planner.search_queries,
        "is_hotel_search": planner.is_hotel_search,
        "hotel_search_query": planner.hotel_search_query,
        "is_flight_search": planner.is_flight_search,
        "flight_search_query": planner.flight_search_query,
        "neo4j_data": planner.neo4j_data,
        "planner_raw": raw,
    }
