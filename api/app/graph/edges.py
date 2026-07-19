from app.graph.state import GraphState

_SKIP_RETRIEVAL_INTENTS = frozenset({"chitchat", "meta"})


def retrieval_router(state: GraphState) -> str:
    """Route on primary_intent: chitchat/meta skip retrieval, others go to vector search."""
    if state.get("error"):
        return "format"
    if state.get("primary_intent", "") in _SKIP_RETRIEVAL_INTENTS:
        return "chitchat"
    return "vector_retrieval"
