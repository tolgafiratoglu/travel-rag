#!/usr/bin/env python3
"""Ingest Wikivoyage cities into Neo4j (v1 schema — neo4j.md).

Deterministic: Country, City, IN_COUNTRY.
Optional Gemini: Airport / Place stubs + SERVED_BY_AIRPORT, CONNECTED_BY, DAY_TRIP_FROM
from get_in / go_next (and get_around) sections only.

Places (museums / historical) come from scripts/ingest-neo4j-places.py.

Usage:
    MARIADB_HOST=localhost NEO4J_URI=bolt://localhost:7687 \\
    LLM_PROVIDER=gemini \\
    .venv/bin/python scripts/ingest-wikivoyage-neo4j.py --page Antalya

    # Anchors only (no LLM):
    .venv/bin/python scripts/ingest-wikivoyage-neo4j.py --page Antalya --anchors-only

    # Wipe all WvEntity nodes first:
    .venv/bin/python scripts/ingest-wikivoyage-neo4j.py --wipe --page Antalya
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "scripts"))


def _require_imports() -> None:
    missing: list[str] = []
    try:
        import pymysql  # noqa: F401
    except ImportError:
        missing.append("pymysql")
    try:
        import neo4j  # noqa: F401
    except ImportError:
        missing.append("neo4j")
    if missing:
        print(f"Missing script deps: pip install {' '.join(missing)}")
        sys.exit(1)
    try:
        import pydantic  # noqa: F401
    except ImportError:
        print("Missing API deps. Run: cd api && pip install -e .")
        sys.exit(1)


_require_imports()

from neo4j import GraphDatabase
from pydantic import ValidationError

from app.config import settings
from app.graph.neo4j_schema import (
    ANCHOR_REL_TYPE,
    LLM_NODE_LABELS,
    LLM_REL_TYPES,
    city_graph_id,
    country_graph_id,
)
from app.models.graph_extract import GraphExtract, validate_graph_extract
from lib.neo4j_wv_ingest import (
    IngestCounts,
    build_city_anchor,
    build_country_anchor,
    ensure_constraints,
    upsert_city_graph,
    wipe_all_entities,
)
from lib.wv_db import load_cities
from lib.wv_parse import sections_for_gemini, split_sections

logger = logging.getLogger(__name__)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")
SLEEP_SECONDS = float(os.getenv("WV_NEO4J_SLEEP", "1.0"))

# Topics useful for airports / connections / day trips.
RELATION_TOPICS = frozenset({"get_in", "go_next", "get_around", "see"})

GRAPH_EXTRACT_PROMPT = """You extract travel GRAPH RELATIONSHIPS from Wikivoyage text.
Return ONLY valid JSON (no markdown fences or commentary).

Allowed node labels: {labels}
Allowed relationship types: {rel_types}

Current city graph_id (use EXACTLY this string when referring to this city):
{city_graph_id}

Output schema:
{{
  "nodes": [
    {{
      "label": "Airport",
      "graph_id": "airport:AYT",
      "name": "Antalya Airport",
      "properties": {{ "iata": "AYT" }}
    }},
    {{
      "label": "Place",
      "graph_id": "place:slug-perge",
      "name": "Perge",
      "properties": {{ "type": "archaeological_site" }}
    }},
    {{
      "label": "City",
      "graph_id": "city:slug-yalova",
      "name": "Yalova",
      "properties": {{}}
    }}
  ],
  "edges": [
    {{
      "from_graph_id": "{city_graph_id}",
      "to_graph_id": "airport:AYT",
      "type": "SERVED_BY_AIRPORT",
      "properties": {{
        "evidence_text": "short quote from the text",
        "confidence": 0.9
      }}
    }},
    {{
      "from_graph_id": "place:slug-perge",
      "to_graph_id": "{city_graph_id}",
      "type": "DAY_TRIP_FROM",
      "properties": {{
        "one_way_minutes": 35,
        "mode": "bus",
        "evidence_text": "quote",
        "confidence": 0.85
      }}
    }},
    {{
      "from_graph_id": "{city_graph_id}",
      "to_graph_id": "city:slug-yalova",
      "type": "CONNECTED_BY",
      "properties": {{
        "mode": "ferry",
        "duration_min": 105,
        "duration_max": null,
        "direct": true,
        "evidence_text": "quote",
        "confidence": 0.8
      }}
    }}
  ]
}}

