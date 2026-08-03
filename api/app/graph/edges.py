from app.graph.state import GraphState

_SKIP_RETRIEVAL_INTENTS = frozenset({"chitchat", "meta"})


def after_guard_router(state: GraphState) -> str:
    """Block graph after safety_guard unless label is Safe."""
    if state.get("error"):
        return "format"
    return "planner_json"


def retrieval_router(state: GraphState) -> str:
    """Route after planner: out_of_scope | clarify | chitchat | graph | deep | vector."""
    if state.get("error"):
        return "format"
    if state.get("in_scope") is False:
        return "out_of_scope"
    if state.get("needs_clarification"):
        return "clarify"
    if state.get("primary_intent", "") in _SKIP_RETRIEVAL_INTENTS:
        return "chitchat"
    if state.get("needs_graph"):
        return "graph_retrieval"
    if state.get("deep_search"):
        return "deep_retrieval"
    return "vector_retrieval"


def after_graph_router(state: GraphState) -> str:
    """After Neo4j templates: optional deep POI, then always guide vector."""
    if state.get("error"):
        return "format"
    if state.get("deep_search"):
        return "deep_retrieval"
    return "vector_retrieval"
