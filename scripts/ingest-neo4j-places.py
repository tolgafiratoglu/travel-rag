#!/usr/bin/env python3
"""Ingest Wikipedia POIs (wp_pois) into Neo4j as Place + IN_CITY.

Requires City anchors already loaded (ingest-wiki-neo4j.py --anchors-only
or full city ingest). Resolves city by name / aliases.

Usage:
    POSTGRES_HOST=localhost NEO4J_URI=bolt://localhost:7687 \\
    .venv/bin/python scripts/ingest-neo4j-places.py --city Antalya

    .venv/bin/python scripts/ingest-neo4j-places.py --type museum --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "scripts"))


def _require_imports() -> None:
    missing: list[str] = []
    try:
        import psycopg  # noqa: F401
    except ImportError:
        missing.append("psycopg")
    try:
        import neo4j  # noqa: F401
    except ImportError:
        missing.append("neo4j")
    if missing:
        print(f"Missing: pip install {' '.join(missing)}")
        sys.exit(1)


_require_imports()

import os

from neo4j import GraphDatabase

from lib.neo4j_ingest import (
    IngestCounts,
    build_place_from_poi,
    ensure_constraints,
    find_city_graph_id,
    upsert_place_for_city,
)
from lib.wp_db import POIType, load_pois

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")


def main() -> None:
    parser = argparse.ArgumentParser(description="wp_pois → Neo4j Place nodes")
    parser.add_argument(
        "--type",
        choices=["museum", "historical_site", "all"],
        default="all",
    )
    parser.add_argument("--city", help="Filter by city_name")
    parser.add_argument("--country", help="Filter by country_name")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    poi_type: POIType | None = None if args.type == "all" else args.type
    rows = load_pois(
        poi_type,
        country=args.country,
        city=args.city,
        limit=args.limit,
    )
    if not rows:
        print("No POIs found in wp_pois")
        sys.exit(1)

    print(f"Loaded {len(rows)} POI(s)")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    totals = IngestCounts()
    skipped = 0

    try:
        if not args.dry_run:
            ensure_constraints(driver)

        with driver.session() as session:
            for i, row in enumerate(rows, 1):
                city_name = row["city_name"]
                city_gid = find_city_graph_id(session, city_name)
                if not city_gid:
                    print(
                        f"[{i}/{len(rows)}] SKIP (no City): "
                        f"{row['name']} @ {city_name}"
                    )
                    skipped += 1
                    continue

                place = build_place_from_poi(row, city_gid=city_gid)
                print(
                    f"[{i}/{len(rows)}] {place.name} ({row['poi_type']}) "
                    f"→ {city_name} [{city_gid}]"
                )

                if args.dry_run:
                    print(f"    graph_id={place.graph_id}")
                    totals.bump_node("Place")
                    totals.bump_edge("IN_CITY")
                    continue

                counts = upsert_place_for_city(
                    driver,
                    place,
                    city_gid,
                    source_url=row["source_url"],
                )
                for label, n in counts.nodes_by_label.items():
                    totals.nodes_by_label[label] = (
                        totals.nodes_by_label.get(label, 0) + n
                    )
                for rel, n in counts.edges_by_type.items():
                    totals.edges_by_type[rel] = totals.edges_by_type.get(rel, 0) + n
    finally:
        driver.close()

    print("\nDone.")
    print(f"  places upserted: {totals.nodes_by_label.get('Place', 0)}")
    print(f"  IN_CITY edges: {totals.edges_by_type.get('IN_CITY', 0)}")
    print(f"  skipped (no city): {skipped}")


if __name__ == "__main__":
    main()