Relationship directions:
- SERVED_BY_AIRPORT: City -> Airport
- DAY_TRIP_FROM: Place -> City  (Place is a day trip FROM the City)
- CONNECTED_BY: City -> City (transport between cities)

Rules:
- Do NOT emit Country nodes.
- Do NOT emit a City node for the current city; always use {city_graph_id} on edges.
- Durations are minutes (integers). If the text does not state a duration, omit the field or set null — NEVER invent times.
- Prefer sparse, high-confidence edges grounded in evidence_text.
- graph_id for airports: airport:{{IATA}} when known, else airport:slug-{{name}}.
- graph_id for places: place:slug-{{name}}.
- graph_id for other cities: city:slug-{{name}}.
- Place.type one of: museum, historical_site, attraction, natural_site, religious_site, archaeological_site.

City metadata:
- name: {city_name}
- country: {country}
- source_url: {source_url}

Sections JSON:
{sections_json}
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in Gemini response")
    return json.loads(text[start : end + 1])


def _relation_sections(sections: list[dict]) -> list[dict]:
    """Keep get_in / go_next / get_around / see for relation extract."""
    filtered = [
        s
        for s in sections
        if (s.get("topic") or "").casefold() in RELATION_TOPICS
    ]
    return sections_for_gemini(filtered)


async def extract_relations_gemini(
    row: dict,
    sections_payload: list[dict],
    city_gid: str,
) -> GraphExtract:
    from app.services import llm

    prompt = GRAPH_EXTRACT_PROMPT.format(
        labels=", ".join(sorted(LLM_NODE_LABELS)),
        rel_types=", ".join(sorted(LLM_REL_TYPES)),
        city_graph_id=city_gid,
        city_name=row["page_name"],
        country=row["country"],
        source_url=row["source_url"],
        sections_json=json.dumps(sections_payload, ensure_ascii=False, indent=2),
    )
    raw = await llm.generate(prompt)
    data = _extract_json(raw)
    extract = GraphExtract.model_validate(data)
    country_gid = country_graph_id(row["country"])
    return validate_graph_extract(
        extract,
        city_graph_id=city_gid,
        country_graph_id=country_gid,
        strict=False,
        llm_only=True,
    )


def _print_counts(label: str, counts: IngestCounts) -> None:
    print(f"  {label}:")
    print(f"    nodes written: {counts.nodes_written}")
    if counts.nodes_by_label:
        for node_label, n in sorted(counts.nodes_by_label.items()):
            print(f"      {node_label}: {n}")
    print(f"    edges written: {counts.edges_written}")
    if counts.edges_by_type:
        for rel_type, n in sorted(counts.edges_by_type.items()):
            print(f"      {rel_type}: {n}")
    if counts.nodes_deleted or counts.edges_deleted:
        print(
            f"    deleted first: {counts.nodes_deleted} node(s), "
            f"{counts.edges_deleted} edge(s)"
        )


