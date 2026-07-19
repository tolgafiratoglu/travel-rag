"""Neo4j upsert helpers for Wikivoyage graph ingest."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from neo4j import Driver

from app.graph.neo4j_schema import (
    ANCHOR_REL_TYPE,
    ManagedScope,
    managed_scope_for_label,
)
from app.models.graph_extract import GraphExtract, GraphEdge, GraphNode

WV_CONSTRAINTS = """
CREATE CONSTRAINT wv_entity_graph_id IF NOT EXISTS
FOR (n:WvEntity) REQUIRE n.graph_id IS UNIQUE
"""


@dataclass
class IngestCounts:
    nodes_by_label: dict[str, int] = field(default_factory=dict)
    edges_by_type: dict[str, int] = field(default_factory=dict)
    nodes_deleted: int = 0
    edges_deleted: int = 0

    @property
    def nodes_written(self) -> int:
        return sum(self.nodes_by_label.values())

    @property
    def edges_written(self) -> int:
        return sum(self.edges_by_type.values())

    def bump_node(self, label: str) -> None:
        self.nodes_by_label[label] = self.nodes_by_label.get(label, 0) + 1

    def bump_edge(self, rel_type: str) -> None:
        self.edges_by_type[rel_type] = self.edges_by_type.get(rel_type, 0) + 1


def ensure_constraints(driver: Driver) -> None:
    with driver.session() as session:
        for stmt in WV_CONSTRAINTS.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                session.run(stmt)


def _clean_props(props: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in props.items() if v is not None}


def build_city_anchor(row: dict, city_gid: str) -> GraphNode:
    props: dict[str, Any] = {
        "slug": row.get("page_slug"),
        "wikidata_qid": row.get("wikidata_qid"),
        "source_url": row["source_url"],
        "managed_scope": ManagedScope.ANCHOR.value,
        "source": "wikivoyage",
    }
    if row.get("latitude") is not None:
        props["lat"] = float(row["latitude"])
    if row.get("longitude") is not None:
        props["lon"] = float(row["longitude"])
    return GraphNode(
        label="City",
        graph_id=city_gid,
        name=row["page_name"],
        properties=_clean_props(props),
    )


def build_country_anchor(country_name: str, country_gid: str) -> GraphNode:
    return GraphNode(
        label="Country",
        graph_id=country_gid,
        name=country_name,
        properties={
            "slug": country_gid.removeprefix("country:"),
            "managed_scope": ManagedScope.GLOBAL.value,
            "source": "wikivoyage",
        },
    )


def delete_city_derived(driver: Driver, city_gid: str) -> tuple[int, int]:
    """Remove Gemini-derived nodes and wikivoyage edges for one city. City anchor kept."""
    edges_deleted = 0
    with driver.session() as session:
        city_edge_result = session.run(
            """
            MATCH (c:City {graph_id: $city_gid})-[r]->(t)
            WHERE NOT (type(r) = $anchor_rel AND t:Country)
              AND coalesce(r.source, '') = 'wikivoyage'
            DELETE r
            RETURN count(r) AS deleted
            """,
            city_gid=city_gid,
            anchor_rel=ANCHOR_REL_TYPE,
        )
        edges_deleted += city_edge_result.single()["deleted"]

        edge_result = session.run(
            """
            MATCH (n:WvEntity {source_city_graph_id: $city_gid, managed_scope: $scope})
            WHERE NOT n:City
            MATCH (n)-[r]-()
            DELETE r
            RETURN count(r) AS deleted
            """,
            city_gid=city_gid,
            scope=ManagedScope.CITY.value,
        )
        edges_deleted += edge_result.single()["deleted"]

        node_result = session.run(
            """
            MATCH (n:WvEntity {source_city_graph_id: $city_gid, managed_scope: $scope})
            WHERE NOT n:City
            DETACH DELETE n
            RETURN count(n) AS deleted
            """,
            city_gid=city_gid,
            scope=ManagedScope.CITY.value,
        )
        nodes_deleted = node_result.single()["deleted"]
    return nodes_deleted, edges_deleted


def merge_node(
    session,
    node: GraphNode,
    *,
    city_gid: str,
    source_url: str,
) -> None:
    scope = managed_scope_for_label(node.label)
    props = {
        **node.properties,
        "name": node.name,
        "graph_id": node.graph_id,
        "managed_scope": scope.value,
        "source": "wikivoyage",
    }
    if scope == ManagedScope.CITY:
        props["source_city_graph_id"] = city_gid
        props["wv_source_url"] = source_url
    if scope == ManagedScope.ANCHOR:
        props["wv_source_url"] = source_url

    labels = ":".join([node.label, "WvEntity"])
    session.run(
        f"""
        MERGE (n:{labels} {{graph_id: $graph_id}})
        SET n += $props
        """,
        graph_id=node.graph_id,
        props=_clean_props(props),
    )


def merge_edge(session, edge: GraphEdge, *, source_url: str) -> None:
    rel_props = {
        **edge.properties,
        "wv_source_url": source_url,
        "source": "wikivoyage",
    }
    session.run(
        f"""
        MATCH (a:WvEntity {{graph_id: $from_id}})
        MATCH (b:WvEntity {{graph_id: $to_id}})
        MERGE (a)-[r:{edge.type}]->(b)
        SET r += $props
        """,
        from_id=edge.from_graph_id,
        to_id=edge.to_graph_id,
        props=_clean_props(rel_props),
    )


def upsert_city_graph(
    driver: Driver,
    row: dict,
    city_anchor: GraphNode,
    country_anchor: GraphNode,
    extract: GraphExtract,
) -> IngestCounts:
    counts = IngestCounts()
    city_gid = city_anchor.graph_id
    source_url = row["source_url"]

    deleted_nodes, deleted_edges = delete_city_derived(driver, city_gid)
    counts.nodes_deleted = deleted_nodes
    counts.edges_deleted = deleted_edges

    with driver.session() as session:
        merge_node(session, country_anchor, city_gid=city_gid, source_url=source_url)
        counts.bump_node("Country")

        merge_node(session, city_anchor, city_gid=city_gid, source_url=source_url)
        counts.bump_node("City")

        merge_edge(
            session,
            GraphEdge(
                from_graph_id=city_anchor.graph_id,
                to_graph_id=country_anchor.graph_id,
                type=ANCHOR_REL_TYPE,
                properties={"source_url": source_url},
            ),
            source_url=source_url,
        )
        counts.bump_edge(ANCHOR_REL_TYPE)

        for node in extract.nodes:
            merge_node(session, node, city_gid=city_gid, source_url=source_url)
            counts.bump_node(node.label)

        for edge in extract.edges:
            merge_edge(session, edge, source_url=source_url)
            counts.bump_edge(edge.type)

    return counts
