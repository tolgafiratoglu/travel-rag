#!/usr/bin/env python3
"""Backfill chunk identity metadata onto already-ingested Qdrant points.

Adds, without touching vectors or the embedded ``text`` field:
  chunk_id            point identity (used for true-duplicate removal)
  section_id          shared by every chunk of one page section
  section_chunk_seq   0-based position inside the section
  section_chunk_total chunks the section was split into
  source_kind         guide | museum | historical_site
  body_text           chunk body with the "[Name / Section]" header removed

The header is rebuilt exactly from ``name`` + ``section_path``, so points that
predate the current label format are reported and left with body_text == text
rather than being stripped heuristically.

Usage:
    QDRANT_URL=http://localhost:6333 \\
      .venv/bin/python scripts/backfill-chunk-metadata.py --dry-run

    QDRANT_URL=http://localhost:6333 \\
      .venv/bin/python scripts/backfill-chunk-metadata.py
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        PayloadSchemaType,
        SetPayload,
        SetPayloadOperation,
    )
except ImportError:
    print("Missing: pip install qdrant-client")
    sys.exit(1)

from lib.wp_chunks import (
    assign_section_sequence,
    derive_source_kind,
    make_section_id,
    strip_section_prefix,
)

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()

DEFAULT_COLLECTIONS = [
    os.getenv("QDRANT_COLLECTION", "travel_chunks"),
    os.getenv("QDRANT_COLLECTION_MUSEUMS", "travel_museums"),
    os.getenv("QDRANT_COLLECTION_HISTORICAL", "travel_historical_sites"),
]

INDEXED_FIELDS = ("section_id", "source_kind")


def ensure_payload_indexes(client: QdrantClient, collection: str) -> None:
    """section_id needs a keyword index before grouped retrieval can use it."""
    for field in INDEXED_FIELDS:
        try:
            client.create_payload_index(
                collection_name=collection,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
            print(f"  payload index {field} on {collection!r}")
        except Exception as exc:
            print(f"  payload index {field} ({collection}): {exc}")


def scroll_all(client: QdrantClient, collection: str) -> list[tuple[str, dict]]:
    """Return [(point_id, payload)] for the whole collection."""
    out: list[tuple[str, dict]] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            out.append((str(point.id), point.payload or {}))
        if offset is None:
            break
    return out


def plan_updates(
    records: list[tuple[str, dict]],
) -> tuple[list[dict], Counter]:
    """Build the per-point payload patches plus a report counter."""
    stats: Counter = Counter()
    updates: list[dict] = []

    for point_id, payload in records:
        stats["total"] += 1
        source_url = (payload.get("source_url") or "").strip()
        section_path = (
            payload.get("section_path") or payload.get("section") or ""
        ).strip()
        if not source_url or not section_path:
            stats["skipped_no_section"] += 1
            continue

        body_text, matched = strip_section_prefix(payload)
        stats["prefix_matched" if matched else "prefix_unmatched"] += 1

        source_kind = derive_source_kind(payload)
        stats[f"kind_{source_kind}"] += 1

        updates.append(
            {
                "point_id": point_id,
                "chunk_id": point_id,
                "section_id": make_section_id(source_url, section_path),
                "chunk_index": int(payload.get("chunk_index") or 0),
                "source_kind": source_kind,
                "body_text": body_text,
            }
        )

    # Same numbering helper the ingest path uses, so seq/total agree.
    assign_section_sequence(updates)
    stats["sections"] = len({u["section_id"] for u in updates})
    return updates, stats


def apply_updates(
    client: QdrantClient,
    collection: str,
    updates: list[dict],
    *,
    batch_size: int,
) -> int:
    written = 0
    for start in range(0, len(updates), batch_size):
        batch = updates[start : start + batch_size]
        operations = [
            SetPayloadOperation(
                set_payload=SetPayload(
                    payload={
                        "chunk_id": u["chunk_id"],
                        "section_id": u["section_id"],
                        "section_chunk_seq": u["section_chunk_seq"],
                        "section_chunk_total": u["section_chunk_total"],
                        "source_kind": u["source_kind"],
                        "body_text": u["body_text"],
                    },
                    points=[u["point_id"]],
                )
            )
            for u in batch
        ]
        client.batch_update_points(
            collection_name=collection,
            update_operations=operations,
        )
        written += len(batch)
        print(f"  set payload {written}/{len(updates)}")
    return written


def print_samples(updates: list[dict], limit: int = 3) -> None:
    for u in updates[:limit]:
        head = u["body_text"][:80].replace("\n", " ")
        print(
            f"    seq={u['section_chunk_seq']}/{u['section_chunk_total']} "
            f"kind={u['source_kind']} section_id={u['section_id'][:8]}… "
            f"body={head!r}"
        )


def run(collections: list[str], *, dry_run: bool, batch_size: int) -> None:
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
    existing = {c.name for c in client.get_collections().collections}

    for collection in collections:
        print(f"\n{collection}")
        if collection not in existing:
            print("  not found — skipped")
            continue

        records = scroll_all(client, collection)
        updates, stats = plan_updates(records)
        print(
            f"  points={stats['total']} sections={stats['sections']} "
            f"prefix_ok={stats['prefix_matched']} "
            f"prefix_miss={stats['prefix_unmatched']} "
            f"no_section={stats['skipped_no_section']}"
        )
        kinds = {k[5:]: v for k, v in stats.items() if k.startswith("kind_")}
        print(f"  source_kind={kinds}")
        if stats["prefix_unmatched"]:
            print(
                f"  note: {stats['prefix_unmatched']} point(s) kept "
                "body_text == text (header format not recognised)"
            )
        if not updates:
            print("  nothing to backfill")
            continue

        print_samples(updates)
        if dry_run:
            print("  dry run — no writes")
            continue

        ensure_payload_indexes(client, collection)
        apply_updates(client, collection, updates, batch_size=batch_size)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill chunk/section identity metadata in Qdrant"
    )
    parser.add_argument(
        "--collection",
        action="append",
        dest="collections",
        help="Collection to backfill (repeatable; default: all three)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=128)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        args.collections or DEFAULT_COLLECTIONS,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    )
