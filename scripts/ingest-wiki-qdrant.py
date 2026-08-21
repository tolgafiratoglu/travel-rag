#!/usr/bin/env python3
"""Ingest Wikipedia guide pages (wp_pages) into Qdrant travel_chunks.

Uses Unstructured chunk_by_title (via lib.wp_chunks). Payload includes `title`.

Usage:
    MARIADB_HOST=localhost .venv/bin/python scripts/ingest-wiki-qdrant.py \\
        --city Istanbul --dry-run

    # Scoped wipe: only Istanbul points removed, other cities kept
    MARIADB_HOST=localhost QDRANT_URL=http://localhost:6333 \\
    OLLAMA_BASE_URL=http://localhost:11434 \\
    .venv/bin/python scripts/ingest-wiki-qdrant.py --city Istanbul

    # Country-scoped wipe (other countries kept)
    .venv/bin/python scripts/ingest-wiki-qdrant.py --country Turkey

    # Full wipe: recreate travel_chunks (no --city/--country)
    .venv/bin/python scripts/ingest-wiki-qdrant.py

    # Secured Qdrant (prod): admin key required for upsert/wipe
    # QDRANT_API_KEY=$QDRANT_ADMIN_API_KEY .venv/bin/python scripts/ingest-wiki-qdrant.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        FieldCondition,
        Filter,
        MatchAny,
        MatchValue,
        PayloadSchemaType,
        PointStruct,
        VectorParams,
    )
except ImportError:
    print("Missing: pip install pymysql httpx qdrant-client")
    sys.exit(1)

from lib.wp_chunks import build_chunks, make_point_id
from lib.wp_pages_db import (
    bump_section_stats,
    ensure_section_stats_table,
    load_pages,
    reset_section_stats,
)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
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
    names = {c.name for c in client.get_collections().collections}
    if QDRANT_COLLECTION not in names:
        return
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="source_url", match=MatchValue(value=source_url))]
        ),
    )


def delete_scoped_points(
    client: QdrantClient,
    *,
    city_names: list[str] | None = None,
    country_name: str | None = None,
    page_kind: str | None = None,
) -> None:
    """Delete points matching city/country/(optional page_kind) — scoped wipe."""
    names = {c.name for c in client.get_collections().collections}
    if QDRANT_COLLECTION not in names:
        print(f"  collection {QDRANT_COLLECTION!r} missing — skip scoped wipe")
        return

    must: list[FieldCondition] = []
    if country_name:
        must.append(
            FieldCondition(
                key="country_name", match=MatchValue(value=country_name)
            )
        )
    if city_names:
        unique = list(dict.fromkeys(n for n in city_names if n))
        if len(unique) == 1:
            must.append(
                FieldCondition(
                    key="city_name", match=MatchValue(value=unique[0])
                )
            )
        elif unique:
            must.append(
                FieldCondition(
                    key="city_name", match=MatchAny(any=unique)
                )
            )
    if page_kind:
        must.append(
            FieldCondition(key="page_kind", match=MatchValue(value=page_kind))
        )
    if not must:
        return

    scope = []
    if country_name:
        scope.append(f"country={country_name!r}")
    if city_names:
        scope.append(f"city={list(dict.fromkeys(n for n in city_names if n))!r}")
    if page_kind:
        scope.append(f"kind={page_kind!r}")
    print(f"  scoped wipe {QDRANT_COLLECTION!r} ({', '.join(scope)})")
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(must=must),
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
        from collections import Counter

        sec_counts = Counter(
            (c.get("section") or c.get("section_path") or "").strip() or "?"
            for c in chunks
        )
        for sec, n in sec_counts.most_common(10):
            print(f"    section {sec!r}: {n}")
        if len(sec_counts) > 10:
            print(f"    ... +{len(sec_counts) - 10} sections")
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

    n_sec = bump_section_stats(chunks)
    print(
        f"  upserted={len(points)}; "
        f"section stats: {n_sec} distinct section(s) bumped"
    )
    return len(points)


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
    if not dry_run:
        ensure_section_stats_table()
    qdrant = QdrantClient(
        url=QDRANT_URL, api_key=QDRANT_API_KEY or None
    )
    total = 0
    wiped = False

    scoped = bool(city or country)

    async with httpx.AsyncClient(timeout=120.0) as http:
        if wipe and not dry_run:
            probe = await embed_text(http, "dimension probe")
            if scoped:
                # Keep other cities; only drop points for this filter scope.
                ensure_collection(qdrant, len(probe))
                cities = sorted(
                    {
                        (r.get("city_name") or "").strip()
                        for r in rows
                        if (r.get("city_name") or "").strip()
                    }
                )
                if city:
                    delete_scoped_points(
                        qdrant, city_names=cities, page_kind=page_kind
                    )
                else:
                    delete_scoped_points(
                        qdrant, country_name=country, page_kind=page_kind
                    )
                print("  skip full wp_section_stats reset (scoped wipe)")
                wiped = True
            else:
                recreate_collection(qdrant, len(probe))
                reset_section_stats()
                print("  reset wp_section_stats")
                wiped = True

        for i, row in enumerate(rows, 1):
            print(
                f"[{i}/{len(rows)}] {row['page_kind']} | "
                f"{row['country_name']} / {row['city_name']}"
            )
            try:
                n = await ingest_page(
                    qdrant,
                    http,
                    row,
                    dry_run,
                    wiped=wiped,
                )
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
        help=(
            "Before ingest: with --city/--country delete only matching points; "
            "without filters recreate the whole collection (default: true)"
        ),
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
