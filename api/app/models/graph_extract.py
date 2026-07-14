from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.graph.neo4j_schema import (
    NEO4J_NODE_LABELS_FROZEN,
    NEO4J_REL_TYPES_FROZEN,
    V1_DISABLED_REL_TYPES,
    Neo4jNodeLabel,
    Neo4jRelType,
)

logger = logging.getLogger(__name__)

FORBIDDEN_GEMINI_LABELS = frozenset({"City", "Country"})


class GraphNode(BaseModel):
    label: Neo4jNodeLabel
    graph_id: str
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    from_graph_id: str
    to_graph_id: str
    type: Neo4jRelType
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _rel_allowed(cls, value: str) -> str:
        if value in V1_DISABLED_REL_TYPES:
            raise ValueError(f"Relationship type disabled in v1: {value}")
        return value


class GraphExtract(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


def validate_graph_extract(
    extract: GraphExtract,
    *,
    city_graph_id: str,
    country_graph_id: str,
    strict: bool = False,
) -> GraphExtract:
    """Drop invalid nodes/edges or raise when strict=True."""
    valid_nodes: list[GraphNode] = []
    for node in extract.nodes:
        if node.label not in NEO4J_NODE_LABELS_FROZEN:
            msg = f"Unknown node label: {node.label}"
            if strict:
                raise ValueError(msg)
            logger.warning(msg)
            continue
        if node.label in FORBIDDEN_GEMINI_LABELS:
            if strict:
                raise ValueError(f"Forbidden Gemini node label: {node.label}")
            continue
        valid_nodes.append(node)

    known_ids = {
        city_graph_id,
        country_graph_id,
        *(node.graph_id for node in valid_nodes),
    }

    valid_edges: list[GraphEdge] = []
    for edge in extract.edges:
        if edge.type not in NEO4J_REL_TYPES_FROZEN:
            msg = f"Unknown relationship type: {edge.type}"
            if strict:
                raise ValueError(msg)
            logger.warning(msg)
            continue
        if edge.from_graph_id not in known_ids:
            msg = f"Unknown edge source graph_id: {edge.from_graph_id}"
            if strict:
                raise ValueError(msg)
            logger.warning(msg)
            continue
        if edge.to_graph_id not in known_ids:
            msg = f"Unknown edge target graph_id: {edge.to_graph_id}"
            if strict:
                raise ValueError(msg)
            logger.warning(msg)
            continue
        valid_edges.append(edge)

    return GraphExtract(nodes=valid_nodes, edges=valid_edges)
