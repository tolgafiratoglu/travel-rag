"""Gemini content gate for Wikipedia guide chunks (conservative TR tourism corpus).

proceed is derived as not (illegal or sensitive). String booleans are rejected.
When uncertain, the model must set illegal or sensitive to true (fail closed).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

GATE_PROMPT = """You are a compliance filter for a Turkey travel RAG index.
Decide whether this Wikipedia chunk should be stored and later shown to users.

The goal is NOT to preserve every valid encyclopedic fact.
The goal is to build a deliberately conservative tourism corpus.

If information is unnecessary for answering normal tourism questions and
introduces political, legal, reputational, extremist, criminal,
or similar controversy, reject it.

Answer these questions (JSON booleans):
1. illegal — Does the chunk clearly describe or encourage activity that is illegal under Turkish law?
2. sensitive — Would storing/serving this likely cause serious legal/compliance trouble in Turkey,
   OR does it contain personal accusations against named individuals,
   OR brand/reputation-damaging claims,
   OR content likely to spark social/political controversy,
   OR extremist/criminal/reputational risk beyond ordinary tourism needs?

Return ONLY valid JSON (no markdown fences):
{{
  "illegal": false,
  "sensitive": false,
  "reason": "short English note"
}}

Rules:
- illegal and sensitive MUST be JSON booleans (true/false), never strings.
- Do NOT include a "proceed" field.
- If you are unsure about any criterion, set the relevant flag(s) to true.
  When in doubt, reject.
- Prefer false-positive rejects over letting borderline content through.

### Metadata
city: {city}
section: {section}
source_url: {source_url}

### Chunk
{text}
"""

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = _JSON_RE.search(raw)
        if not m:
            raise
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object, got {type(data).__name__}")
    return data


def _require_bool(data: dict, key: str) -> bool:
    value = data.get(key)
    if type(value) is not bool:
        raise ValueError(f"{key} must be boolean, got {value!r}")
    return value


def normalize_decision(data: dict) -> dict:
    """Strict bools; proceed derived (never trust model proceed / string bools)."""
    illegal = _require_bool(data, "illegal")
    sensitive = _require_bool(data, "sensitive")
    proceed = not (illegal or sensitive)
    reason = data.get("reason")
    if reason is not None and not isinstance(reason, str):
        reason = str(reason)
    return {
        "illegal": illegal,
        "sensitive": sensitive,
        "proceed": proceed,
        "reason": (reason or "").strip()[:500],
    }


async def gate_chunk(chunk: dict) -> dict:
    """Call Gemini; return decision with proceed bool. Errors → proceed=false."""
    from app.services import llm

    prompt = GATE_PROMPT.format(
        city=chunk.get("city_name") or "",
        section=chunk.get("section") or chunk.get("section_path") or "",
        source_url=chunk.get("source_url") or "",
        text=(chunk.get("text") or "")[:6000],
    )
    try:
        raw = await llm.generate(prompt)
        data = _extract_json(raw)
        return normalize_decision(data)
    except Exception as exc:
        return {
            "illegal": False,
            "sensitive": True,
            "proceed": False,
            "reason": f"gate_error_fail_closed: {exc}",
        }


def append_rejected(path: Path, chunk: dict, decision: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "city_name": chunk.get("city_name"),
        "section": chunk.get("section") or chunk.get("section_path"),
        "source_url": chunk.get("source_url"),
        "decision": decision,
        "text_preview": (chunk.get("text") or "")[:400],
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
