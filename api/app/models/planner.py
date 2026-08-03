from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.schemas import Plan
from app.services.graph_store import ALLOWED_GRAPH_TEMPLATES

PRIMARY_INTENT_VALUES = frozenset(
    {
        "chitchat",
        "factoid",
        "recommendation",
        "planning",
        "comparison",
        "logistics",
        "meta",
    }
)

SLOT_INTENT_VALUES = frozenset(
    {
        "factoid",
        "recommendation",
        "planning",
        "comparison",
        "logistics",
    }
)

PrimaryIntent = Literal[
    "chitchat",
    "factoid",
    "recommendation",
    "planning",
    "comparison",
    "logistics",
    "meta",
]

SlotIntent = Literal[
    "factoid",
    "recommendation",
    "planning",
    "comparison",
    "logistics",
]


class IntentSlot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: SlotIntent
    cities: list[str] = Field(default_factory=list)

    @field_validator("cities", mode="before")
    @classmethod
    def _clean_cities(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            name = item.strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
        return out


class SearchQuery(BaseModel):
    """Vector search phrase with optional city payload filter.

    city=None / omitted / \"ANY\" means no city filter (global top-k).
    """

    model_config = ConfigDict(extra="ignore")

    q: str
    city: str | None = None

    @field_validator("q", mode="before")
    @classmethod
    def _clean_q(cls, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()

    @field_validator("city", mode="before")
    @classmethod
    def _clean_city(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        name = value.strip()
        if not name or name.upper() == "ANY":
            return None
        return name


class GraphQuery(BaseModel):
    """Allowlisted Neo4j template + params (neo4j.md)."""

    model_config = ConfigDict(extra="ignore")

    template: str
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("template", mode="before")
    @classmethod
    def _clean_template(cls, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()

    @field_validator("params", mode="before")
    @classmethod
    def _clean_params(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return value


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary_intent: PrimaryIntent = "factoid"
    in_scope: bool = True
    intents: list[IntentSlot] = Field(default_factory=list)
    prompt_language: str = "en"
    plan: Plan
    search_queries: list[SearchQuery] = Field(default_factory=list)
    deep_search: bool = False
    search_museums: bool = False
    search_artifacts: bool = False
    museum_search_queries: list[SearchQuery] = Field(default_factory=list)
    artifact_search_queries: list[SearchQuery] = Field(default_factory=list)
    is_hotel_search: bool = False
    hotel_search_query: str = ""
    is_flight_search: bool = False
    flight_search_query: str = ""
    needs_graph: bool = False
    graph_queries: list[GraphQuery] = Field(default_factory=list)

    @model_validator(mode="after")
    def _align_out_of_scope(self) -> PlannerOutput:
        if self.in_scope:
            return self
        self.primary_intent = "meta"
        self.intents = []
        self.search_queries = []
        self.deep_search = False
        self.search_museums = False
        self.search_artifacts = False
        self.museum_search_queries = []
        self.artifact_search_queries = []
        self.is_hotel_search = False
        self.hotel_search_query = ""
        self.is_flight_search = False
        self.flight_search_query = ""
        self.needs_graph = False
        self.graph_queries = []
        return self

    @model_validator(mode="after")
    def _align_primary_with_slots(self) -> PlannerOutput:
        if not self.in_scope or self.primary_intent in ("chitchat", "meta"):
            return self
        slot_names = {slot.intent for slot in self.intents}
        if self.primary_intent not in slot_names:
            self.intents = [
                IntentSlot(intent=self.primary_intent, cities=[]),  # type: ignore[arg-type]
                *self.intents,
            ]
        return self

    @model_validator(mode="after")
    def _align_deep_search_flags(self) -> PlannerOutput:
        if not self.in_scope or self.primary_intent in ("chitchat", "meta"):
            self.deep_search = False
            self.search_museums = False
            self.search_artifacts = False
            self.museum_search_queries = []
            self.artifact_search_queries = []
            return self
        if not self.search_museums:
            self.museum_search_queries = []
        if not self.search_artifacts:
            self.artifact_search_queries = []
        self.deep_search = bool(self.search_museums or self.search_artifacts)
        return self

    @model_validator(mode="after")
    def _align_graph_flags(self) -> PlannerOutput:
        if not self.in_scope or self.primary_intent in ("chitchat", "meta"):
            self.needs_graph = False
            self.graph_queries = []
            return self
        # Drop unknown templates.
        self.graph_queries = [
            q for q in self.graph_queries if q.template in ALLOWED_GRAPH_TEMPLATES
        ]
        if self.graph_queries:
            self.needs_graph = True
        if not self.needs_graph:
            self.graph_queries = []
        return self

    @model_validator(mode="after")
    def _drop_empty_queries(self) -> PlannerOutput:
        self.search_queries = [item for item in self.search_queries if item.q]
        self.museum_search_queries = [item for item in self.museum_search_queries if item.q]
        self.artifact_search_queries = [
            item for item in self.artifact_search_queries if item.q
        ]
        return self


def _sanitize_graph_queries(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        template = item.get("template")
        if not isinstance(template, str) or not template.strip():
            continue
        name = template.strip()
        if name not in ALLOWED_GRAPH_TEMPLATES:
            continue
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        out.append({"template": name, "params": params})
    return out


def _sanitize_intents(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        intent = item.get("intent")
        if not isinstance(intent, str) or intent not in SLOT_INTENT_VALUES:
            continue
        if intent in seen:
            for slot in slots:
                if slot["intent"] == intent:
                    existing = slot.get("cities") or []
                    extra = item.get("cities") if isinstance(item.get("cities"), list) else []
                    merged = list(existing)
                    for city in extra:
                        if isinstance(city, str) and city.strip() and city not in merged:
                            merged.append(city.strip())
                    slot["cities"] = merged
                    break
            continue
        seen.add(intent)
        slots.append({"intent": intent, "cities": item.get("cities") or []})
    return slots


def _sanitize_primary_intent(raw: Any, intents: list[dict[str, Any]]) -> str:
    if isinstance(raw, str) and raw in PRIMARY_INTENT_VALUES:
        return raw
    if intents:
        return intents[0]["intent"]
    return "factoid"


def _sanitize_search_queries(raw: Any) -> list[dict[str, Any]]:
    """Accept legacy string lists or {q, city} objects."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            q = item.strip()
            if q:
                out.append({"q": q, "city": None})
            continue
        if not isinstance(item, dict):
            continue
        q = item.get("q")
        if not isinstance(q, str) or not q.strip():
            q = item.get("query") if isinstance(item.get("query"), str) else ""
        if not isinstance(q, str) or not q.strip():
            continue
        city = item.get("city")
        out.append({"q": q.strip(), "city": city})
    return out


def parse_planner_output(data: dict[str, Any]) -> PlannerOutput:
    intents = _sanitize_intents(data.get("intents"))
    primary = data.get("primary_intent")
    if primary is None and isinstance(data.get("intent"), str):
        primary = data["intent"]
    primary_intent = _sanitize_primary_intent(primary, intents)

    needs_graph = bool(data.get("needs_graph"))
    graph_queries = _sanitize_graph_queries(data.get("graph_queries"))
    # Legacy neo4j_data → places_in_city fallback (keys only).
    legacy = data.get("neo4j_data")
    if not graph_queries and isinstance(legacy, dict) and legacy:
        for city_name in legacy:
            if isinstance(city_name, str) and city_name.strip():
                graph_queries.append(
                    {
                        "template": "places_in_city",
                        "params": {"city_slug": city_name.strip()},
                    }
                )
        if graph_queries:
            needs_graph = True

    sanitized = {
        **data,
        "primary_intent": primary_intent,
        "intents": intents,
        "needs_graph": needs_graph,
        "graph_queries": graph_queries,
        "search_queries": _sanitize_search_queries(data.get("search_queries")),
        "museum_search_queries": _sanitize_search_queries(
            data.get("museum_search_queries")
        ),
        "artifact_search_queries": _sanitize_search_queries(
            data.get("artifact_search_queries")
        ),
    }
    if not sanitized.get("is_hotel_search"):
        sanitized["hotel_search_query"] = ""
    if not sanitized.get("is_flight_search"):
        sanitized["flight_search_query"] = ""
    if not sanitized.get("search_museums"):
        sanitized["museum_search_queries"] = []
    if not sanitized.get("search_artifacts"):
        sanitized["artifact_search_queries"] = []
    return PlannerOutput.model_validate(sanitized)


def cities_from_intents(intents: list[dict[str, Any]] | list[IntentSlot] | None) -> list[str]:
    """Union of cities across intent slots (stable, de-duped)."""
    if not intents:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in intents:
        if isinstance(item, IntentSlot):
            cities = item.cities
        elif isinstance(item, dict):
            cities = item.get("cities") or []
        else:
            continue
        for city in cities:
            if not isinstance(city, str):
                continue
            name = city.strip()
            key = name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            out.append(name)
    return out


def planner_state_update(planner: PlannerOutput, raw: str) -> dict[str, Any]:
    dump = planner.model_dump()
    return {
        "planner": dump,
        "primary_intent": planner.primary_intent,
        "in_scope": planner.in_scope,
        "intents": dump["intents"],
        "prompt_language": planner.prompt_language,
        "plan": dump["plan"],
        "search_queries": dump["search_queries"],
        "deep_search": planner.deep_search,
        "search_museums": planner.search_museums,
        "search_artifacts": planner.search_artifacts,
        "museum_search_queries": dump["museum_search_queries"],
        "artifact_search_queries": dump["artifact_search_queries"],
        "is_hotel_search": planner.is_hotel_search,
        "hotel_search_query": planner.hotel_search_query,
        "is_flight_search": planner.is_flight_search,
        "flight_search_query": planner.flight_search_query,
        "needs_graph": planner.needs_graph,
        "graph_queries": dump["graph_queries"],
        "planner_raw": raw,
    }
