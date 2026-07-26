"""Shared Wikivoyage wikitext parsing helpers.

Section policy (objective tourism, lower commercial/anecdote risk):
  Allowed top-level topics: see, do, get_in, districts, go_next
  understand: only history / climate subsections
  get_around: public-transport / walking / cycling style subsections only
  Excluded: sleep, eat, drink, buy, stay_safe, stay_healthy, talk, work, …
  Lead/intro is skipped (often promotional or subjective).
"""

from __future__ import annotations

import re
import unicodedata

# Top-level (== Section ==) topics kept in full (all subsections unless noted).
ALLOWED_TOPICS = frozenset(
    {
        "see",
        "do",
        "get_in",
        "districts",
        "go_next",
    }
)

# Under == Understand ==, only these subsection slugs (or slug containing them).
ALLOWED_UNDERSTAND_KEYS = frozenset(
    {
        "history",
        "climate",
    }
)

# Under == Get around ==, subsection must match one of these keys (substring).
ALLOWED_GET_AROUND_KEYS = frozenset(
    {
        "public_transport",
        "metro",
        "rer",
        "bus",
        "tram",
        "train",
        "rail",
        "ferry",
        "on_foot",
        "by_foot",
        "foot",
        "walk",
        "pedestrian",
        "bicycle",
        "bike",
        "cycling",
        "velib",
        "fares",
        "ticket",
        "navigo",
    }
)

# Explicit reject under get_around even if another key matches.
DENIED_GET_AROUND_KEYS = frozenset(
    {
        "taxi",
        "car",
        "motorcycle",
        "scooter",
        "chauffeur",
        "driving",
        "rental",
        "rent",
    }
)

SECTION_RE = re.compile(r"^(={2,})\s*(.+?)\s*\1\s*$", re.MULTILINE)
WIKILINK_RE = re.compile(r"\[\[(?:[^|\]#]+#)?(?:[^|\]]+\|)?([^\]]+)\]\]")
BOLD_RE = re.compile(r"'''(.+?)'''")
ITALIC_RE = re.compile(r"''(.+?)''")
HTML_TAG_RE = re.compile(r"<[^>]+>")
MULTI_NL_RE = re.compile(r"\n{3,}")


def section_slug(title: str) -> str:
    """Lowercase ASCII-ish slug: accents stripped so Métro → metro."""
    normalized = unicodedata.normalize("NFKD", title.strip().lower())
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"[^\w]+", "_", ascii_text).strip("_")


def _slug_has_key(slug: str, keys: frozenset[str]) -> bool:
    if slug in keys:
        return True
    return any(key in slug for key in keys)


def section_allowed(topic: str, subsection: str | None) -> bool:
    """Return True if this section should be ingested / sent to Gemini."""
    if topic == "intro":
        return False

    if topic in ALLOWED_TOPICS:
        return True

    if topic == "understand":
        if not subsection:
            return False
        return _slug_has_key(subsection, ALLOWED_UNDERSTAND_KEYS)

    if topic == "get_around":
        if not subsection:
            return False
        if _slug_has_key(subsection, DENIED_GET_AROUND_KEYS):
            return False
        return _slug_has_key(subsection, ALLOWED_GET_AROUND_KEYS)

    return False


def split_sections(wikitext: str) -> list[dict]:
    matches = list(SECTION_RE.finditer(wikitext))
    sections: list[dict] = []
    current_topic: str | None = None

    if matches:
        lead = wikitext[: matches[0].start()].strip()
        if lead and section_allowed("intro", None):
            sections.append(
                {
                    "topic": "intro",
                    "section_path": "intro",
                    "subsection": None,
                    "body": lead,
                }
            )

    for i, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        slug = section_slug(title)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(wikitext)
        body = wikitext[start:end].strip()

        if level == 2:
            current_topic = slug
            topic = slug
            section_path = slug
            subsection = None
        else:
            topic = current_topic or slug
            subsection = slug
            section_path = f"{topic}/{slug}" if current_topic else slug

        if not body:
            continue

        if not section_allowed(topic, subsection):
            continue

        sections.append(
            {
                "topic": topic,
                "section_path": section_path,
                "subsection": subsection,
                "body": body,
            }
        )

    return sections


def find_template_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    i = 0
    while True:
        start = text.find("{{", i)
        if start == -1:
            break
        depth = 0
        pos = start
        found = False
        while pos < len(text) - 1:
            if text[pos : pos + 2] == "{{":
                depth += 1
                pos += 2
                continue
            if text[pos : pos + 2] == "}}":
                depth -= 1
                pos += 2
                if depth == 0:
                    spans.append((start, pos, text[start:pos]))
                    i = pos
                    found = True
                    break
            pos += 1
        if not found:
            i = start + 2
    return spans


def strip_remaining_templates(text: str) -> str:
    for start, end, _ in sorted(find_template_spans(text), key=lambda x: x[0], reverse=True):
        text = text[:start] + " " + text[end:]
    text = WIKILINK_RE.sub(r"\1", text)
    text = BOLD_RE.sub(r"\1", text)
    text = ITALIC_RE.sub(r"\1", text)
    text = HTML_TAG_RE.sub(" ", text)
    text = MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def sections_for_gemini(
    sections: list[dict],
    *,
    max_section_chars: int = 2500,
    max_total_chars: int = 14000,
) -> list[dict]:
    """Plain-text section payloads for Gemini graph extraction."""
    out: list[dict] = []
    total = 0
    for section in sections:
        plain = strip_remaining_templates(section["body"])
        if not plain:
            continue
        if len(plain) > max_section_chars:
            plain = plain[:max_section_chars] + "..."
        item = {
            "topic": section["topic"],
            "section_path": section["section_path"],
            "text": plain,
        }
        total += len(plain)
        if total > max_total_chars:
            break
        out.append(item)
    return out
