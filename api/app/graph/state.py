import operator
from typing import Annotated, Any, TypedDict


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
    # Retrieval / synthesis contract — three distinct responsibilities:
    #   candidate_chunks  everything retrieval found (appended by each
    #                     retrieval node; never pruned for presentation)
    #   selected_chunks   the evidence handed to synthesis
    #   sources           the sources actually cited in the final answer
    candidate_chunks: Annotated[list[dict[str, Any]], operator.add]
    selected_chunks: list[dict[str, Any]]
    # Rerank telemetry — skipped vs applied vs fallback stay distinct.
    rerank_status: str
    rerank_reason: str
    graph_contexts: list[dict[str, str]]
    sources: list[dict[str, Any]]
    answer: str
    error: str | None
