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

OPTIONAL MATCH (c)-[:PART_OF]->(country:Country)

OPTIONAL MATCH (c)-[:SEASONAL_FOR]->(season:Season)
WITH c, country, collect(DISTINCT {
  name: season.name,
  months: season.months
}) AS seasons

OPTIONAL MATCH (c)-[:ACCESSIBLE_BY]->(access)
WHERE access:Airport OR access:TransportHub OR access:Route
WITH c, country, seasons, collect(DISTINCT {
  name: access.name,
  label: head([l IN labels(access) WHERE l <> 'WvEntity' | l]),
  mode: access.mode
}) AS access_options

OPTIONAL MATCH (c)-[:GOOD_BASE_FOR]->(base_target)
WITH c, country, seasons, access_options, collect(DISTINCT {
  name: base_target.name,
  label: head([l IN labels(base_target) WHERE l <> 'WvEntity' | l])
}) AS good_base_for

OPTIONAL MATCH (attr)-[:PART_OF]->(c)
WHERE attr:Attraction
WITH c, country, seasons, access_options, good_base_for,
     collect(DISTINCT {
       name: attr.name,
       type: attr.type,
       unesco: attr.unesco,
       description: attr.description
     }) AS attractions

OPTIONAL MATCH (act)-[:PART_OF]->(c)
WHERE act:Activity

OPTIONAL MATCH (peer)-[:IN_SAME_AREA]-(c)
WHERE peer:Attraction OR peer:Activity OR peer:District OR peer:City

RETURN
  c.name AS city,
  c.description AS description,
  c.population AS population,
  country.name AS country,
  seasons,
  access_options,
  good_base_for,
  attractions,
  collect(DISTINCT {
    name: act.name,
    category: act.category,
    season_dependent: act.season_dependent
  }) AS activities,
  collect(DISTINCT {
    name: peer.name,
    label: head([l IN labels(peer) WHERE l <> 'WvEntity' | l])
  }) AS same_area
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

    seasons = [s for s in (row.get("seasons") or []) if _has_values(s)]
    if seasons:
        lines.append("Seasonal:")
        for season in seasons:
            name = season.get("name")
            if not name:
                continue
            months = season.get("months")
            if months:
                lines.append(f" - {name} ({months})")
            else:
                lines.append(f" - {name}")

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

    access_options = [a for a in (row.get("access_options") or []) if _has_values(a)]
    if access_options:
        lines.append("Accessible by:")
        for access in access_options:
            name = access.get("name")
            if not name:
                continue
            parts = [name]
            if access.get("label"):
                parts.append(f"kind: {access['label']}")
            if access.get("mode"):
                parts.append(f"mode: {access['mode']}")
            lines.append(" - " + ", ".join(parts))

    good_base_for = [g for g in (row.get("good_base_for") or []) if _has_values(g)]
    if good_base_for:
        lines.append("Good base for:")
        for item in good_base_for:
            name = item.get("name")
            if not name:
                continue
            label = item.get("label")
            if label:
                lines.append(f" - {name} ({label})")
            else:
                lines.append(f" - {name}")

    same_area = [p for p in (row.get("same_area") or []) if _has_values(p)]
    if same_area:
        lines.append("Same area:")
        for peer in same_area:
            name = peer.get("name")
            if not name:
                continue
            label = peer.get("label")
            if label:
                lines.append(f" - {name} ({label})")
            else:
                lines.append(f" - {name}")

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
