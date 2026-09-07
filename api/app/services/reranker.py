"""Cross-encoder reranker for candidate selection (BGE by default).

Providers:
  none  — never scores (selection stays retrieval-order)
  bge   — local ``BAAI/bge-reranker-*`` via FlagEmbedding (preferred)
  http  — Cohere / TEI-compatible POST {query, documents} → ranked results
  llm   — chat-LLM batch scores (debug only; not the production path)

Modes (``settings.rerank_mode``):
  off          — never call the provider
  always       — call whenever there is more than one candidate
  conditional  — call only when the pool exceeds the context budget
                (the case where elimination actually matters)

Status values returned to the graph (distinct on purpose):
  skipped   — mode off, or pool fits the budget under conditional
  applied   — scores written onto candidates as ``rerank_score``
  fallback  — provider raised; selection continues without scores
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

RerankStatus = Literal["skipped", "applied", "fallback"]

_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")
_bge_reranker = None


@dataclass(frozen=True)
class RerankOutcome:
    status: RerankStatus
    reason: str
    scored: int = 0


def estimate_pool_tokens(chunks: list[dict]) -> int:
    total = 0
    for chunk in chunks:
        text = chunk.get("body_text") or chunk.get("text") or ""
        total += max(1, len(text) // 4)
    return total


def needs_elimination(candidates: list[dict]) -> bool:
    """True when the pool cannot all fit into the synthesis budget."""
    if len(candidates) > settings.max_selected_chunks:
        return True
    return estimate_pool_tokens(candidates) > settings.max_context_tokens


def should_rerank(candidates: list[dict], *, mode: str | None = None) -> tuple[bool, str]:
    """Decide whether to call the provider. Returns (call?, reason)."""
    mode = (mode or settings.rerank_mode or "off").strip().lower()
    if mode in ("off", "false", "0", "none"):
        return False, "mode_off"
    if len(candidates) <= 1:
        return False, "too_few_candidates"
    if mode == "always":
        return True, "mode_always"
    if mode == "conditional":
        if needs_elimination(candidates):
            return True, "exceeds_budget"
        return False, "fits_budget"
    return False, f"unknown_mode:{mode}"


def _passage_text(chunk: dict, *, max_chars: int) -> str:
    text = (chunk.get("body_text") or chunk.get("text") or "").strip()
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def _get_bge_reranker():
    """Lazy-load FlagEmbedding FlagReranker (heavy; once per process)."""
    global _bge_reranker
    if _bge_reranker is not None:
        return _bge_reranker
    try:
        from FlagEmbedding import FlagReranker
    except ImportError as exc:
        raise RuntimeError(
            "FlagEmbedding is required for RERANK_PROVIDER=bge "
            "(pip install FlagEmbedding)"
        ) from exc

    model = (settings.rerank_model or "BAAI/bge-reranker-v2-m3").strip()
    logger.info("Loading BGE reranker %s …", model)
    _bge_reranker = FlagReranker(model, use_fp16=False)
    return _bge_reranker


def _bge_compute_scores(query: str, passages: list[str]) -> list[float]:
    reranker = _get_bge_reranker()
    pairs = [[query, passage] for passage in passages]
    raw = reranker.compute_score(pairs, normalize=True)
    if isinstance(raw, (int, float)):
        return [float(raw)]
    return [float(s) for s in raw]


async def _score_bge(query: str, passages: list[str]) -> list[float]:
    """Score (query, passage) pairs with a BGE cross-encoder on a worker thread."""
    return await asyncio.to_thread(_bge_compute_scores, query, passages)


async def _score_http(query: str, passages: list[str]) -> list[float]:
    url = (settings.rerank_url or "").strip()
    if not url:
        raise RuntimeError("rerank_url is empty")

    payload: dict[str, Any] = {
        "query": query,
        "documents": passages,
        "top_n": len(passages),
    }
    if settings.rerank_model:
        payload["model"] = settings.rerank_model

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.rerank_api_key:
        headers["Authorization"] = f"Bearer {settings.rerank_api_key}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # Cohere: {"results":[{"index":i,"relevance_score":s}, ...]}
    # TEI:    [{"index":i,"score":s}, ...]
    results = data.get("results") if isinstance(data, dict) else data
    if not isinstance(results, list):
        raise RuntimeError(f"unexpected rerank response shape: {type(data)}")

    scores = [0.0] * len(passages)
    for item in results:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        score = item.get("relevance_score", item.get("score"))
        if idx is None or score is None:
            continue
        i = int(idx)
        if 0 <= i < len(scores):
            scores[i] = float(score)
    return scores


async def _score_llm(query: str, passages: list[str]) -> list[float]:
    """Debug-only: ask the chat LLM for scores. Prefer ``bge`` in production."""
    from app.services import llm

    lines = [f"[{i}] {text}" for i, text in enumerate(passages)]
    prompt = (
        "You are a relevance judge for a travel retrieval system.\n"
        "Score how useful each passage is for answering the query.\n"
        "Return ONLY a JSON array of objects: "
        '[{"id": 0, "score": 0.0}, ...] with scores in [0, 1].\n'
        "Do not invent passages. Score every id exactly once.\n\n"
        f"### Query\n{query}\n\n"
        f"### Passages\n" + "\n\n".join(lines) + "\n\n"
        "### Scores (JSON only)\n"
    )
    raw = await llm.generate(prompt)
    match = _JSON_ARRAY_RE.search(raw or "")
    if not match:
        raise RuntimeError(f"LLM rerank returned no JSON array: {raw[:200]!r}")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, list):
        raise RuntimeError("LLM rerank JSON was not a list")

    scores = [0.0] * len(passages)
    for item in parsed:
        if not isinstance(item, dict):
            continue
        idx = item.get("id", item.get("index"))
        score = item.get("score")
        if idx is None or score is None:
            continue
        i = int(idx)
        if 0 <= i < len(scores):
            scores[i] = max(0.0, min(1.0, float(score)))
    return scores


async def score_passages(query: str, passages: list[str]) -> list[float]:
    provider = (settings.rerank_provider or "none").strip().lower()
    if provider in ("none", "off", ""):
        raise RuntimeError("rerank_provider is none")
    if provider == "bge":
        return await _score_bge(query, passages)
    if provider == "http":
        return await _score_http(query, passages)
    if provider == "llm":
        return await _score_llm(query, passages)
    raise RuntimeError(f"unknown rerank_provider: {provider}")


async def apply_rerank(
    query: str,
    candidates: list[dict],
    *,
    mode: str | None = None,
) -> RerankOutcome:
    """Score candidates in place (``rerank_score``) according to mode.

    Never raises — failures become ``fallback`` so selection can continue.
    """
    call, reason = should_rerank(candidates, mode=mode)
    if not call:
        return RerankOutcome(status="skipped", reason=reason)

    provider = (settings.rerank_provider or "none").strip().lower()
    if provider in ("none", "off", ""):
        return RerankOutcome(status="skipped", reason="provider_none")

    max_chars = settings.rerank_max_chars
    passages = [_passage_text(c, max_chars=max_chars) for c in candidates]
    try:
        scores = await score_passages(query, passages)
        if len(scores) != len(candidates):
            raise RuntimeError(
                f"score count mismatch: {len(scores)} vs {len(candidates)}"
            )
        for chunk, score in zip(candidates, scores):
            chunk["rerank_score"] = float(score)
        logger.info(
            "Rerank applied (%s): %d candidate(s) via %s",
            reason,
            len(candidates),
            provider,
        )
        return RerankOutcome(
            status="applied", reason=reason, scored=len(candidates)
        )
    except Exception as exc:
        logger.warning("Rerank fallback (%s): %s", reason, exc)
        return RerankOutcome(status="fallback", reason=f"error:{exc}")
