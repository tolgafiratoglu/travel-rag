#!/usr/bin/env python3
"""Ingest Wikivoyage cities into Neo4j (v1 schema — neo4j.md).

Deterministic: Country, City, IN_COUNTRY.
Optional Gemini (3 narrow passes): SERVED_BY_AIRPORT, CONNECTED_BY, DAY_TRIP_FROM.

Places (museums / historical) come from scripts/ingest-neo4j-places.py.

Usage:
    MARIADB_HOST=localhost NEO4J_URI=bolt://localhost:7687 \\
    LLM_PROVIDER=gemini \\
    .venv/bin/python scripts/ingest-wikivoyage-neo4j.py --page Antalya

    .venv/bin/python scripts/ingest-wikivoyage-neo4j.py --page Antalya --anchors-only
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
import time
from dataclasses import dataclass
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
    city_graph_id,
    country_graph_id,
)
from app.models.graph_extract import GraphEdge, GraphExtract, GraphNode, validate_graph_extract
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
PASS_SLEEP = float(os.getenv("WV_NEO4J_PASS_SLEEP", "0.4"))
RAW_DUMP_DIR = Path(
    os.getenv("WV_NEO4J_RAW_DIR", str(ROOT / "tmp" / "neo4j-gemini-raw"))
)

# Narrow passes: one relation family, few topics, small JSON.
PASS_AIRPORT = "airport"
PASS_CONNECTED = "connected"
PASS_DAY_TRIP = "day_trip"


@dataclass(frozen=True)
class RelationPass:
    key: str
    topics: frozenset[str]
    node_labels: tuple[str, ...]
    rel_type: str
    max_edges: int
    max_section_chars: int
    max_total_chars: int
    instructions: str


RELATION_PASSES: tuple[RelationPass, ...] = (
    RelationPass(
        key=PASS_AIRPORT,
        topics=frozenset({"get_in"}),
        node_labels=("Airport",),
        rel_type="SERVED_BY_AIRPORT",
        max_edges=3,
        max_section_chars=1800,
        max_total_chars=5000,
        instructions=(
            "Extract airports that serve THIS city.\n"
            "Edges: City -> Airport, type SERVED_BY_AIRPORT only.\n"
            "Node label: Airport only. graph_id airport:IATA or airport:slug-name.\n"
            "Max 3 edges. Omit duration fields."
        ),
    ),
    RelationPass(
        key=PASS_CONNECTED,
        topics=frozenset({"get_in", "go_next"}),
        node_labels=("City",),
        rel_type="CONNECTED_BY",
        max_edges=5,
        max_section_chars=1800,
        max_total_chars=6000,
        instructions=(
            "Extract transport links from THIS city to OTHER cities.\n"
            "Edges: current city -> other City, type CONNECTED_BY only.\n"
            "Emit other City nodes (not the current city). "
            "graph_id city:slug-name.\n"
            "Properties: mode (flight|bus|ferry|train), optional duration_min/"
            "duration_max (minutes, only if stated), direct, evidence_text, confidence.\n"
            "NEVER invent durations. Max 5 edges."
        ),
    ),
    RelationPass(
        key=PASS_DAY_TRIP,
        topics=frozenset({"see", "go_next", "do"}),
        node_labels=("Place",),
        rel_type="DAY_TRIP_FROM",
        max_edges=5,
        max_section_chars=1800,
        max_total_chars=6000,
        instructions=(
            "Extract nearby places that are day trips FROM this city.\n"
            "Edges: Place -> City, type DAY_TRIP_FROM only "
            "(Place is a day trip from the city).\n"
            "Node label: Place. graph_id place:slug-name. "
            "type one of museum|historical_site|attraction|natural_site|"
            "religious_site|archaeological_site.\n"
            "Optional one_way_minutes only if stated. Max 5 edges. "
            "NEVER invent times."
        ),
    ),
)

PASS_PROMPT = """You extract a SMALL travel graph fragment. Return ONLY valid compact JSON.
No markdown fences, no comments, no trailing commas.

Current city graph_id (use EXACTLY on edges; do NOT emit a City node for it):
{city_graph_id}

Allowed node labels: {labels}
Allowed relationship type: {rel_type} ONLY
Max edges: {max_edges}

Task:
{instructions}

JSON shape:
{{
  "nodes": [{{"label":"...","graph_id":"...","name":"...","properties":{{}}}}],
  "edges": [{{
    "from_graph_id":"...",
    "to_graph_id":"...",
    "type":"{rel_type}",
    "properties": {{"evidence_text":"short quote <=120 chars","confidence":0.8}}
  }}]
}}

City: {city_name} ({country})
Source: {source_url}

Sections:
{sections_json}
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        text = fence.group(1).strip()
    # Strip // and /* */ style noise sometimes inserted by models.
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in Gemini response")
    return json.loads(text[start : end + 1])


