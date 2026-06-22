from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    question: str
    history: list[dict[str, str]]
    intent: str
    entities: dict[str, Any]
    assumptions: list[str]
    plan: dict[str, Any]
    search_queries: list[str]
    answer_style: str
    planner_raw: str
    error: str | None
