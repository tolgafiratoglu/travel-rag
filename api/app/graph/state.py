from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    question: str
    history: list[dict[str, str]]
    planner: dict[str, Any]
    intent: str
    prompt_language: str
    entities: dict[str, Any]
    assumptions: list[str]
    plan: dict[str, Any]
    search_queries: list[str]
    is_hotel_search: bool
    hotel_search_query: str
    is_flight_search: bool
    flight_search_query: str
    neo4j_data: dict[str, str]
    planner_raw: str
    vector_chunks: list[dict[str, Any]]
    graph_contexts: list[dict[str, str]]
    sources: list[dict[str, Any]]
    answer: str
    error: str | None
