from app.graph.state import GraphState

_CHITCHAT_INTENTS = {"chitchat", "meta"}


def retrieval_router(state: GraphState) -> str:
    """Route based on intent: chitchat/meta skip retrieval, others go to vector search."""
    if state.get("error"):
        return "format"
    if state.get("intent", "") in _CHITCHAT_INTENTS:
        return "chitchat"
    return "vector_retrieval"