async def ingest_city(
    driver,
    row: dict,
    *,
    dry_run: bool,
    anchors_only: bool,
) -> IngestCounts | None:
    sections = split_sections(row["wikitext"] or "")
    payload = _relation_sections(sections)
    print(
        f"  {len(sections)} section(s), {len(payload)} chunk(s) for relation LLM"
    )

    city_gid = city_graph_id(row.get("wikidata_qid"), row["source_url"])
    country_gid = country_graph_id(row["country"])
    city_anchor = build_city_anchor(row, city_gid)
    country_anchor = build_country_anchor(row["country"], country_gid)

    if anchors_only:
        extract = GraphExtract()
        print("  anchors-only: skipping Gemini")
    else:
        if not payload:
            print("  no relation sections; anchors only")
            extract = GraphExtract()
        else:
            extract = await extract_relations_gemini(row, payload, city_gid)
            print(
                f"  Gemini: {len(extract.nodes)} node(s), "
                f"{len(extract.edges)} edge(s)"
            )

    if dry_run:
        print(
            json.dumps(
                {
                    "city_anchor": city_anchor.model_dump(),
                    "country_anchor": country_anchor.model_dump(),
                    "extract": extract.model_dump(),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        counts = IngestCounts()
        counts.bump_node("Country")
        counts.bump_node("City")
        counts.bump_edge(ANCHOR_REL_TYPE)
        for node in extract.nodes:
            counts.bump_node(node.label)
        for edge in extract.edges:
            counts.bump_edge(edge.type)
        return counts

    counts = upsert_city_graph(
        driver,
        row,
        city_anchor,
        country_anchor,
        extract,
    )
    _print_counts("Neo4j upsert", counts)
    return counts


async def main_async(
    page: str | None,
    dry_run: bool,
    anchors_only: bool,
    wipe: bool,
) -> None:
    if not anchors_only:
        if settings.llm_provider != "gemini":
            print(
                f"ERROR: relation extract needs LLM_PROVIDER=gemini "
                f"(got {settings.llm_provider!r}). Use --anchors-only to skip LLM."
            )
            sys.exit(1)
        if not settings.gemini_api_key:
            print(
                f"ERROR: GEMINI_API_KEY not set. Add it to {settings.secrets_env_path} "
                "or the environment."
            )
            sys.exit(1)
        try:
            from app.services import llm  # noqa: F401
        except ImportError as exc:
            print("Missing API deps. Run: cd api && pip install -e .")
            print(exc)
            sys.exit(1)

    rows = load_cities(page, include_coords=True, add_page_slug=True)
    if not rows:
        print("No rows in wv_cities (run parse-wikivoyage.py first)")
        sys.exit(1)

    print(f"Loaded {len(rows)} city/cities from wv_cities")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        if wipe and not dry_run:
            deleted = wipe_all_entities(driver)
            print(f"Wiped {deleted} WvEntity node(s)")

        if not dry_run:
            ensure_constraints(driver)

        totals = IngestCounts()

        for i, row in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] {row['country']} / {row['page_name']}")
            try:
                counts = await ingest_city(
                    driver,
                    row,
                    dry_run=dry_run,
                    anchors_only=anchors_only,
                )
                if counts:
                    for label, n in counts.nodes_by_label.items():
                        totals.nodes_by_label[label] = (
                            totals.nodes_by_label.get(label, 0) + n
                        )
                    for rel, n in counts.edges_by_type.items():
                        totals.edges_by_type[rel] = (
                            totals.edges_by_type.get(rel, 0) + n
                        )
                    totals.nodes_deleted += counts.nodes_deleted
                    totals.edges_deleted += counts.edges_deleted
            except (ValidationError, ValueError, RuntimeError) as exc:
                print(f"  ERROR: {exc}")
            if i < len(rows) and SLEEP_SECONDS > 0:
                await asyncio.sleep(SLEEP_SECONDS)
    finally:
        driver.close()

    print("\nDone. Totals:")
    print(f"  nodes written: {totals.nodes_written}")
    for label, n in sorted(totals.nodes_by_label.items()):
        print(f"    {label}: {n}")
    print(f"  edges written: {totals.edges_written}")
    for rel, n in sorted(totals.edges_by_type.items()):
        print(f"    {rel}: {n}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest wv_cities into Neo4j (v1 schema)"
    )
    parser.add_argument("--page", help="Single city (page_name or wiki title)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print extract JSON, no Neo4j writes",
    )
    parser.add_argument(
        "--anchors-only",
        action="store_true",
        help="Write Country/City/IN_COUNTRY only (no Gemini)",
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="DETACH DELETE all WvEntity nodes before ingest",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    args = parse_args()
    asyncio.run(
        main_async(args.page, args.dry_run, args.anchors_only, args.wipe)
    )
