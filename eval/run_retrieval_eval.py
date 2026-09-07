#!/usr/bin/env python3
"""Retrieval eval for travel_chunks: Hit@k, Recall@k, Precision@k, MRR,
city_coverage, section_coverage.

Runs the API's own retrieval and selection code (``app.services.vector_store``
and ``app.graph.nodes``) instead of a copy, so a measured number always
describes what the application actually does.

Modes (``--mode``):
    flat           flat top-k, one chunk per section  (pre-grouping baseline)
    flat-nodedupe  flat top-k, section duplicates kept
    grouped        section-grouped candidate pool     (search_candidates)
    pipeline       grouped candidates + select_node    (what synthesis sees;
                   this is the reranker-free baseline)

Multi-query / comparison goldens use ``search_queries`` + ``expected_cities``.
``city_coverage`` and ``section_coverage`` measure the spread that Hit/MRR
miss on single-section cases.

Unreachable golden sections (not in Qdrant) are excluded from scoring.
Run ``eval/validate_golden.py`` first to inspect mismatches.

Usage:
    QDRANT_URL=http://localhost:6333 OLLAMA_BASE_URL=http://localhost:11434 \\
      .venv/bin/python eval/run_retrieval_eval.py --mode pipeline --k 10

    for m in flat flat-nodedupe grouped pipeline; do
      .venv/bin/python eval/run_retrieval_eval.py --mode $m --k 10
    done
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parents[0]
sys.path.insert(0, str(EVAL_DIR))
# The app package is the source of truth for retrieval + selection.
sys.path.insert(0, str(ROOT / "api"))

try:
    from qdrant_client import QdrantClient
except ImportError:
    print("Missing: pip install httpx qdrant-client")
    sys.exit(1)

from app.config import settings  # noqa: E402
from app.graph.nodes import _collect_query_chunks, select_candidates  # noqa: E402
from app.services import vector_store  # noqa: E402
from common import (  # noqa: E402
    filter_reachable,
    load_inventory,
    norm_url,
    relevant_key,
    section_equiv,
    section_leaf,
)

DEFAULT_GOLDEN = EVAL_DIR / "golden" / "retrieval.json"
DEFAULT_RESULTS_DIR = EVAL_DIR / "results"

MODES = ("flat", "flat-nodedupe", "grouped", "pipeline")
RERANK_MODES = ("off", "always", "conditional")

QDRANT_URL = os.getenv("QDRANT_URL", settings.qdrant_url)
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", settings.qdrant_api_key).strip()
QDRANT_COLLECTION = settings.qdrant_collection


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


def case_queries(case: dict) -> list[dict]:
    """Normalise a golden case to a list of {q, city} planner queries."""
    raw = case.get("search_queries")
    if isinstance(raw, list) and raw:
        out: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            q = (item.get("q") or item.get("query") or "").strip()
            if not q:
                continue
            city = (item.get("city") or "").strip() or None
            out.append({"q": q, "city": city})
        if out:
            return out
    q = (case.get("search_query") or case.get("query") or "").strip()
    city = (case.get("city") or "").strip() or None
    return [{"q": q, "city": city}] if q else []


async def retrieve(
    mode: str,
    queries: list[dict],
    *,
    top_k: int,
    question: str = "",
    rerank_mode: str = "off",
) -> tuple[list[dict], dict]:
    """Run one eval mode through the application's own code paths.

    Returns (chunks, meta) where meta may include rerank_status/reason.
    Multi-query cases run every planner query. Flat modes merge by score
    (pre-selection behaviour); pipeline uses ``select_candidates``.
    """
    meta: dict = {}
    if not queries:
        return [], meta

    if mode == "pipeline":
        candidates = await _collect_query_chunks(queries)
        selected, outcome = await select_candidates(
            candidates,
            question=question or (queries[0]["q"] if queries else ""),
            rerank_mode=rerank_mode,
        )
        meta = {
            "rerank_status": outcome.status,
            "rerank_reason": outcome.reason,
            "rerank_scored": outcome.scored,
            "candidate_count": len(candidates),
        }
        # Full selected set is what synthesis sees; Hit@k is scored on the head.
        return selected, meta

    if mode == "grouped":
        merged: list[dict] = []
        seen: set[str] = set()
        for item in queries:
            hits = await vector_store.search_candidates(
                item["q"], city=item.get("city")
            )
            for chunk in hits:
                cid = (chunk.get("chunk_id") or "").strip() or str(id(chunk))
                if cid in seen:
                    continue
                seen.add(cid)
                merged.append(chunk)
        merged.sort(key=lambda c: float(c.get("score") or 0), reverse=True)
        return merged[:top_k], meta

    dedupe = mode == "flat"
    if mode not in ("flat", "flat-nodedupe"):
        raise ValueError(f"unknown mode: {mode}")

    merged = []
    seen = set()
    for item in queries:
        hits = await vector_store.search(
            item["q"],
            top_k=top_k,
            city=item.get("city"),
            dedupe_section=dedupe,
        )
        for chunk in hits:
            cid = (chunk.get("chunk_id") or "").strip() or str(id(chunk))
            if cid in seen:
                continue
            seen.add(cid)
            merged.append(chunk)
    merged.sort(key=lambda c: float(c.get("score") or 0), reverse=True)
    return merged[:top_k], meta


def project(chunk: dict) -> dict:
    """Keep the result file readable — drop chunk bodies."""
    return {
        "score": chunk.get("score"),
        "source_url": chunk.get("source_url") or "",
        "section": chunk.get("section") or "",
        "section_group": chunk.get("section_group") or "",
        "section_id": chunk.get("section_id") or "",
        "section_chunk_seq": chunk.get("section_chunk_seq"),
        "city_name": chunk.get("city_name") or "",
        "title": chunk.get("title") or "",
        "query_city": chunk.get("query_city") or "",
    }


def _norm_city(value: str | None) -> str:
    return (value or "").strip().casefold()


def coverage_metrics(
    retrieved: list[dict],
    relevant: list[dict],
    *,
    expected_cities: list[str] | None,
) -> dict:
    """Metrics that reward spread — the thing Hit/MRR miss on single-section goldens."""
    retrieved_sections = {
        relevant_key(
            {
                "source_url": c.get("source_url"),
                "section": c.get("section") or c.get("section_path"),
            }
        )
        for c in retrieved
        if (c.get("source_url") or "").strip()
    }
    retrieved_sections.discard(("", ""))

    relevant_keys = {relevant_key(item) for item in relevant}
    relevant_keys = {k for k in relevant_keys if k[0] and k[1]}
    matched_sections = retrieved_sections & relevant_keys

    # Cities from the retrieved payloads (prefer city_name, fall back to query_city).
    retrieved_cities = {
        _norm_city(c.get("city_name") or c.get("query_city"))
        for c in retrieved
    }
    retrieved_cities.discard("")

    expected = [_norm_city(c) for c in (expected_cities or []) if _norm_city(c)]
    if expected:
        hit_cities = sum(1 for c in expected if c in retrieved_cities)
        city_coverage = hit_cities / len(expected)
    else:
        city_coverage = None

    # Distinct relevant sections present / reachable relevant sections.
    section_coverage = (
        len(matched_sections) / len(relevant_keys) if relevant_keys else None
    )

    return {
        "city_coverage": city_coverage,
        "section_coverage": section_coverage,
        "distinct_sections": len(retrieved_sections),
        "distinct_cities": len(retrieved_cities),
        "matched_sections": len(matched_sections),
        "expected_city_count": len(expected) if expected else None,
    }


def score_case(
    relevant: list[dict],
    retrieved: list[dict],
    *,
    top_k: int,
    expected_cities: list[str] | None = None,
) -> dict:
    relevant_by_key: dict[tuple[str, str], dict] = {}
    for item in relevant:
        key = relevant_key(item)
        if not key[0] or not key[1] or key in relevant_by_key:
            continue
        relevant_by_key[key] = {
            "source_url": (item.get("source_url") or "").strip(),
            "section": (item.get("section") or "").strip(),
        }

    # Coverage is measured on the full retrieved set (pipeline may return >k).
    coverage = coverage_metrics(
        retrieved, relevant, expected_cities=expected_cities
    )

    if not relevant_by_key:
        return {
            "hit": 0.0,
            "recall": 0.0,
            "precision": 0.0,
            "rr": 0.0,
            "first_rank": None,
            "matched": [],
            "relevant_count": 0,
            "matched_count": 0,
            "relevant_in_topk": 0,
            "returned": len(retrieved),
            "scored": False,
            **coverage,
        }

    matched_keys: set[tuple[str, str]] = set()
    first_rank: int | None = None
    relevant_in_topk = 0
    head = retrieved[: max(top_k, 1)]
    for rank, chunk in enumerate(head, start=1):
        hits = _hit_matches(chunk, relevant)
        if not hits:
            continue
        relevant_in_topk += 1
        if first_rank is None:
            first_rank = rank
        matched_keys.update(hits)

    hit = 1.0 if matched_keys else 0.0
    recall = len(matched_keys) / len(relevant_by_key)
    k = max(top_k, 1)
    precision = relevant_in_topk / k
    rr = (1.0 / first_rank) if first_rank else 0.0
    return {
        "hit": hit,
        "recall": recall,
        "precision": precision,
        "rr": rr,
        "first_rank": first_rank,
        "matched": [relevant_by_key[k_] for k_ in sorted(matched_keys)],
        "relevant_count": len(relevant_by_key),
        "matched_count": len(matched_keys),
        "relevant_in_topk": relevant_in_topk,
        "returned": len(retrieved),
        "scored": True,
        **coverage,
    }


async def run_eval(
    *,
    golden_path: Path,
    results_dir: Path,
    top_k: int,
    limit: int | None,
    dry_run: bool,
    mode: str,
    rerank_mode: str = "off",
) -> Path | None:
    data = json.loads(golden_path.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    if limit is not None:
        cases = cases[:limit]
    if not cases:
        print("No cases in golden file")
        sys.exit(1)

    qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
    names = {c.name for c in qdrant.get_collections().collections}
    if QDRANT_COLLECTION not in names:
        print(f"Collection {QDRANT_COLLECTION!r} not found at {QDRANT_URL}")
        sys.exit(1)

    inventory = load_inventory(qdrant, QDRANT_COLLECTION)
    print(
        f"Eval: {len(cases)} case(s) | k={top_k} | mode={mode} | "
        f"rerank={rerank_mode} | "
        f"{settings.ollama_embed_model} → {QDRANT_COLLECTION}"
    )
    if mode in ("grouped", "pipeline"):
        print(
            f"  candidate_sections={settings.candidate_sections} "
            f"candidates_per_section={settings.candidates_per_section}"
        )
    if mode == "pipeline":
        print(
            f"  max_selected_chunks={settings.max_selected_chunks} "
            f"max_context_tokens={settings.max_context_tokens} "
            f"rerank_provider={settings.rerank_provider}"
        )

    per_case: list[dict] = []
    skipped = 0
    for i, case in enumerate(cases, 1):
        case_id = case.get("id") or f"case_{i}"
        query = (case.get("query") or "").strip()
        queries = case_queries(case)
        search_query = (case.get("search_query") or query).strip()
        city = (case.get("city") or "").strip() or None
        expected_cities = [
            c for c in (case.get("expected_cities") or []) if isinstance(c, str) and c.strip()
        ] or None
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
                    "kind": case.get("kind") or "single",
                    "query": query,
                    "search_query": search_query,
                    "search_queries": queries,
                    "city": city,
                    "expected_cities": expected_cities,
                    "skipped": True,
                    "unreachable": unreachable,
                }
            )
            continue

        if dry_run:
            per_case.append(
                {
                    "id": case_id,
                    "kind": case.get("kind") or "single",
                    "query": query,
                    "search_query": search_query,
                    "search_queries": queries,
                    "city": city,
                    "expected_cities": expected_cities,
                    "reachable": reachable,
                    "unreachable": unreachable,
                    "dry_run": True,
                }
            )
            continue

        retrieved, retrieve_meta = await retrieve(
            mode,
            queries,
            top_k=top_k,
            question=query,
            rerank_mode=rerank_mode,
        )
        metrics = score_case(
            reachable,
            retrieved,
            top_k=top_k,
            expected_cities=expected_cities,
        )
        cov = ""
        if metrics.get("city_coverage") is not None:
            cov = f" city_cov={metrics['city_coverage']:.2f}"
        if metrics.get("section_coverage") is not None:
            cov += f" sec_cov={metrics['section_coverage']:.2f}"
        rerank_note = ""
        if retrieve_meta.get("rerank_status"):
            rerank_note = (
                f" rerank={retrieve_meta['rerank_status']}"
                f"({retrieve_meta.get('rerank_reason')})"
            )
        print(
            f"  hit={metrics['hit']:.0f} "
            f"recall={metrics['recall']:.3f} "
            f"prec={metrics['precision']:.3f} "
            f"rr={metrics['rr']:.3f} "
            f"rank={metrics['first_rank']} "
            f"returned={len(retrieved)}"
            f"{cov}{rerank_note}"
        )
        per_case.append(
            {
                "id": case_id,
                "kind": case.get("kind") or "single",
                "query": query,
                "search_query": search_query,
                "search_queries": queries,
                "city": city,
                "expected_cities": expected_cities,
                "reachable": reachable,
                "unreachable": unreachable,
                "metrics": metrics,
                "rerank": retrieve_meta or None,
                "retrieved": [project(c) for c in retrieved],
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

    def avg(key: str, rows: list[dict] | None = None) -> float | None:
        rows = rows if rows is not None else scored
        vals = [
            c["metrics"][key]
            for c in rows
            if c["metrics"].get(key) is not None
        ]
        return (sum(vals) / len(vals)) if vals else None

    hit_at_k = avg("hit") or 0.0
    recall_at_k = avg("recall") or 0.0
    precision_at_k = avg("precision") or 0.0
    mrr = avg("rr") or 0.0
    city_cov = avg("city_coverage")
    section_cov = avg("section_coverage")

    comparison = [c for c in scored if c.get("kind") == "comparison"]
    single = [c for c in scored if c.get("kind") in (None, "single")]

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%SZ")
    model_slug = re.sub(r"[^\w.-]+", "-", settings.ollama_embed_model)
    rerank_slug = rerank_mode if mode == "pipeline" else "na"
    out_path = (
        results_dir / f"{stamp}_{model_slug}_{mode}_rerank-{rerank_slug}_k{top_k}.json"
    )
    results_dir.mkdir(parents=True, exist_ok=True)

    try:
        golden_rel = str(golden_path.relative_to(ROOT))
    except ValueError:
        golden_rel = str(golden_path)

    # Rerank status tallies (pipeline only).
    rerank_counts: dict[str, int] = {}
    for c in per_case:
        status = (c.get("rerank") or {}).get("rerank_status")
        if status:
            rerank_counts[status] = rerank_counts.get(status, 0) + 1

    metrics_block: dict = {
        f"hit_at_{top_k}": round(hit_at_k, 4),
        f"recall_at_{top_k}": round(recall_at_k, 4),
        f"precision_at_{top_k}": round(precision_at_k, 4),
        "mrr": round(mrr, 4),
    }
    if city_cov is not None:
        metrics_block["city_coverage"] = round(city_cov, 4)
    if section_cov is not None:
        metrics_block["section_coverage"] = round(section_cov, 4)

    slices: dict = {}
    for label, rows in (("all", scored), ("single", single), ("comparison", comparison)):
        if not rows:
            continue
        block = {
            "cases": len(rows),
            f"hit_at_{top_k}": round(avg("hit", rows) or 0.0, 4),
            f"recall_at_{top_k}": round(avg("recall", rows) or 0.0, 4),
            f"precision_at_{top_k}": round(avg("precision", rows) or 0.0, 4),
            "mrr": round(avg("rr", rows) or 0.0, 4),
        }
        cc = avg("city_coverage", rows)
        sc = avg("section_coverage", rows)
        if cc is not None:
            block["city_coverage"] = round(cc, 4)
        if sc is not None:
            block["section_coverage"] = round(sc, 4)
        slices[label] = block

    payload = {
        "config": {
            "embedding": settings.ollama_embed_model,
            "top_k": top_k,
            "mode": mode,
            "rerank_mode": rerank_mode,
            "rerank_provider": settings.rerank_provider,
            "collection": QDRANT_COLLECTION,
            "qdrant_url": QDRANT_URL,
            "golden": golden_rel,
            "cases": len(cases),
            "scored_cases": n,
            "skipped_unreachable": skipped,
            "candidate_sections": settings.candidate_sections,
            "candidates_per_section": settings.candidates_per_section,
            "max_selected_chunks": settings.max_selected_chunks,
            "max_context_tokens": settings.max_context_tokens,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "baseline": mode == "pipeline" and rerank_mode == "off",
        },
        "metrics": metrics_block,
        "slices": slices,
        "rerank_counts": rerank_counts,
        "results": per_case,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("\nSummary")
    print(f"  Mode:      {mode} (rerank={rerank_mode})")
    print(f"  Scored:    {n}/{len(cases)} (skipped unreachable: {skipped})")
    print(f"  Hit@{top_k}:    {hit_at_k:.4f}")
    print(f"  Recall@{top_k}: {recall_at_k:.4f}")
    print(f"  Prec@{top_k}:   {precision_at_k:.4f}")
    print(f"  MRR:       {mrr:.4f}")
    if city_cov is not None:
        print(f"  CityCov:   {city_cov:.4f}")
    if section_cov is not None:
        print(f"  SecCov:    {section_cov:.4f}")
    if rerank_counts:
        print(f"  Rerank:    {rerank_counts}")
    if comparison:
        print(
            f"  Comparison slice (n={len(comparison)}): "
            f"recall={avg('recall', comparison):.4f} "
            f"city_cov={avg('city_coverage', comparison):.4f} "
            f"sec_cov={avg('section_coverage', comparison):.4f}"
        )
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
        "--mode",
        choices=MODES,
        default="pipeline",
        help="Retrieval path to measure (default: pipeline)",
    )
    parser.add_argument(
        "--rerank",
        choices=RERANK_MODES,
        default="off",
        help="Rerank mode for pipeline (default: off = step-6 baseline)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        run_eval(
            golden_path=args.golden,
            results_dir=args.results_dir,
            top_k=args.k,
            limit=args.limit,
            dry_run=args.dry_run,
            mode=args.mode,
            rerank_mode=args.rerank,
        )
    )
