#!/usr/bin/env python3
"""[Legacy] Ingest Wikivoyage wikitext from MariaDB into Qdrant (bge-m3).

Prefer Wikipedia guide ingest:
  scripts/parse-wiki-pages.py → scripts/ingest-wiki-qdrant.py → travel_chunks

Parses see/do/go listing templates into dedicated chunks, then allowed-section
prose into separate chunks. Section allowlist (see/do/get_in/districts/go_next,
understand history/climate, get_around transit/walk/bike only) excludes sleep,
eat, drink, stay_safe, taxi/car commentary, and similar high-risk topics.

By default recreates the target collection (wipe) so embeddings stay on one
model/dimension. Use --no-wipe for incremental city upserts.

Usage:
    MARIADB_HOST=localhost .venv/bin/python scripts/ingest-wikivoyage-qdrant.py \\
        --page Bordeaux --dry-run

    MARIADB_HOST=localhost QDRANT_URL=http://localhost:6333 \\
    OLLAMA_BASE_URL=http://localhost:11434 \\
    .venv/bin/python scripts/ingest-wikivoyage-qdrant.py
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

from lib.wv_chunks import build_chunks, make_point_id
from lib.wv_db import load_cities

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "travel_chunks")


async def embed_text(client: httpx.AsyncClient, text: str) -> list[float]:
    resp = await client.post(
        f"{OLLAMA_BASE_URL}/api/embed",
        json={"model": OLLAMA_EMBED_MODEL, "input": text},
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def ensure_city_name_index(client: QdrantClient) -> None:
    try:
        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="city_name",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        print(f"  payload index city_name on {QDRANT_COLLECTION!r}")
    except Exception as exc:
        # Already exists or server variant — safe to continue
        print(f"  payload index city_name: {exc}")


def recreate_collection(client: QdrantClient, dim: int) -> None:
    names = {c.name for c in client.get_collections().collections}
    if QDRANT_COLLECTION in names:
        print(f"  wiping collection {QDRANT_COLLECTION!r}")
        client.delete_collection(QDRANT_COLLECTION)
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    print(f"  created {QDRANT_COLLECTION!r} dim={dim}")
    ensure_city_name_index(client)


def ensure_collection(client: QdrantClient, dim: int) -> None:
    names = {c.name for c in client.get_collections().collections}
    if QDRANT_COLLECTION not in names:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        ensure_city_name_index(client)
    else:
        ensure_city_name_index(client)


def delete_city_points(client: QdrantClient, source_url: str) -> None:
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="source_url", match=MatchValue(value=source_url))]
        ),
    )


def qdrant_payload(chunk: dict) -> dict:
    return {k: v for k, v in chunk.items() if v is not None}


async def ingest_city(
    qdrant: QdrantClient,
    http: httpx.AsyncClient,
    row: dict,
    dry_run: bool,
    *,
    wiped: bool,
) -> tuple[int, int, int]:
    chunks = build_chunks(row)
    listing_n = sum(1 for c in chunks if c["chunk_kind"] == "listing")
    section_n = sum(1 for c in chunks if c["chunk_kind"] == "section")
    print(f"  {len(chunks)} chunk(s): {listing_n} listing, {section_n} section")

    if dry_run:
        for chunk in chunks[:5]:
            kind = chunk["chunk_kind"]
            label = chunk.get("name") or chunk["section_path"]
            print(f"    [{kind}] {chunk['topic']} / {label}: {chunk['text'][:100]}...")
        if len(chunks) > 5:
            print(f"    ... +{len(chunks) - 5} more")
        return listing_n, section_n, len(chunks)

    if not wiped:
        delete_city_points(qdrant, row["source_url"])

    points: list[PointStruct] = []
    for i, chunk in enumerate(chunks):
        vector = await embed_text(http, chunk["text"])
        if not points:
            ensure_collection(qdrant, len(vector))
        points.append(
            PointStruct(
                id=make_point_id(chunk),
                vector=vector,
                payload=qdrant_payload(chunk),
            )
        )
        if (i + 1) % 20 == 0:
            print(f"  embedded {i + 1}/{len(chunks)}")

    qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)
    return listing_n, section_n, len(chunks)


async def main_async(page: str | None, dry_run: bool, wipe: bool) -> None:
    rows = load_cities(page)
    if not rows:
        print("No rows in wv_cities (run parse-wikivoyage.py first)")
        sys.exit(1)

    print(f"Loaded {len(rows)} city/cities from wv_cities")
    print(f"Embed model: {OLLAMA_EMBED_MODEL}")
    qdrant = QdrantClient(url=QDRANT_URL)
    totals = {"listing": 0, "section": 0, "all": 0}
    wiped = False

    async with httpx.AsyncClient(timeout=120.0) as http:
        if wipe and not dry_run:
            probe = await embed_text(http, "dimension probe")
            recreate_collection(qdrant, len(probe))
            wiped = True

        for i, row in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] {row['country']} / {row['page_name']}")
            try:
                listing_n, section_n, all_n = await ingest_city(
                    qdrant, http, row, dry_run, wiped=wiped
                )
                totals["listing"] += listing_n
                totals["section"] += section_n
                totals["all"] += all_n
                if not dry_run:
                    print(f"  upserted {all_n} point(s)")
            except Exception as exc:
                print(f"  ERROR: {exc}")

    print(
        f"Done. Total: {totals['all']} chunks "
        f"({totals['listing']} listing, {totals['section']} section)"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest wv_cities into Qdrant")
    parser.add_argument("--page", help="Single city (page_name or wiki title)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no embed")
    parser.add_argument(
        "--wipe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Recreate collection before ingest (default: true)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main_async(args.page, args.dry_run, args.wipe))
