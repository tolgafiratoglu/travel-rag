"""Neo4j upsert helpers for v1 graph ingest (neo4j.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from neo4j import Driver

from app.graph.neo4j_schema import (
    ANCHOR_REL_TYPE,
    ManagedScope,
    airport_graph_id,
    managed_scope_for_label,
    place_graph_id,
    slugify,
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


def wipe_all_entities(driver: Driver) -> int:
    """Delete every WvEntity (full re-ingest). Returns deleted node count."""
    with driver.session() as session:
        result = session.run(
            """
            MATCH (n:WvEntity)
            DETACH DELETE n
            RETURN count(n) AS deleted
            """
        )
        return int(result.single()["deleted"])


def _clean_props(props: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in props.items() if v is not None}


def build_city_anchor(row: dict, city_gid: str) -> GraphNode:
    name = row["page_name"]
    aliases = [name]
    # Light alias for Turkish capital I / İ mismatch on common pages.
    if name == "İstanbul" and "Istanbul" not in aliases:
        aliases.append("Istanbul")
    elif name == "Istanbul" and "İstanbul" not in aliases:
        aliases.append("İstanbul")

    props: dict[str, Any] = {
        "slug": row.get("page_slug") or slugify(name),
        "wikidata_qid": row.get("wikidata_qid"),
        "source_url": row["source_url"],
        "aliases": aliases,
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
        name=name,
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


def build_place_from_poi(row: dict, *, city_gid: str) -> GraphNode:
    """Wikipedia POI row → Place node (source=wikipedia)."""
    source_url = row["source_url"]
    gid = place_graph_id(row.get("wikidata_qid"), source_url)
    poi_type = (row.get("poi_type") or "attraction").strip()
    props: dict[str, Any] = {
        "slug": slugify(row["name"]),
        "type": poi_type,
        "wikidata_qid": row.get("wikidata_qid"),
        "source_url": source_url,
        "source_city_graph_id": city_gid,
        "managed_scope": ManagedScope.CITY.value,
        "source": "wikipedia",
    }
    return GraphNode(
        label="Place",
        graph_id=gid,
        name=row["name"],
        properties=_clean_props(props),
    )


def delete_city_wv_derived(driver: Driver, city_gid: str) -> tuple[int, int]:
    """Remove Wikivoyage/LLM-derived nodes & edges for one city.

    Keeps City anchor, IN_COUNTRY → Country, and Wikipedia Places.
    """
    edges_deleted = 0
    with driver.session() as session:
        # Outgoing city edges except IN_COUNTRY (and keep wikipedia-sourced if any).
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

        # Incoming DAY_TRIP_FROM from WV places onto this city.
        in_edge = session.run(
            """
            MATCH (p:Place)-[r:DAY_TRIP_FROM]->(c:City {graph_id: $city_gid})
            WHERE coalesce(r.source, '') = 'wikivoyage'
            DELETE r
            RETURN count(r) AS deleted
            """,
            city_gid=city_gid,
        )
        edges_deleted += in_edge.single()["deleted"]

        edge_result = session.run(
            """
            MATCH (n:WvEntity {source_city_graph_id: $city_gid, managed_scope: $scope})
            WHERE NOT n:City
              AND coalesce(n.source, '') = 'wikivoyage'
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
              AND coalesce(n.source, '') = 'wikivoyage'
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
    source: str = "wikivoyage",
) -> None:
    scope = managed_scope_for_label(node.label)
    props = {
        **node.properties,
        "name": node.name,
        "graph_id": node.graph_id,
        "managed_scope": scope.value,
        "source": node.properties.get("source") or source,
    }
    if scope == ManagedScope.CITY:
        props.setdefault("source_city_graph_id", city_gid)
        props.setdefault("source_url", source_url)
    if scope == ManagedScope.ANCHOR:
        props.setdefault("source_url", source_url)

    labels = ":".join([node.label, "WvEntity"])
    session.run(
        f"""
        MERGE (n:{labels} {{graph_id: $graph_id}})
        SET n += $props
        REMOVE n.wv_source_url
        """,
        graph_id=node.graph_id,
        props=_clean_props(props),
    )


def merge_edge(session, edge: GraphEdge, *, source_url: str, source: str = "wikivoyage") -> None:
    rel_props = {
        **edge.properties,
        "source_url": source_url,
        "source": source,
    }
    session.run(
        f"""
        MATCH (a:WvEntity {{graph_id: $from_id}})
        MATCH (b:WvEntity {{graph_id: $to_id}})
        MERGE (a)-[r:{edge.type}]->(b)
        SET r += $props
        REMOVE r.wv_source_url
        """,
        from_id=edge.from_graph_id,
        to_id=edge.to_graph_id,
        props=_clean_props(rel_props),
    )


def find_city_graph_id(session, city_name: str) -> str | None:
    """Resolve City by name or aliases (case-insensitive)."""
    result = session.run(
        """
        MATCH (c:City)
        WHERE toLower(c.name) = toLower($name)
           OR any(a IN coalesce(c.aliases, []) WHERE toLower(a) = toLower($name))
        RETURN c.graph_id AS gid
        LIMIT 1
        """,
        name=city_name.strip(),
    )
    record = result.single()
    return record["gid"] if record else None


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

    deleted_nodes, deleted_edges = delete_city_wv_derived(driver, city_gid)
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
            # Remote City stubs from CONNECTED_BY: treat as anchor-lite.
            merge_node(session, node, city_gid=city_gid, source_url=source_url)
            counts.bump_node(node.label)

        for edge in extract.edges:
            merge_edge(session, edge, source_url=source_url)
            counts.bump_edge(edge.type)

    return counts


def upsert_place_for_city(
    driver: Driver,
    place: GraphNode,
    city_gid: str,
    *,
    source_url: str,
) -> IngestCounts:
    counts = IngestCounts()
    with driver.session() as session:
        merge_node(
            session,
            place,
            city_gid=city_gid,
            source_url=source_url,
            source="wikipedia",
        )
        counts.bump_node("Place")
        merge_edge(
            session,
            GraphEdge(
                from_graph_id=place.graph_id,
                to_graph_id=city_gid,
                type="IN_CITY",
                properties={"source_url": source_url},
            ),
            source_url=source_url,
            source="wikipedia",
        )
        counts.bump_edge("IN_CITY")
    return counts


# Re-export for scripts that build airport ids during dry-run display.
__all__ = [
    "IngestCounts",
    "airport_graph_id",
    "build_city_anchor",
    "build_country_anchor",
    "build_place_from_poi",
    "ensure_constraints",
    "find_city_graph_id",
    "upsert_city_graph",
    "upsert_place_for_city",
    "wipe_all_entities",
]
