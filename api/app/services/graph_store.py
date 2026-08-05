"""Neo4j async driver + v1 template Cypher (neo4j.md)."""

from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncGraphDatabase

from app.config import settings

logger = logging.getLogger(__name__)

# Prefer canonical source_url; tolerate legacy wv_source_url until re-ingest.
_EDGE_SOURCE_URL = "coalesce(r.source_url, r.wv_source_url)"

# City match: slug, name, or aliases (case-insensitive).
_CITY_MATCH = """
OPTIONAL MATCH (c:City)
WHERE toLower(c.slug) = toLower($city_key)
   OR toLower(c.name) = toLower($city_key)
   OR any(a IN coalesce(c.aliases, []) WHERE toLower(a) = toLower($city_key))
WITH c
WHERE c IS NOT NULL
"""

GRAPH_TEMPLATES: dict[str, str] = {
    "places_in_city": f"""
{_CITY_MATCH}
MATCH (p:Place)-[:IN_CITY]->(c)
WHERE $place_type = '' OR p.type = $place_type
RETURN p.name AS name,
       p.type AS type,
       p.wikidata_qid AS wikidata_qid,
       coalesce(p.source_url, c.source_url) AS source_url,
       c.name AS city
ORDER BY p.name
LIMIT $limit
""",
    "day_trips_from_city": f"""
{_CITY_MATCH}
MATCH (p:Place)-[r:DAY_TRIP_FROM]->(c)
WHERE ($max_one_way_minutes < 0 OR r.one_way_minutes IS NULL
       OR r.one_way_minutes <= $max_one_way_minutes)
  AND ($place_type = '' OR p.type = $place_type)
RETURN p.name AS name,
       p.type AS type,
       p.wikidata_qid AS wikidata_qid,
       coalesce({_EDGE_SOURCE_URL}, p.source_url, c.source_url) AS source_url,
       r.one_way_minutes AS one_way_minutes,
       r.mode AS mode,
       c.name AS city
ORDER BY coalesce(r.one_way_minutes, 99999), p.name
LIMIT $limit
""",
    "connection_between_cities": f"""
OPTIONAL MATCH (a:City)
WHERE toLower(a.slug) = toLower($city_a)
   OR toLower(a.name) = toLower($city_a)
   OR any(x IN coalesce(a.aliases, []) WHERE toLower(x) = toLower($city_a))
OPTIONAL MATCH (b:City)
WHERE toLower(b.slug) = toLower($city_b)
   OR toLower(b.name) = toLower($city_b)
   OR any(x IN coalesce(b.aliases, []) WHERE toLower(x) = toLower($city_b))
WITH a, b
WHERE a IS NOT NULL AND b IS NOT NULL
MATCH (a)-[r:CONNECTED_BY]-(b)
RETURN a.name AS from_city,
       b.name AS to_city,
       r.mode AS mode,
       r.duration_min AS duration_min,
       r.duration_max AS duration_max,
       r.direct AS direct,
       r.evidence_text AS evidence_text,
       coalesce({_EDGE_SOURCE_URL}, a.source_url, b.source_url) AS source_url
LIMIT $limit
""",
    "airports_serving_city": f"""
{_CITY_MATCH}
MATCH (c)-[:SERVED_BY_AIRPORT]->(ap:Airport)
RETURN ap.name AS name,
       ap.iata AS iata,
       coalesce(ap.source_url, c.source_url) AS source_url,
       c.name AS city
ORDER BY ap.name
LIMIT $limit
""",
}

ALLOWED_GRAPH_TEMPLATES = frozenset(GRAPH_TEMPLATES)

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
    return _driver


