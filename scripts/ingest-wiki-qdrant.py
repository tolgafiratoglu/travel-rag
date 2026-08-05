#!/usr/bin/env python3
"""Ingest Wikipedia guide pages (wp_pages) into Qdrant travel_chunks.

Uses Unstructured chunk_by_title (via lib.wp_chunks). Payload includes `title`.

Usage:
    MARIADB_HOST=localhost .venv/bin/python scripts/ingest-wiki-qdrant.py \\
        --city Istanbul --dry-run

    MARIADB_HOST=localhost QDRANT_URL=http://localhost:6333 \\
    OLLAMA_BASE_URL=http://localhost:11434 \\
    .venv/bin/python scripts/ingest-wiki-qdrant.py --country Turkey
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
from lib.wp_pages_db import load_pages

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


def ensure_payload_indexes(client: QdrantClient) -> None:
    for field in ("city_name", "title", "page_kind"):
        try:
            client.create_payload_index(
                collection_name=QDRANT_COLLECTION,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
            print(f"  payload index {field} on {QDRANT_COLLECTION!r}")
        except Exception as exc:
            print(f"  payload index {field}: {exc}")


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
    ensure_payload_indexes(client)


def ensure_collection(client: QdrantClient, dim: int) -> None:
    names = {c.name for c in client.get_collections().collections}
    if QDRANT_COLLECTION not in names:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        print(f"  created {QDRANT_COLLECTION!r} dim={dim}")
    ensure_payload_indexes(client)


def delete_page_points(client: QdrantClient, source_url: str) -> None:
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="source_url", match=MatchValue(value=source_url))]
        ),
    )


def qdrant_payload(chunk: dict) -> dict:
    return {k: v for k, v in chunk.items() if v is not None}


async def ingest_page(
    qdrant: QdrantClient,
    http: httpx.AsyncClient,
    row: dict,
    dry_run: bool,
    *,
    wiped: bool,
) -> int:
    chunks = build_chunks(row)
    print(f"  {len(chunks)} chunk(s) title={row.get('title') or row.get('resolved_title')!r}")

    if dry_run:
        for chunk in chunks[:5]:
            print(
                f"    [{chunk['section_path']}] "
                f"{chunk['text'][:120].replace(chr(10), ' ')}..."
            )
        if len(chunks) > 5:
            print(f"    ... +{len(chunks) - 5} more")
        return len(chunks)

    if not wiped:
        delete_page_points(qdrant, row["source_url"])

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

    if points:
        qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)
    return len(chunks)


async def main_async(
    country: str | None,
    city: str | None,
    page_kind: str | None,
    limit: int | None,
    dry_run: bool,
    wipe: bool,
) -> None:
    rows = load_pages(country=country, city=city, page_kind=page_kind, limit=limit)
    if not rows:
        print("No rows in wp_pages (run parse-wiki-pages.py first)")
        sys.exit(1)

    print(f"Loaded {len(rows)} page(s) from wp_pages")
    print(f"Embed model: {OLLAMA_EMBED_MODEL} → {QDRANT_COLLECTION}")
    qdrant = QdrantClient(url=QDRANT_URL)
    total = 0
    wiped = False

    async with httpx.AsyncClient(timeout=120.0) as http:
        if wipe and not dry_run:
            probe = await embed_text(http, "dimension probe")
            recreate_collection(qdrant, len(probe))
            wiped = True

        for i, row in enumerate(rows, 1):
            print(
                f"[{i}/{len(rows)}] {row['page_kind']} | "
                f"{row['country_name']} / {row['city_name']}"
            )
            try:
                n = await ingest_page(qdrant, http, row, dry_run, wiped=wiped)
                total += n
                if not dry_run:
                    print(f"  upserted {n} point(s)")
            except Exception as exc:
                print(f"  ERROR: {exc}")

    print(f"Done. Total chunks: {total}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest wp_pages into travel_chunks")
    parser.add_argument("--country", help="Filter country_name")
    parser.add_argument("--city", help="Filter city_name")
    parser.add_argument(
        "--kind",
        choices=("city", "tourism", "attraction_list"),
        help="Filter page_kind",
    )
    parser.add_argument("--limit", type=int, help="Max pages")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--wipe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Recreate travel_chunks before ingest (default: true)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        main_async(
            args.country,
            args.city,
            args.kind,
            args.limit,
            args.dry_run,
            args.wipe,
        )
    )
