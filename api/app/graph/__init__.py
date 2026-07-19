"""LangGraph package."""

__all__ = ["build_graph"]


def build_graph():
    from app.graph.builder import build_graph as _build_graph

    return _build_graph()
