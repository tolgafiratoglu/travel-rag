#!/usr/bin/env python3
"""Retrieval eval for travel_chunks: Hit@k, Recall@k, MRR.

Unreachable golden sections (not in Qdrant) are excluded from scoring.
Run ``eval/validate_golden.py`` first to inspect mismatches.

Usage:
    QDRANT_URL=http://localhost:6333 OLLAMA_BASE_URL=http://localhost:11434 \\
      .venv/bin/python eval/run_retrieval_eval.py

    .venv/bin/python eval/run_retrieval_eval.py --k 10 --limit 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

try:
    from qdrant_client import QdrantClient
except ImportError:
    print("Missing: pip install httpx qdrant-client")
    sys.exit(1)

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parents[0]
sys.path.insert(0, str(EVAL_DIR))

from common import (  # noqa: E402
    build_city_filter,
    filter_reachable,
    load_inventory,
    norm_url,
    relevant_key,
    section_equiv,
    section_leaf,
)

DEFAULT_GOLDEN = EVAL_DIR / "golden" / "retrieval.json"
DEFAULT_RESULTS_DIR = EVAL_DIR / "results"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "travel_chunks")


def _hit_matches(payload: dict, relevant: list[dict]) -> list[tuple[str, str]]:
    url = norm_url(payload.get("source_url"))
    leaf = section_leaf(payload.get("section") or payload.get("section_path"))
    matched: list[tuple[str, str]] = []
    for item in relevant:
        key = relevant_key(item)
        if not key[0] or not key[1]:
            continue
        if url != key[0]:
            continue
        if section_equiv(leaf, key[1]):
            matched.append(key)
    return matched


async def embed(client: httpx.AsyncClient, text: str) -> list[float]:
    resp = await client.post(
        f"{OLLAMA_BASE_URL}/api/embed",
        json={"model": OLLAMA_EMBED_MODEL, "input": text},
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def search_qdrant(
    qdrant: QdrantClient,
    vector: list[float],
    *,
    top_k: int,
    city: str | None,
    dedupe_section: bool = True,
) -> list[dict]:
    fetch_k = top_k * 4 if dedupe_section else top_k
    results = qdrant.query_points(
        collection_name=QDRANT_COLLECTION,
        query=vector,
        query_filter=build_city_filter(city),
        limit=fetch_k,
        with_payload=True,
    )
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for point in results.points or []:
        payload = point.payload or {}
        url = payload.get("source_url") or ""
        section = payload.get("section") or payload.get("section_path") or ""
        key = (norm_url(url), section_leaf(section))
        if dedupe_section and key in seen:
            continue
        if dedupe_section:
            seen.add(key)
        out.append(
            {
                "score": point.score,
                "source_url": url,
                "section": section,
                "section_group": payload.get("section_group") or "",
                "city_name": payload.get("city_name") or "",
                "title": payload.get("title") or "",
            }
        )
        if len(out) >= top_k:
            break
    return out


def score_case(relevant: list[dict], retrieved: list[dict]) -> dict:
    relevant_by_key: dict[tuple[str, str], dict] = {}
    for item in relevant:
        key = relevant_key(item)
        if not key[0] or not key[1] or key in relevant_by_key:
            continue
        relevant_by_key[key] = {
            "source_url": (item.get("source_url") or "").strip(),
            "section": (item.get("section") or "").strip(),
        }

    if not relevant_by_key:
        return {
            "hit": 0.0,
            "recall": 0.0,
            "rr": 0.0,
            "first_rank": None,
            "matched": [],
            "relevant_count": 0,
            "matched_count": 0,
            "scored": False,
        }

    matched_keys: set[tuple[str, str]] = set()
    first_rank: int | None = None
    for rank, chunk in enumerate(retrieved, start=1):
        hits = _hit_matches(chunk, relevant)
        if not hits:
            continue
        if first_rank is None:
            first_rank = rank
        matched_keys.update(hits)

    hit = 1.0 if matched_keys else 0.0
    recall = len(matched_keys) / len(relevant_by_key)
    rr = (1.0 / first_rank) if first_rank else 0.0
    return {
        "hit": hit,
        "recall": recall,
        "rr": rr,
        "first_rank": first_rank,
        "matched": [relevant_by_key[k] for k in sorted(matched_keys)],
        "relevant_count": len(relevant_by_key),
        "matched_count": len(matched_keys),
        "scored": True,
    }


async def run_eval(
    *,
    golden_path: Path,
    results_dir: Path,
    top_k: int,
    limit: int | None,
    dry_run: bool,
    dedupe_section: bool,
) -> Path | None:
    data = json.loads(golden_path.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    if limit is not None:
        cases = cases[:limit]
    if not cases:
        print("No cases in golden file")
        sys.exit(1)

    qdrant = QdrantClient(url=QDRANT_URL)
    names = {c.name for c in qdrant.get_collections().collections}
    if QDRANT_COLLECTION not in names:
        print(f"Collection {QDRANT_COLLECTION!r} not found at {QDRANT_URL}")
        sys.exit(1)

    inventory = load_inventory(qdrant, QDRANT_COLLECTION)
    print(
        f"Eval: {len(cases)} case(s) | k={top_k} | "
        f"{OLLAMA_EMBED_MODEL} → {QDRANT_COLLECTION} | "
        f"dedupe_section={dedupe_section}"
    )

    per_case: list[dict] = []
    skipped = 0
    async with httpx.AsyncClient(timeout=120.0) as http:
        for i, case in enumerate(cases, 1):
            case_id = case.get("id") or f"case_{i}"
            query = (case.get("query") or "").strip()
            search_query = (case.get("search_query") or query).strip()
            city = (case.get("city") or "").strip() or None
            relevant_raw = case.get("relevant") or []
            reachable, unreachable = filter_reachable(
                relevant_raw, inventory, allow_synonyms=True
            )

            print(f"[{i}/{len(cases)}] {case_id}")
            if unreachable:
                for item in unreachable:
                    sim = ", ".join(item.get("similar") or []) or "—"
                    print(f"  skip unreachable {item['section']!r} (similar: {sim})")
            if not reachable:
                skipped += 1
                print("  SKIP case — no reachable relevant in Qdrant")
                per_case.append(
                    {
                        "id": case_id,
                        "query": query,
                        "search_query": search_query,
                        "city": city,
                        "skipped": True,
                        "unreachable": unreachable,
                    }
                )
                continue

            if dry_run:
                per_case.append(
                    {
                        "id": case_id,
                        "query": query,
                        "search_query": search_query,
                        "city": city,
                        "reachable": reachable,
                        "unreachable": unreachable,
                        "dry_run": True,
                    }
                )
                continue

            vector = await embed(http, search_query)
            retrieved = search_qdrant(
                qdrant,
                vector,
                top_k=top_k,
                city=city,
                dedupe_section=dedupe_section,
            )
            metrics = score_case(reachable, retrieved)
            print(
                f"  hit={metrics['hit']:.0f} "
                f"recall={metrics['recall']:.3f} "
                f"rr={metrics['rr']:.3f} "
                f"rank={metrics['first_rank']}"
            )
            per_case.append(
                {
                    "id": case_id,
                    "query": query,
                    "search_query": search_query,
                    "city": city,
                    "reachable": reachable,
                    "unreachable": unreachable,
                    "metrics": metrics,
                    "retrieved": retrieved,
                }
            )

    if dry_run:
        print("Dry run only — no results file written")
        return None

    scored = [c for c in per_case if c.get("metrics", {}).get("scored")]
    n = len(scored)
    if n == 0:
        print("No scored cases")
        sys.exit(1)

    hit_at_k = sum(c["metrics"]["hit"] for c in scored) / n
    recall_at_k = sum(c["metrics"]["recall"] for c in scored) / n
    mrr = sum(c["metrics"]["rr"] for c in scored) / n

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%SZ")
    model_slug = re.sub(r"[^\w.-]+", "-", OLLAMA_EMBED_MODEL)
    out_path = results_dir / f"{stamp}_{model_slug}_k{top_k}.json"
    results_dir.mkdir(parents=True, exist_ok=True)

    try:
        golden_rel = str(golden_path.relative_to(ROOT))
    except ValueError:
        golden_rel = str(golden_path)

    payload = {
        "config": {
            "embedding": OLLAMA_EMBED_MODEL,
            "top_k": top_k,
            "collection": QDRANT_COLLECTION,
            "qdrant_url": QDRANT_URL,
            "golden": golden_rel,
            "cases": len(cases),
            "scored_cases": n,
            "skipped_unreachable": skipped,
            "dedupe_section": dedupe_section,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "metrics": {
            f"hit_at_{top_k}": round(hit_at_k, 4),
            f"recall_at_{top_k}": round(recall_at_k, 4),
            "mrr": round(mrr, 4),
        },
        "results": per_case,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("\nSummary")
    print(f"  Scored:    {n}/{len(cases)} (skipped unreachable: {skipped})")
    print(f"  Hit@{top_k}:    {hit_at_k:.4f}")
    print(f"  Recall@{top_k}: {recall_at_k:.4f}")
    print(f"  MRR:       {mrr:.4f}")
    print(f"  Wrote {out_path.relative_to(ROOT)}")
    return out_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Hit@k / Recall@k / MRR on travel_chunks"
    )
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--k", type=int, default=5, help="Top-k (default: 5)")
    parser.add_argument("--limit", type=int, help="Max golden cases")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--dedupe-section",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Dedupe retrieved chunks by source_url+section (default: true)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    import asyncio

    args = parse_args()
    asyncio.run(
        run_eval(
            golden_path=args.golden,
            results_dir=args.results_dir,
            top_k=args.k,
            limit=args.limit,
            dry_run=args.dry_run,
            dedupe_section=args.dedupe_section,
        )
    )
