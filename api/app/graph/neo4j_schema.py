"""Neo4j v1 schema — see neo4j.md.

Nodes: Country, City, Place, Airport
Rels: IN_COUNTRY, IN_CITY, SERVED_BY_AIRPORT, CONNECTED_BY, DAY_TRIP_FROM
"""

from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Literal

NEO4J_NODE_LABELS: tuple[str, ...] = (
    "Country",
    "City",
    "Place",
    "Airport",
)

Neo4jNodeLabel = Literal["Country", "City", "Place", "Airport"]

NEO4J_NODE_LABELS_FROZEN = frozenset(NEO4J_NODE_LABELS)

GRAPH_ID_PREFIX: dict[str, str] = {
    "Country": "country",
    "City": "city",
    "Place": "place",
    "Airport": "airport",
}

NEO4J_REL_TYPES: tuple[str, ...] = (
    "IN_COUNTRY",
    "IN_CITY",
    "SERVED_BY_AIRPORT",
    "CONNECTED_BY",
    "DAY_TRIP_FROM",
)

Neo4jRelType = Literal[
    "IN_COUNTRY",
    "IN_CITY",
    "SERVED_BY_AIRPORT",
    "CONNECTED_BY",
    "DAY_TRIP_FROM",
]

NEO4J_REL_TYPES_FROZEN = frozenset(NEO4J_REL_TYPES)

# LLM may emit these; anchors are written deterministically.
LLM_NODE_LABELS = frozenset({"Place", "Airport", "City"})
LLM_REL_TYPES = frozenset(
    {"SERVED_BY_AIRPORT", "CONNECTED_BY", "DAY_TRIP_FROM"}
)

PLACE_TYPES: tuple[str, ...] = (
    "museum",
    "historical_site",
    "attraction",
    "natural_site",
    "religious_site",
    "archaeological_site",
)

NEO4J_MERGE_KEY = "graph_id"

# Deterministic City -> Country; protected on city re-ingest delete.
ANCHOR_REL_TYPE: Neo4jRelType = "IN_COUNTRY"


class ManagedScope(str, Enum):
    GLOBAL = "global"
    ANCHOR = "anchor"
    CITY = "city"


GLOBAL_NODE_LABELS = frozenset({"Country"})
ANCHOR_NODE_LABELS = frozenset({"City"})
CITY_SCOPED_LABELS = frozenset({"Place", "Airport"})

V1_DISABLED_REL_TYPES = frozenset()


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text, flags=re.UNICODE)
    return text.strip("-") or "unknown"


def stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def normalize_qid(qid: str | None) -> str | None:
    if not qid:
        return None
    qid = qid.strip().upper()
    return qid if qid.startswith("Q") else None


def city_graph_id(wikidata_qid: str | None, source_url: str) -> str:
    qid = normalize_qid(wikidata_qid)
    if qid:
        return f"city:{qid}"
    return f"city:url-{stable_hash(source_url)}"


def country_graph_id(country_name: str) -> str:
    return f"country:{slugify(country_name)}"


def place_graph_id(wikidata_qid: str | None, source_url: str) -> str:
    qid = normalize_qid(wikidata_qid)
    if qid:
        return f"place:{qid}"
    return f"place:url-{stable_hash(source_url)}"


def airport_graph_id(*, iata: str | None = None, name: str, source_url: str = "") -> str:
    if iata and iata.strip():
        return f"airport:{iata.strip().upper()}"
    return f"airport:slug-{slugify(name)}" if name else f"airport:url-{stable_hash(source_url)}"


def managed_scope_for_label(label: str) -> ManagedScope:
    if label in GLOBAL_NODE_LABELS:
        return ManagedScope.GLOBAL
    if label in ANCHOR_NODE_LABELS:
        return ManagedScope.ANCHOR
    return ManagedScope.CITY
