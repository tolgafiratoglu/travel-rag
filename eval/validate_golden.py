#!/usr/bin/env python3
"""Validate golden relevant sections against Qdrant inventory.

Usage:
    QDRANT_URL=http://localhost:6333 .venv/bin/python eval/validate_golden.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from qdrant_client import QdrantClient
except ImportError:
    print("Missing: pip install qdrant-client")
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import filter_reachable, load_inventory  # noqa: E402

DEFAULT_GOLDEN = Path(__file__).resolve().parent / "golden" / "retrieval.json"
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "travel_chunks")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate retrieval golden vs Qdrant")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any relevant section is missing",
    )
    args = parser.parse_args()

    data = json.loads(args.golden.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
    names = {c.name for c in client.get_collections().collections}
    if QDRANT_COLLECTION not in names:
        print(f"Collection {QDRANT_COLLECTION!r} not found")
        return 1

    inventory = load_inventory(client, QDRANT_COLLECTION)
    print(f"Inventory: {len(inventory)} source_url(s) in {QDRANT_COLLECTION!r}\n")

    missing_total = 0
    for case in cases:
        case_id = case.get("id") or "?"
        relevant = case.get("relevant") or []
        ok, bad = filter_reachable(relevant, inventory, allow_synonyms=True)
        print(case_id)
        for item in ok:
            print(f"  ✓ {item['section']}")
        for item in bad:
            missing_total += 1
            print(f"  ✗ {item['section']}")
            if item.get("similar"):
                print(f"    available similar: {', '.join(item['similar'])}")
            elif not item.get("source_url"):
                print("    (empty source_url)")
            else:
                print("    (no similar sections / URL missing from collection)")
        if not relevant:
            print("  (no relevant entries)")
        print()

    print(f"Missing relevant refs: {missing_total}")
    if args.strict and missing_total:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
