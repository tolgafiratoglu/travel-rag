from langgraph.graph import END, START, StateGraph

from app.graph.edges import after_guard_router, retrieval_router
from app.graph.nodes import (
    chitchat_node,
    deep_retrieval_node,
    format_node,
    graph_retrieval_node,
    intake_node,
    planner_json_node,
    safety_guard_node,
    synthesize_node,
    vector_retrieval_node,
)
from app.graph.state import GraphState


def build_graph():
    """
    Phase 2 graph with safety guard + retrieval router:

      intake → safety_guard → (blocked) format → END
                           → planner_json → retrieval_router
        ├── chitchat/meta → chitchat → format → END
        ├── deep_search → deep_retrieval → vector_retrieval → graph → synthesize → format
        └── else → vector_retrieval → graph_retrieval → synthesize → format → END
    """
    g = StateGraph(GraphState)

    g.add_node("intake", intake_node)
    g.add_node("safety_guard", safety_guard_node)
    g.add_node("planner_json", planner_json_node)
    g.add_node("chitchat", chitchat_node)
    g.add_node("deep_retrieval", deep_retrieval_node)
    g.add_node("vector_retrieval", vector_retrieval_node)
    g.add_node("graph_retrieval", graph_retrieval_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("format", format_node)

    g.add_edge(START, "intake")
    g.add_edge("intake", "safety_guard")
    g.add_conditional_edges("safety_guard", after_guard_router)
    g.add_conditional_edges("planner_json", retrieval_router)
    g.add_edge("chitchat", "format")
    g.add_edge("deep_retrieval", "vector_retrieval")
    g.add_edge("vector_retrieval", "graph_retrieval")
    g.add_edge("graph_retrieval", "synthesize")
    g.add_edge("synthesize", "format")
    g.add_edge("format", END)

    return g.compile()
