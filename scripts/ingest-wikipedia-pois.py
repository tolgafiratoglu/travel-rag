#!/usr/bin/env python3
"""Ingest Wikipedia POIs from MariaDB (wp_pois) into Qdrant via Unstructured.

Collections:
  travel_museums            (poi_type=museum)
  travel_historical_sites   (poi_type=historical_site)

Uses Ollama bge-m3 by default. Recreates target collection(s) unless --no-wipe.

Usage:
    MARIADB_HOST=localhost .venv/bin/python scripts/ingest-wikipedia-pois.py \\
        --type museum --city Ankara --dry-run

    MARIADB_HOST=localhost QDRANT_URL=http://localhost:6333 \\
    OLLAMA_BASE_URL=http://localhost:11434 \\
    .venv/bin/python scripts/ingest-wikipedia-pois.py --type all
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        FieldCondition,
        Filter,
        MatchValue,
        PayloadSchemaType,
        PointStruct,
        VectorParams,
    )
except ImportError:
    print("Missing: pip install pymysql httpx qdrant-client")
    sys.exit(1)

from lib.wp_chunks import build_chunks, make_point_id
from lib.wp_db import load_pois

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
COLLECTION_MUSEUMS = os.getenv("QDRANT_COLLECTION_MUSEUMS", "travel_museums")
COLLECTION_HISTORICAL = os.getenv(
    "QDRANT_COLLECTION_HISTORICAL", "travel_historical_sites"
)

TYPE_TO_COLLECTION = {
    "museum": COLLECTION_MUSEUMS,
    "historical_site": COLLECTION_HISTORICAL,
}


async def embed_text(client: httpx.AsyncClient, text: str) -> list[float]:
    resp = await client.post(
        f"{OLLAMA_BASE_URL}/api/embed",
        json={"model": OLLAMA_EMBED_MODEL, "input": text},
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def ensure_city_name_index(client: QdrantClient, name: str) -> None:
    try:
        client.create_payload_index(
            collection_name=name,
            field_name="city_name",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        print(f"  payload index city_name on {name!r}")
    except Exception as exc:
        print(f"  payload index city_name ({name}): {exc}")


def recreate_collection(client: QdrantClient, name: str, dim: int) -> None:
    names = {c.name for c in client.get_collections().collections}
    if name in names:
        print(f"  wiping collection {name!r}")
        client.delete_collection(name)
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    print(f"  created {name!r} dim={dim}")
    ensure_city_name_index(client, name)


def ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    names = {c.name for c in client.get_collections().collections}
    if name not in names:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
    ensure_city_name_index(client, name)


def delete_poi_points(client: QdrantClient, collection: str, source_url: str) -> None:
    client.delete(
        collection_name=collection,
        points_selector=Filter(
            must=[FieldCondition(key="source_url", match=MatchValue(value=source_url))]
        ),
    )


def qdrant_payload(chunk: dict) -> dict:
    return {k: v for k, v in chunk.items() if v is not None}


async def ingest_row(
    qdrant: QdrantClient,
    http: httpx.AsyncClient,
    row: dict,
    dry_run: bool,
    *,
    wiped_collections: set[str],
) -> int:
    collection = TYPE_TO_COLLECTION[row["poi_type"]]
    chunks = build_chunks(row)
    print(f"  {len(chunks)} chunk(s) → {collection}")

    if dry_run:
        for chunk in chunks[:5]:
            print(
                f"    [{chunk['section_path']}] {chunk['text'][:120].replace(chr(10), ' ')}..."
            )
        if len(chunks) > 5:
            print(f"    ... +{len(chunks) - 5} more")
        return len(chunks)

    if collection not in wiped_collections:
        delete_poi_points(qdrant, collection, row["source_url"])

    points: list[PointStruct] = []
    for i, chunk in enumerate(chunks):
        vector = await embed_text(http, chunk["text"])
        if not points:
            ensure_collection(qdrant, collection, len(vector))
        points.append(
            PointStruct(
                id=make_point_id(chunk),
                vector=vector,
                payload=qdrant_payload(chunk),
            )
        )
        if (i + 1) % 20 == 0:
            print(f"  embedded {i + 1}/{len(chunks)}")

    if points:
        qdrant.upsert(collection_name=collection, points=points)
    return len(chunks)


async def main_async(
    poi_type: str | None,
    country: str | None,
    city: str | None,
    limit: int | None,
    dry_run: bool,
    wipe: bool,
) -> None:
    rows = load_pois(poi_type, country=country, city=city, limit=limit)
    if not rows:
        print("No rows in wp_pois (run parse-wikipedia-pois.py first)")
        sys.exit(1)

    types_present = sorted({r["poi_type"] for r in rows})
    print(f"Loaded {len(rows)} POI row(s) types={types_present}")
    print(f"Embed model: {OLLAMA_EMBED_MODEL}")

    qdrant = QdrantClient(
        url=QDRANT_URL, api_key=QDRANT_API_KEY or None
    )
    wiped: set[str] = set()
    total = 0

    async with httpx.AsyncClient(timeout=120.0) as http:
        if wipe and not dry_run:
            probe = await embed_text(http, "dimension probe")
            for t in types_present:
                name = TYPE_TO_COLLECTION[t]
                recreate_collection(qdrant, name, len(probe))
                wiped.add(name)

        for i, row in enumerate(rows, 1):
            print(
                f"[{i}/{len(rows)}] {row['poi_type']} | "
                f"{row['country_name']} / {row['city_name']} / {row['name']}"
            )
            try:
                n = await ingest_row(
                    qdrant, http, row, dry_run, wiped_collections=wiped
                )
                total += n
                if not dry_run:
                    print(f"  upserted {n} point(s)")
            except Exception as exc:
                print(f"  ERROR: {exc}")

    print(f"Done. Total chunks: {total}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest wp_pois into Qdrant")
    parser.add_argument(
        "--type",
        choices=("museum", "historical_site", "all"),
        default="all",
    )
    parser.add_argument("--country", help="Filter country_name")
    parser.add_argument("--city", help="Filter city_name")
    parser.add_argument("--limit", type=int, help="Max POI rows")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--wipe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Recreate target collection(s) before ingest (default: true)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    poi_type = None if args.type == "all" else args.type
    asyncio.run(
        main_async(
            poi_type,
            args.country,
            args.city,
            args.limit,
            args.dry_run,
            args.wipe,
        )
    )
