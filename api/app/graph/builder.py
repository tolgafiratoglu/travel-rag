from langgraph.graph import END, START, StateGraph

from app.graph.edges import retrieval_router
from app.graph.nodes import (
    chitchat_node,
    format_node,
    intake_node,
    planner_json_node,
    synthesize_node,
    vector_retrieval_node,
)
from app.graph.state import GraphState


def build_graph():
    """
    Phase 1 graph with retrieval router:

      intake → planner_json → retrieval_router
        ├── chitchat/meta   → chitchat       → format → END
        └── everything else → vector_retrieval → synthesize → format → END
    """
    g = StateGraph(GraphState)

    g.add_node("intake", intake_node)
    g.add_node("planner_json", planner_json_node)
    g.add_node("chitchat", chitchat_node)
    g.add_node("vector_retrieval", vector_retrieval_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("format", format_node)

    g.add_edge(START, "intake")
    g.add_edge("intake", "planner_json")
    g.add_conditional_edges("planner_json", retrieval_router)
    g.add_edge("chitchat", "format")
    g.add_edge("vector_retrieval", "synthesize")
    g.add_edge("synthesize", "format")
    g.add_edge("format", END)

    return g.compile()
