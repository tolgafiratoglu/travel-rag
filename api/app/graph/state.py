from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    question: str
    history: list[dict[str, str]]
    include_history: bool
    planner: dict[str, Any]
    primary_intent: str
    in_scope: bool
    needs_clarification: bool
    clarification: dict[str, Any]
    intents: list[dict[str, Any]]
    suggestions: list[str]
    prompt_language: str
    plan: dict[str, Any]
    search_queries: list[str]
    deep_search: bool
    search_museums: bool
    search_artifacts: bool
    museum_search_queries: list[str]
    artifact_search_queries: list[str]
    is_hotel_search: bool
    hotel_search_query: str
    is_flight_search: bool
    flight_search_query: str
    needs_graph: bool
    graph_queries: list[dict[str, Any]]
    planner_raw: str
    guard_label: str | None
    guard_raw: str
    museum_chunks: list[dict[str, Any]]
    artifact_chunks: list[dict[str, Any]]
    vector_chunks: list[dict[str, Any]]
    graph_contexts: list[dict[str, str]]
    sources: list[dict[str, Any]]
    answer: str
    error: str | None
