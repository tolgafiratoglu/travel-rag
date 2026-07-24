from app.graph.state import GraphState

_SKIP_RETRIEVAL_INTENTS = frozenset({"chitchat", "meta"})


def after_guard_router(state: GraphState) -> str:
    """Block graph after safety_guard unless label is Safe."""
    if state.get("error"):
        return "format"
    return "planner_json"


def retrieval_router(state: GraphState) -> str:
    """Route: chitchat/meta skip retrieval; deep_search → museums/sites then WV."""
    if state.get("error"):
        return "format"
    if state.get("primary_intent", "") in _SKIP_RETRIEVAL_INTENTS:
        return "chitchat"
    if state.get("deep_search"):
        return "deep_retrieval"
    return "vector_retrieval"
