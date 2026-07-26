from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.graph.neo4j_schema import (
    LLM_NODE_LABELS,
    LLM_REL_TYPES,
    NEO4J_NODE_LABELS_FROZEN,
    NEO4J_REL_TYPES_FROZEN,
    V1_DISABLED_REL_TYPES,
    Neo4jNodeLabel,
    Neo4jRelType,
)

logger = logging.getLogger(__name__)

# Deterministic anchors — LLM must not recreate the current city/country.
FORBIDDEN_GEMINI_LABELS = frozenset({"Country"})


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


def _clean_edge_props(props: dict[str, Any]) -> dict[str, Any]:
    """Drop invented durations: keep only explicit numbers; never coerce missing to 0."""
    out = dict(props or {})
    for key in ("duration_min", "duration_max", "one_way_minutes", "confidence"):
        if key not in out:
            continue
        val = out[key]
        if val is None or val == "":
            out.pop(key, None)
            continue
        try:
            out[key] = float(val) if key == "confidence" else int(val)
        except (TypeError, ValueError):
            out.pop(key, None)
    return out


def validate_graph_extract(
    extract: GraphExtract,
    *,
    city_graph_id: str,
    country_graph_id: str,
    strict: bool = False,
    llm_only: bool = True,
) -> GraphExtract:
    """Drop invalid nodes/edges or raise when strict=True.

    When llm_only=True (relation pass): only Place/Airport/City nodes and
    SERVED_BY_AIRPORT / CONNECTED_BY / DAY_TRIP_FROM edges. Current city must
    not be re-emitted as a node.
    """
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
        if llm_only and node.label not in LLM_NODE_LABELS:
            logger.warning("Skipping non-LLM node label: %s", node.label)
            continue
        if node.graph_id == city_graph_id or node.graph_id == country_graph_id:
            logger.warning("Skipping anchor graph_id from LLM: %s", node.graph_id)
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
        if llm_only and edge.type not in LLM_REL_TYPES:
            logger.warning("Skipping non-LLM edge type: %s", edge.type)
            continue
        if edge.type in ("IN_COUNTRY", "IN_CITY"):
            # Deterministic only
            logger.warning("Skipping deterministic edge type from LLM: %s", edge.type)
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
        cleaned = _clean_edge_props(edge.properties)
        valid_edges.append(
            GraphEdge(
                from_graph_id=edge.from_graph_id,
                to_graph_id=edge.to_graph_id,
                type=edge.type,
                properties=cleaned,
            )
        )

    return GraphExtract(nodes=valid_nodes, edges=valid_edges)
