"""Neo4j async driver + city profile retrieval."""

from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncGraphDatabase

from app.config import settings

logger = logging.getLogger(__name__)

CYPHER_CITY_PROFILE = """
MATCH (c:City)
WHERE toLower(c.name) = toLower($city)

OPTIONAL MATCH (c)-[:IN]->(country:Country)
OPTIONAL MATCH (c)-[:BEST_SEASON]->(season:Season)
OPTIONAL MATCH (c)-[:BUDGET_LEVEL]->(budget:Budget)

OPTIONAL MATCH (c)-[conn:CONNECTED_TO]->(other:City)
WITH c, country, season, budget,
     collect(DISTINCT {
       other: other.name,
       mode: conn.mode,
       duration: conn.duration,
       cost_level: conn.cost_level
     }) AS connections

OPTIONAL MATCH (attr:Attraction)-[:LOCATED_IN]->(c)
WITH c, country, season, budget, connections,
     collect(DISTINCT {
       name: attr.name,
       type: attr.type,
       unesco: attr.unesco,
       description: attr.description
     }) AS attractions

OPTIONAL MATCH (act:Activity)-[:LOCATED_IN]->(c)

RETURN
  c.name AS city,
  c.description AS description,
  c.population AS population,
  country.name AS country,
  season.name AS season,
  season.months AS season_months,
  budget.level AS budget,
  connections,
  attractions,
  collect(DISTINCT {
    name: act.name,
    category: act.category,
    season_dependent: act.season_dependent
  }) AS activities
"""

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


def _has_values(entry: dict) -> bool:
    return any(v is not None for v in entry.values())


def city_profile_to_context(row: dict[str, Any]) -> str | None:
    if not row.get("city"):
        return None

    lines = [f"City: {row['city']}"]

    description = row.get("description")
    if description:
        lines.append(f"Description: {description}")

    population = row.get("population")
    if population is not None:
        lines.append(f"Population: {population}")

    country = row.get("country")
    if country:
        lines.append(f"Country: {country}")

    season = row.get("season")
    season_months = row.get("season_months")
    if season:
        if season_months:
            lines.append(f"Best season: {season} ({season_months})")
        else:
            lines.append(f"Best season: {season}")

    budget = row.get("budget")
    if budget:
        lines.append(f"Budget level: {budget}")

    attractions = [a for a in (row.get("attractions") or []) if _has_values(a)]
    if attractions:
        lines.append("Attractions:")
        for attr in attractions:
            name = attr.get("name")
            if not name:
                continue

            parts = [name]
            if attr.get("type"):
                parts.append(f"type: {attr['type']}")
            if attr.get("unesco") is not None:
                parts.append(f"UNESCO: {'yes' if attr['unesco'] else 'no'}")

            text = " - " + ", ".join(parts)
            if attr.get("description"):
                text += f": {attr['description']}"
            lines.append(text)

    activities = [a for a in (row.get("activities") or []) if _has_values(a)]
    if activities:
        lines.append("Activities:")
        for act in activities:
            name = act.get("name")
            if not name:
                continue

            parts = [name]
            if act.get("category"):
                parts.append(f"category: {act['category']}")
            if act.get("season_dependent") is not None:
                parts.append(
                    f"season-dependent: {'yes' if act['season_dependent'] else 'no'}"
                )
            lines.append(" - " + ", ".join(parts))

    connections = [c for c in (row.get("connections") or []) if _has_values(c)]
    if connections:
        lines.append("Connections:")
        for conn in connections:
            other = conn.get("other")
            if not other:
                continue

            parts = [f"to {other}"]
            if conn.get("mode"):
                parts.append(f"mode: {conn['mode']}")
            if conn.get("duration"):
                parts.append(f"duration: {conn['duration']}")
            if conn.get("cost_level"):
                parts.append(f"cost: {conn['cost_level']}")
            lines.append(" - " + ", ".join(parts))

    return "\n".join(lines)


async def fetch_city_context(city: str) -> dict[str, str] | None:
    city = (city or "").strip()
    if not city:
        return None

    try:
        rows = await _run_query(CYPHER_CITY_PROFILE, {"city": city})
    except Exception as e:
        logger.warning("Neo4j query failed for city %r: %s", city, e)
        return None

    if not rows:
        return None

    text = city_profile_to_context(rows[0])
    if not text:
        return None

    return {"city": rows[0]["city"], "text": text}


async def fetch_contexts_for_cities(cities: list[str]) -> list[dict[str, str]]:
    seen: set[str] = set()
    contexts: list[dict[str, str]] = []
    for city in cities:
        key = city.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        ctx = await fetch_city_context(city.strip())
        if ctx:
            contexts.append(ctx)
    return contexts
