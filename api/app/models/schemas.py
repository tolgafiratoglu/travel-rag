from pydantic import BaseModel


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
