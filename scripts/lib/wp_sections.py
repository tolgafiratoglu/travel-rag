"""Wikipedia HTML → section payloads for Neo4j Gemini relation extract."""

from __future__ import annotations

import re
from typing import Iterable

from bs4 import BeautifulSoup

# Map Wikipedia heading text → synthetic topics (replaces WV get_in / go_next / see).
PASS_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "airport": (
        "airport",
        "airports",
        "air transport",
        "aviation",
    ),
    "connected": (
        "transport",
        "transportation",
        "infrastructure",
        "rail",
        "railway",
        "road",
        "roads",
        "highway",
        "highways",
        "bus",
        "ferry",
        "ferries",
        "metro",
        "transit",
        "public transport",
        "get around",
        "connectivity",
    ),
    "day_trip": (
        "tourism",
        "tourist",
        "attraction",
        "attractions",
        "landmark",
        "landmarks",
        "sightseeing",
        "culture",
        "museum",
        "museums",
        "historic",
        "historical",
        "heritage",
        "places of interest",
        "recreation",
        "parks",
    ),
}


def _normalize(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().lower()


def _heading_matches(title: str, keywords: Iterable[str]) -> bool:
    norm = _normalize(title)
    return any(k in norm for k in keywords)


def split_wikipedia_sections(html: str) -> list[dict]:
    """Split article HTML into {topic, section_path, body} by h2/h3 headings."""
    if not html or not html.strip():
        return []

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select("script, style, noscript, .mw-editsection, #toc, .toc"):
        tag.decompose()

    root = soup.body or soup
    sections: list[dict] = []
    current_title = "Lead"
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_parts
        text = "\n".join(p for p in current_parts if p.strip()).strip()
        current_parts = []
        if not text:
            return
        sections.append(
            {
                "topic": _normalize(current_title).replace(" ", "_")[:64] or "lead",
                "section_path": current_title,
                "body": text,
                "heading": current_title,
            }
        )

    for el in root.children:
        name = getattr(el, "name", None)
        if name and re.match(r"^h[1-6]$", name):
            flush()
            current_title = el.get_text(" ", strip=True) or "Section"
            continue
        if name is None:
            continue
        text = el.get_text(" ", strip=True) if hasattr(el, "get_text") else ""
        if text:
            current_parts.append(text)
    flush()
    return sections


def sections_for_pass(
    sections: list[dict],
    pass_key: str,
    *,
    max_section_chars: int = 1800,
    max_total_chars: int = 6000,
) -> list[dict]:
    """Filter sections by pass keyword map; truncate for Gemini."""
    keywords = PASS_TOPIC_KEYWORDS.get(pass_key, ())
    if not keywords:
        filtered = sections
    else:
        filtered = [
            s
            for s in sections
            if _heading_matches(s.get("heading") or s.get("section_path") or "", keywords)
        ]
        # Fallback: if nothing matched, feed a short Lead + first transport-ish chunks
        if not filtered and pass_key in ("airport", "connected"):
            filtered = [
                s
                for s in sections
                if _normalize(s.get("heading") or "") in ("lead", "")
                or "transport" in _normalize(s.get("heading") or "")
            ][:3]
        if not filtered and pass_key == "day_trip":
            filtered = sections[:4]

    out: list[dict] = []
    total = 0
    for section in filtered:
        plain = (section.get("body") or "").strip()
        if not plain:
            continue
        if len(plain) > max_section_chars:
            plain = plain[:max_section_chars] + "..."
        item = {
            "topic": section.get("topic") or pass_key,
            "section_path": section.get("section_path") or section.get("heading") or "",
            "text": plain,
        }
        total += len(plain)
        if total > max_total_chars:
            break
        out.append(item)
    return out
