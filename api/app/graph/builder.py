from langgraph.graph import END, START, StateGraph

from app.graph.nodes import intake_node, planner_json_node
from app.graph.state import GraphState


def build_planner_graph():
    """Phase 1 step 1: intake → planner_json only."""
    graph = StateGraph(GraphState)
    graph.add_node("intake", intake_node)
    graph.add_node("planner_json", planner_json_node)
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "planner_json")
    graph.add_edge("planner_json", END)
    return graph.compile()
