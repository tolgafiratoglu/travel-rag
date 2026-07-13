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


class PlanStep(BaseModel):
    id: str
    label: str
    kind: str
    status: str = "idle"


class Plan(BaseModel):
    summary: str
    steps: list[PlanStep]


class ChatResponse(BaseModel):
    answer: str = ""
    sources: list[Source] = Field(default_factory=list)
    intent: str | None = None
    entities: dict[str, Any] | None = None
    assumptions: list[str] = Field(default_factory=list)
    plan: Plan | None = None
    search_queries: list[str] = Field(default_factory=list)
    answer_style: str | None = None
    error: str | None = None
    planner_raw: str | None = None
