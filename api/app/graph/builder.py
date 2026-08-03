from langgraph.graph import END, START, StateGraph

from app.graph.edges import after_graph_router, after_guard_router, retrieval_router
from app.graph.nodes import (
    chitchat_node,
    deep_retrieval_node,
    format_node,
    graph_retrieval_node,
    intake_node,
    out_of_scope_node,
    planner_json_node,
    safety_guard_node,
    synthesize_node,
    vector_retrieval_node,
)
from app.graph.state import GraphState


def build_graph():
    """
    Graph aligned with neo4j.md:

      intake → safety_guard → planner_json → retrieval_router
        ├── in_scope false → out_of_scope → format → END
        ├── chitchat/meta → chitchat → format → END
        ├── needs_graph → graph_retrieval → (deep?) → vector → synthesize
        ├── deep_search → deep_retrieval → vector → synthesize
        └── else → vector_retrieval → synthesize → format → END
    """
    g = StateGraph(GraphState)

    g.add_node("intake", intake_node)
    g.add_node("safety_guard", safety_guard_node)
    g.add_node("planner_json", planner_json_node)
    g.add_node("out_of_scope", out_of_scope_node)
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
    g.add_edge("out_of_scope", "format")
    g.add_edge("chitchat", "format")
    g.add_conditional_edges("graph_retrieval", after_graph_router)
    g.add_edge("deep_retrieval", "vector_retrieval")
    g.add_edge("vector_retrieval", "synthesize")
    g.add_edge("synthesize", "format")
    g.add_edge("format", END)

    return g.compile()