async def _run_query(cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    driver = _get_driver()
    async with driver.session() as session:
        result = await session.run(cypher, params)
        return [dict(record) async for record in result]


def _norm_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _template_params(template: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    limit = raw.get("limit", 20)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 50))

    if template == "places_in_city":
        city = _norm_key(raw.get("city_slug") or raw.get("city") or raw.get("city_name"))
        place_type = _norm_key(raw.get("place_type") or raw.get("type"))
        return {"city_key": city, "place_type": place_type, "limit": limit}

    if template == "day_trips_from_city":
        city = _norm_key(raw.get("city_slug") or raw.get("city") or raw.get("city_name"))
        place_type = _norm_key(raw.get("place_type") or raw.get("type"))
        max_m = raw.get("max_one_way_minutes", -1)
        try:
            max_m = int(max_m)
        except (TypeError, ValueError):
            max_m = -1
        return {
            "city_key": city,
            "place_type": place_type,
            "max_one_way_minutes": max_m,
            "limit": limit,
        }

    if template == "connection_between_cities":
        city_a = _norm_key(
            raw.get("city_a")
            or raw.get("from_city")
            or raw.get("city_slug_a")
            or raw.get("city")
        )
        city_b = _norm_key(
            raw.get("city_b") or raw.get("to_city") or raw.get("city_slug_b")
        )
        return {"city_a": city_a, "city_b": city_b, "limit": limit}

    if template == "airports_serving_city":
        city = _norm_key(raw.get("city_slug") or raw.get("city") or raw.get("city_name"))
        return {"city_key": city, "limit": limit}

    return {"limit": limit}


def _rows_to_text(template: str, rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    lines = [f"Graph template: {template}"]
    for row in rows:
        parts: list[str] = []
        for key, val in row.items():
            if val is None or val == "":
                continue
            parts.append(f"{key}={val}")
        if parts:
            lines.append(" - " + "; ".join(parts))
    return "\n".join(lines) if len(lines) > 1 else None


def _pick_str(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _context_label(template: str, rows: list[dict[str, Any]], params: dict[str, Any]) -> str:
    """Human label for citations — never the template id."""
    for row in rows:
        label = _pick_str(row, "name", "city", "from_city", "to_city")
        if label:
            return label
    for key in ("city_key", "city_a", "city_b"):
        if params.get(key):
            return str(params[key])
    return ""


def _primary_source_url(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        url = _pick_str(row, "source_url")
        if url:
            return url
    return ""


def _infer_source_kind(url: str) -> str:
    lower = url.lower()
    if "wikivoyage" in lower:
        return "wikivoyage"
    if "wikipedia" in lower:
        return "wikipedia"
    return "wikivoyage" if url else ""


async def run_graph_query(
    template: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Run one allowlisted template; return {template, text, rows} or None."""
    if template not in ALLOWED_GRAPH_TEMPLATES:
        logger.warning("Unknown graph template: %s", template)
        return None
    cypher = GRAPH_TEMPLATES[template]
    query_params = _template_params(template, params)
    if template == "connection_between_cities":
        if not query_params.get("city_a") or not query_params.get("city_b"):
            return None
    elif not query_params.get("city_key"):
        return None

    try:
        rows = await _run_query(cypher, query_params)
    except Exception as e:
        logger.warning("Neo4j template %s failed: %s", template, e)
        return None

    text = _rows_to_text(template, rows)
    if not text:
        return None
    return {"template": template, "text": text, "rows": rows, "params": query_params}


async def fetch_contexts_for_queries(
    graph_queries: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Execute planner graph_queries; return synthesize-ready contexts."""
    contexts: list[dict[str, str]] = []
    for item in graph_queries or []:
        if not isinstance(item, dict):
            continue
        template = item.get("template")
        if not isinstance(template, str):
            continue
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        result = await run_graph_query(template.strip(), params)
        if not result:
            continue
        rows = result.get("rows") or []
        url = _primary_source_url(rows)
        label = _context_label(result["template"], rows, result.get("params") or {})
        contexts.append(
            {
                "city": label,
                "name": label,
                "text": result["text"],
                "source_url": url,
                "source": _infer_source_kind(url),
            }
        )
    return contexts
