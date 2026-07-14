#!/usr/bin/env python3
"""Ingest Wikivoyage wikitext from MariaDB into Neo4j via Gemini graph extraction.

City and Country anchors are deterministic from MariaDB. Gemini extracts
Attraction, Activity, Airport, Season, Budget links, etc.

Uses the same LLM stack as the API (app.services.llm → GeminiProvider).

Usage:
    # Install API deps + script-only driver
    cd api && pip install -e . && pip install pymysql
    cd ..

    MARIADB_HOST=localhost NEO4J_URI=bolt://localhost:7687 \\
    .venv/bin/python scripts/ingest-wikivoyage-neo4j.py --page Bordeaux --dry-run

    MARIADB_HOST=localhost NEO4J_URI=bolt://localhost:7687 \\
    .venv/bin/python scripts/ingest-wikivoyage-neo4j.py --page Bordeaux

    MARIADB_HOST=localhost .venv/bin/python scripts/ingest-wikivoyage-neo4j.py
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

try:
    from app.services import llm
except ImportError as exc:
    print("Missing API deps (google-genai, etc.). Run: cd api && pip install -e .")
    print(exc)
    sys.exit(1)

from app.config import settings
from app.graph.neo4j_schema import (
    NEO4J_NODE_LABELS,
    NEO4J_REL_TYPES,
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
)
from lib.wv_db import load_cities
from lib.wv_parse import sections_for_gemini, split_sections

logger = logging.getLogger(__name__)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")
SLEEP_SECONDS = float(os.getenv("WV_NEO4J_SLEEP", "1.0"))

GRAPH_EXTRACT_PROMPT = """You extract a travel knowledge graph from Wikivoyage section text.
Return ONLY valid JSON (no markdown fences or commentary).

Allowed node labels: {labels}

Allowed relationship types: {rel_types}

Output schema:
{{
  "nodes": [
    {{
      "label": "Attraction",
      "graph_id": "attraction:bordeaux:cathedrale-saint-andre",
      "name": "Cathédrale Saint-André",
      "properties": {{
        "description": "short summary",
        "unesco": false,
        "type": "cathedral",
        "section": "see"
      }}
    }}
  ],
  "edges": [
    {{
      "from_graph_id": "attraction:bordeaux:cathedrale-saint-andre",
      "to_graph_id": "{city_graph_id}",
      "type": "LOCATED_IN",
      "properties": {{
        "evidence": "short quote or paraphrase",
        "section": "see"
      }}
    }}
  ]
}}

Rules:
- Do NOT create City or Country nodes.
- For every relationship involving the current city, use exactly this graph_id as the city target: {city_graph_id}
- Never construct, normalize, shorten, or modify the city graph_id.
- CONNECTED_TO to other cities is disabled in v1 — do not emit cross-city travel edges.
- Use SERVES for airports and transport hubs that serve the current city.
- Use LOCATED_IN for an airport only when the text explicitly says it is physically inside the city.
- LOCATED_IN direction: object/place -> city (from_graph_id is the place, to_graph_id is the city).
- Season nodes must use global ids only: season:spring, season:summer, season:autumn, season:winter
- Budget nodes must use global ids only: budget:low, budget:mid, budget:high, budget:luxury
- Put city-specific season/budget evidence on the edge properties (months, evidence, section).
- graph_id format: {{prefix}}:{{city-slug}}:{{entity-slug}} for city-scoped entities; use lowercase ASCII slugs.
- Derive nodes only from the provided sections; do not invent facts.
- Omit nodes or edges you cannot support from the text.

Section mapping hints:
- intro / understand -> Region, TravelTheme
- see -> Attraction
- do -> Activity
- get_in / go / get_around -> Airport, TransportHub, Route
- climate -> Season (BEST_SEASON edge from city)
- sleep / eat / buy -> optional Activity or skip if insufficient detail

City metadata:
- name: {city_name}
- country: {country}
- wikidata_qid: {wikidata_qid}
- source_url: {source_url}
- city_graph_id: {city_graph_id}

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


async def extract_graph_gemini(
    row: dict,
    sections_payload: list[dict],
    city_gid: str,
) -> GraphExtract:
    prompt = GRAPH_EXTRACT_PROMPT.format(
        labels=", ".join(NEO4J_NODE_LABELS),
        rel_types=", ".join(NEO4J_REL_TYPES),
        city_graph_id=city_gid,
        city_name=row["page_name"],
        country=row["country"],
        wikidata_qid=row.get("wikidata_qid") or "",
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
) -> IngestCounts | None:
    sections = split_sections(row["wikitext"] or "")
    payload = sections_for_gemini(sections)
    print(f"  {len(sections)} section(s), {len(payload)} sent to Gemini")

    city_gid = city_graph_id(row.get("wikidata_qid"), row["source_url"])
    country_gid = country_graph_id(row["country"])
    city_anchor = build_city_anchor(row, city_gid)
    country_anchor = build_country_anchor(row["country"], country_gid)

    extract = await extract_graph_gemini(row, payload, city_gid)
    print(
        f"  Gemini extracted: {len(extract.nodes)} node(s), "
        f"{len(extract.edges)} edge(s)"
    )

    if dry_run:
        print(json.dumps(
            {
                "city_anchor": city_anchor.model_dump(),
                "country_anchor": country_anchor.model_dump(),
                "extract": extract.model_dump(),
            },
            indent=2,
            ensure_ascii=False,
        ))
        counts = IngestCounts()
        counts.bump_node("Country")
        counts.bump_node("City")
        counts.bump_edge("IN_COUNTRY")
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


async def main_async(page: str | None, dry_run: bool) -> None:
    if settings.llm_provider != "gemini":
        print(
            f"ERROR: Neo4j ingest requires LLM_PROVIDER=gemini (got {settings.llm_provider!r})"
        )
        sys.exit(1)
    if not settings.gemini_api_key:
        print(
            f"ERROR: GEMINI_API_KEY not set. Add it to {settings.secrets_env_path} "
            "or the environment."
        )
        sys.exit(1)

    rows = load_cities(page, include_coords=True, add_page_slug=True)
    if not rows:
        print("No rows in wv_cities (run parse-wikivoyage.py first)")
        sys.exit(1)

    print(f"Loaded {len(rows)} city/cities from wv_cities")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    if not dry_run:
        ensure_constraints(driver)

    totals = IngestCounts()

    try:
        for i, row in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] {row['country']} / {row['page_name']}")
            try:
                counts = await ingest_city(driver, row, dry_run=dry_run)
                if counts:
                    for label, n in counts.nodes_by_label.items():
                        totals.nodes_by_label[label] = (
                            totals.nodes_by_label.get(label, 0) + n
                        )
                    for rel, n in counts.edges_by_type.items():
                        totals.edges_by_type[rel] = totals.edges_by_type.get(rel, 0) + n
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
    if totals.nodes_deleted or totals.edges_deleted:
        print(
            f"  deleted (re-ingest): {totals.nodes_deleted} node(s), "
            f"{totals.edges_deleted} edge(s)"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest wv_cities into Neo4j")
    parser.add_argument("--page", help="Single city (page_name or wiki title)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Gemini extract only, no Neo4j writes",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    args = parse_args()
    asyncio.run(main_async(args.page, args.dry_run))
