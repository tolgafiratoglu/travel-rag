"""Shared Neo4j node labels, relationship types, and graph_id helpers."""

from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Literal

NEO4J_NODE_LABELS: tuple[str, ...] = (
    "Country",
    "Region",
    "City",
    "District",
    "Attraction",
    "Activity",
    "Airport",
    "TransportHub",
    "Route",
    "TravelTheme",
    "Season",
    "Budget",
)

Neo4jNodeLabel = Literal[
    "Country",
    "Region",
    "City",
    "District",
    "Attraction",
    "Activity",
    "Airport",
    "TransportHub",
    "Route",
    "TravelTheme",
    "Season",
    "Budget",
]

NEO4J_NODE_LABELS_FROZEN = frozenset(NEO4J_NODE_LABELS)

GRAPH_ID_PREFIX: dict[str, str] = {
    "Country": "country",
    "Region": "region",
    "City": "city",
    "District": "district",
    "Attraction": "attraction",
    "Activity": "activity",
    "Airport": "airport",
    "TransportHub": "hub",
    "Route": "route",
    "TravelTheme": "theme",
    "Season": "season",
    "Budget": "budget",
}

NEO4J_REL_TYPES: tuple[str, ...] = (
    "IN_COUNTRY",
    "IN_REGION",
    "IN_CITY",
    "LOCATED_IN",
    "BEST_SEASON",
    "BUDGET_LEVEL",
    "HAS_THEME",
    "SERVES",
    "PART_OF_ROUTE",
)

Neo4jRelType = Literal[
    "IN_COUNTRY",
    "IN_REGION",
    "IN_CITY",
    "LOCATED_IN",
    "BEST_SEASON",
    "BUDGET_LEVEL",
    "HAS_THEME",
    "SERVES",
    "PART_OF_ROUTE",
]

NEO4J_REL_TYPES_FROZEN = frozenset(NEO4J_REL_TYPES)

BudgetLevel = Literal["low", "mid", "high", "luxury"]
BUDGET_LEVELS: tuple[str, ...] = ("low", "mid", "high", "luxury")

GLOBAL_SEASON_IDS: tuple[str, ...] = (
    "season:spring",
    "season:summer",
    "season:autumn",
    "season:winter",
)

NEO4J_MERGE_KEY = "graph_id"


class ManagedScope(str, Enum):
    GLOBAL = "global"
    ANCHOR = "anchor"
    CITY = "city"


GLOBAL_NODE_LABELS = frozenset({"Country", "Season", "Budget", "TravelTheme"})
ANCHOR_NODE_LABELS = frozenset({"City"})
CITY_SCOPED_LABELS = frozenset(
    label
    for label in NEO4J_NODE_LABELS
    if label not in GLOBAL_NODE_LABELS and label not in ANCHOR_NODE_LABELS
)

V1_DISABLED_REL_TYPES = frozenset()  # CONNECTED_TO disabled — not in NEO4J_REL_TYPES


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


def budget_graph_id(level: str) -> str:
    level = level.strip().lower()
    if level not in BUDGET_LEVELS:
        level = "mid"
    return f"budget:{level}"


def season_graph_id(season_name: str) -> str:
    return f"season:{slugify(season_name)}"


def managed_scope_for_label(label: str) -> ManagedScope:
    if label in GLOBAL_NODE_LABELS:
        return ManagedScope.GLOBAL
    if label in ANCHOR_NODE_LABELS:
        return ManagedScope.ANCHOR
    return ManagedScope.CITY
