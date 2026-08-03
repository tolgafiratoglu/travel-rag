"""Sanitize chat history and decide whether to attach it under the question."""

from __future__ import annotations

import re
from typing import Any

from app.config import settings

# Follow-up / deictic cues — history helps resolve city/place references.
_FOLLOWUP_RE = re.compile(
    r"(?i)"
    r"(?:\b(?:"
    r"peki|daha|orası|orada|oradaki|oraya|aynı|önceki|yukarıdaki|yukarıda|"
    r"bunun|bunlar|şunu|şunlar|onu|onlar|hangisi|diğeri|öbürü|"
    r"what about|how about|that one|those|there|earlier|previous|instead|"
    r"another|same|else|cheaper|longer|shorter"
    r")\b)"
    r"|(?:^(?:ve|ama|fakat|but|and|also|too)\b)"
)

_DEBUG_SPLIT_RE = re.compile(
    r"\n\n----- (?:prompt|planner response) -----\n",
    re.IGNORECASE,
)


def strip_debug_footer(content: str) -> str:
    return _DEBUG_SPLIT_RE.split(content, maxsplit=1)[0].strip()


def sanitize_history(raw: Any) -> list[dict[str, str]]:
    """Keep last N user/assistant turns; cap per-message and total size."""
    if not isinstance(raw, list):
        return []

    max_turns = max(0, settings.max_history_turns)
    max_msg = max(1, settings.max_history_message_chars)
    max_total = max(1, settings.max_history_total_chars)

    cleaned: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in ("user", "assistant"):
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        text = strip_debug_footer(content)
        if not text:
            continue
        if len(text) > max_msg:
            text = text[:max_msg]
        cleaned.append({"role": role, "content": text})

    if max_turns:
        cleaned = cleaned[-max_turns:]

    total = 0
    kept_rev: list[dict[str, str]] = []
    for msg in reversed(cleaned):
        n = len(msg["content"])
        if kept_rev and total + n > max_total:
            break
        kept_rev.append(msg)
        total += n
    kept_rev.reverse()
    return kept_rev


def decide_include_history(question: str, history: list[dict[str, str]]) -> bool:
    """Set once in intake; downstream prompts only read this flag."""
    if not history:
        return False
    q = (question or "").strip()
    if not q:
        return False
    return bool(_FOLLOWUP_RE.search(q))


def format_history_section(history: list[dict[str, str]]) -> str:
    if not history:
        return ""
    lines = [
        "",
        "### Recent conversation",
        "Use only to resolve references in the current question "
        "(e.g. city, place). Do not treat history as the main request "
        "and do not invent a new trip from history alone.",
    ]
    for msg in history:
        lines.append(f"{msg['role']}: {msg['content']}")
    return "\n".join(lines) + "\n"


def question_block(
    question: str,
    history: list[dict[str, str]] | None,
    *,
    include_history: bool,
) -> str:
    """Current question, with history appended underneath when include_history."""
    q = (question or "").strip()
    if not include_history:
        return q
    hist = history or []
    if not hist:
        return q
    return q + format_history_section(hist)
