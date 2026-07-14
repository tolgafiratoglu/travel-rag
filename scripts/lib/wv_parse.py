"""Shared Wikivoyage wikitext parsing helpers."""

from __future__ import annotations

import re

EXCLUDED_TOPICS = frozenset({"drink", "stay_safe", "stay_healthy"})

SECTION_RE = re.compile(r"^(={2,})\s*(.+?)\s*\1\s*$", re.MULTILINE)
WIKILINK_RE = re.compile(r"\[\[(?:[^|\]#]+#)?(?:[^|\]]+\|)?([^\]]+)\]\]")
BOLD_RE = re.compile(r"'''(.+?)'''")
ITALIC_RE = re.compile(r"''(.+?)''")
HTML_TAG_RE = re.compile(r"<[^>]+>")
MULTI_NL_RE = re.compile(r"\n{3,}")


def section_slug(title: str) -> str:
    return re.sub(r"[^\w]+", "_", title.strip().lower()).strip("_")


def split_sections(wikitext: str) -> list[dict]:
    matches = list(SECTION_RE.finditer(wikitext))
    sections: list[dict] = []
    current_topic: str | None = None

    if matches:
        lead = wikitext[: matches[0].start()].strip()
        if lead:
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
        if not body:
            continue

        if level == 2:
            current_topic = slug
            topic = slug
            section_path = slug
            subsection = None
        else:
            topic = current_topic or slug
            subsection = slug
            section_path = f"{topic}/{slug}" if current_topic else slug

        if topic in EXCLUDED_TOPICS:
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