def _dump_raw(city_slug: str, pass_key: str, raw: str, err: Exception) -> Path:
    RAW_DUMP_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DUMP_DIR / f"{city_slug}_{pass_key}_{int(time.time())}.txt"
    path.write_text(
        f"ERROR: {err}\n\n----- raw -----\n{raw}\n",
        encoding="utf-8",
    )
    return path


def _sections_for_pass(sections: list[dict], rel_pass: RelationPass) -> list[dict]:
    filtered = [
        s
        for s in sections
        if (s.get("topic") or "").casefold() in rel_pass.topics
    ]
    return sections_for_gemini(
        filtered,
        max_section_chars=rel_pass.max_section_chars,
        max_total_chars=rel_pass.max_total_chars,
    )


def _merge_extracts(parts: list[GraphExtract]) -> GraphExtract:
    nodes_by_id: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    seen_edge: set[tuple[str, str, str]] = set()
    for part in parts:
        for node in part.nodes:
            nodes_by_id[node.graph_id] = node
        for edge in part.edges:
            key = (edge.from_graph_id, edge.to_graph_id, edge.type)
            if key in seen_edge:
                continue
            seen_edge.add(key)
            edges.append(edge)
    return GraphExtract(nodes=list(nodes_by_id.values()), edges=edges)


def _filter_pass_edges(extract: GraphExtract, rel_type: str, max_edges: int) -> GraphExtract:
    edges = [e for e in extract.edges if e.type == rel_type][:max_edges]
    needed = {e.from_graph_id for e in edges} | {e.to_graph_id for e in edges}
    nodes = [n for n in extract.nodes if n.graph_id in needed]
    return GraphExtract(nodes=nodes, edges=edges)


async def _gemini_pass(
    row: dict,
    city_gid: str,
    rel_pass: RelationPass,
    sections_payload: list[dict],
    *,
    retries: int = 1,
) -> GraphExtract:
    from app.services import llm

    if not sections_payload:
        print(f"    pass {rel_pass.key}: no sections, skip")
        return GraphExtract()

    prompt = PASS_PROMPT.format(
        city_graph_id=city_gid,
        labels=", ".join(rel_pass.node_labels),
        rel_type=rel_pass.rel_type,
        max_edges=rel_pass.max_edges,
        instructions=rel_pass.instructions,
        city_name=row["page_name"],
        country=row["country"],
        source_url=row["source_url"],
        sections_json=json.dumps(sections_payload, ensure_ascii=False),
    )
    country_gid = country_graph_id(row["country"])
    city_slug = (row.get("page_slug") or row["page_name"]).replace("/", "-")
    last_err: Exception | None = None
    raw = ""

    for attempt in range(retries + 1):
        try:
            raw = await llm.generate(prompt)
            data = _extract_json(raw)
            extract = GraphExtract.model_validate(data)
            extract = validate_graph_extract(
                extract,
                city_graph_id=city_gid,
                country_graph_id=country_gid,
                strict=False,
                llm_only=True,
            )
            extract = _filter_pass_edges(
                extract, rel_pass.rel_type, rel_pass.max_edges
            )
            print(
                f"    pass {rel_pass.key}: "
                f"{len(extract.nodes)} node(s), {len(extract.edges)} edge(s)"
                + (f" (retry {attempt})" if attempt else "")
            )
            return extract
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_err = exc
            dump = _dump_raw(city_slug, rel_pass.key, raw or "", exc)
            print(
                f"    pass {rel_pass.key}: parse fail ({exc}); "
                f"raw → {dump}"
            )
            if attempt < retries:
                await asyncio.sleep(0.5)

    print(f"    pass {rel_pass.key}: giving up ({last_err})")
    return GraphExtract()


async def extract_relations_multipart(
    row: dict,
    sections: list[dict],
    city_gid: str,
) -> GraphExtract:
    parts: list[GraphExtract] = []
    for i, rel_pass in enumerate(RELATION_PASSES):
        payload = _sections_for_pass(sections, rel_pass)
        print(
            f"    pass {rel_pass.key}: {len(payload)} section chunk(s) → "
            f"{rel_pass.rel_type}"
        )
        part = await _gemini_pass(row, city_gid, rel_pass, payload, retries=1)
        parts.append(part)
        if i < len(RELATION_PASSES) - 1 and PASS_SLEEP > 0:
            await asyncio.sleep(PASS_SLEEP)
    merged = _merge_extracts(parts)
    print(
        f"  Gemini merged: {len(merged.nodes)} node(s), "
        f"{len(merged.edges)} edge(s)"
    )
    return merged


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
    print(f"  {len(sections)} section(s)")

    city_gid = city_graph_id(row.get("wikidata_qid"), row["source_url"])
    city_anchor = build_city_anchor(row, city_gid)
    country_anchor = build_country_anchor(row["country"], country_graph_id(row["country"]))

    if anchors_only:
        extract = GraphExtract()
        print("  anchors-only: skipping Gemini")
    else:
        extract = await extract_relations_multipart(row, sections, city_gid)

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
            except (ValidationError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
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
