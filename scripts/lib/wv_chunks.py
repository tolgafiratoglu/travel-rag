"""Qdrant chunk building from Wikivoyage wikitext."""

from __future__ import annotations

import os
import re
import uuid

from lib.wv_parse import (
    find_template_spans,
    split_sections,
    strip_remaining_templates,
)

LISTING_TYPES = frozenset({"see", "do", "go", "eat", "sleep", "marker", "listing"})

MAX_SECTION_CHARS = int(os.getenv("WV_MAX_CHUNK_CHARS", "1800"))
SECTION_OVERLAP = int(os.getenv("WV_CHUNK_OVERLAP", "200"))


def parse_template_fields(inner: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in inner.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("|"):
            line = line[1:].strip()
        segments = re.split(r"\s*\|\s*", line) if "|" in line else [line]
        for segment in segments:
            segment = segment.strip()
            if "=" not in segment:
                continue
            key, _, value = segment.partition("=")
            key = key.strip().lower()
            value = value.strip()
            if key:
                fields[key] = value
    return fields


def template_type_and_body(raw: str) -> tuple[str | None, str]:
    inner = raw[2:-2].strip()
    match = re.match(r"^(\w+)", inner)
    if not match:
        return None, inner
    template_type = match.group(1).lower()
    if template_type not in LISTING_TYPES:
        return None, inner
    body = inner[len(match.group(1)) :].strip()
    if body.startswith("|"):
        body = body[1:].strip()
    return template_type, body


def parse_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def extract_listings(body: str) -> tuple[list[dict], str]:
    listings: list[dict] = []
    removals: list[tuple[int, int]] = []

    for start, end, raw in find_template_spans(body):
        listing_type, template_body = template_type_and_body(raw)
        if listing_type is None:
            continue
        fields = parse_template_fields(template_body)
        listings.append(
            {
                "listing_type": listing_type,
                "name": fields.get("name"),
                "address": fields.get("address") or fields.get("directions"),
                "lat": parse_float(fields.get("lat")),
                "lon": parse_float(fields.get("long") or fields.get("lon")),
                "url": fields.get("url"),
                "phone": fields.get("phone"),
                "hours": fields.get("hours"),
                "price": fields.get("price"),
                "content": fields.get("content"),
                "listing_wikidata_qid": fields.get("wikidata"),
            }
        )
        removals.append((start, end))

    remaining = body
    for start, end in sorted(removals, key=lambda x: x[0], reverse=True):
        remaining = remaining[:start] + "\n" + remaining[end:]

    return listings, remaining


def split_long_text(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            cut = text.rfind("\n\n", start, end)
            if cut > start + max_chars // 2:
                end = cut
        piece = text[start:end].strip()
        if piece:
            parts.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return parts


def format_listing_text(meta: dict, listing: dict) -> str:
    name = listing.get("name") or "Listing"
    header = f"[{meta['city_name']} / {meta['section_path']} / {name}]"
    lines = [header]
    if listing.get("content"):
        lines.append(listing["content"])
    extras: list[str] = []
    if listing.get("address"):
        extras.append(listing["address"])
    if listing.get("hours"):
        extras.append(f"Hours: {listing['hours']}")
    if listing.get("price"):
        extras.append(f"Price: {listing['price']}")
    if listing.get("phone"):
        extras.append(f"Phone: {listing['phone']}")
    if listing.get("url"):
        extras.append(listing["url"])
    if extras:
        lines.append(". ".join(extras))
    return "\n\n".join(lines)


def format_section_text(meta: dict, piece: str) -> str:
    label = f"{meta['city_name']} / {meta['section_path']}"
    return f"[{label}]\n\n{piece}"


def base_meta(row: dict, section: dict) -> dict:
    return {
        "city_name": row["page_name"],
        "country_name": row["country"],
        "source_url": row["source_url"],
        "source": "wikivoyage",
        "lang": "en",
        "wikidata_qid": row.get("wikidata_qid"),
        "revision_id": row.get("revid"),
        "topic": section["topic"],
        "section_path": section["section_path"],
        "subsection": section.get("subsection"),
        "section": section["topic"],
    }


def build_chunks(row: dict) -> list[dict]:
    chunks: list[dict] = []
    for section in split_sections(row["wikitext"] or ""):
        meta = base_meta(row, section)
        listings, prose_body = extract_listings(section["body"])

        for listing in listings:
            if not any(
                listing.get(k)
                for k in ("name", "content", "address", "url", "hours", "price")
            ):
                continue
            chunks.append(
                {
                    **meta,
                    "chunk_kind": "listing",
                    "chunk_index": 0,
                    **listing,
                    "text": format_listing_text(meta, listing),
                }
            )

        plain = strip_remaining_templates(prose_body)
        if not plain:
            continue

        for i, piece in enumerate(
            split_long_text(plain, MAX_SECTION_CHARS, SECTION_OVERLAP)
        ):
            chunks.append(
                {
                    **meta,
                    "chunk_kind": "section",
                    "chunk_index": i,
                    "text": format_section_text(meta, piece),
                }
            )

    return chunks


def make_point_id(chunk: dict) -> str:
    if chunk["chunk_kind"] == "listing":
        name = chunk.get("name") or "unknown"
        qid = chunk.get("listing_wikidata_qid") or ""
        key = f"{chunk['source_url']}:listing:{chunk['listing_type']}:{name}:{qid}"
    else:
        key = (
            f"{chunk['source_url']}:section:{chunk['section_path']}:"
            f"{chunk['chunk_index']}"
        )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
