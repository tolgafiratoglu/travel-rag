from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str


class Source(BaseModel):
    text: str
    city_name: str
    section: str
    source_url: str = ""
    score: float
    source: str = ""
    name: str = ""


class PlanStep(BaseModel):
    id: str
    label: str
    kind: str
    status: str = "idle"


class Plan(BaseModel):
    summary: str
    steps: list[PlanStep]


class SearchQueryRef(BaseModel):
    q: str
    city: str | None = None


class ChatResponse(BaseModel):
    answer: str = ""
    sources: list[Source] = Field(default_factory=list)
    primary_intent: str | None = None
    intents: list[dict[str, Any]] = Field(default_factory=list)
    prompt_language: str | None = None
    plan: Plan | None = None
    search_queries: list[SearchQueryRef] = Field(default_factory=list)
    deep_search: bool = False
    search_museums: bool = False
    search_artifacts: bool = False
    museum_search_queries: list[SearchQueryRef] = Field(default_factory=list)
    artifact_search_queries: list[SearchQueryRef] = Field(default_factory=list)
    is_hotel_search: bool = False
    hotel_search_query: str = ""
    is_flight_search: bool = False
    flight_search_query: str = ""
    neo4j_data: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    planner_raw: str | None = None
