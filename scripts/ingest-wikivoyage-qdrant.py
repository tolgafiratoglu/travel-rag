#!/usr/bin/env python3
"""Ingest Wikivoyage wikitext from MariaDB into Qdrant.

Parses listing templates (see/do/go/eat/sleep/marker/listing) into dedicated
chunks, then section prose into separate chunks. Excludes drink, stay_safe,
stay_healthy at ingestion.

Usage:
    .venv/bin/pip install pymysql httpx qdrant-client

    MARIADB_HOST=localhost .venv/bin/python scripts/ingest-wikivoyage-qdrant.py \\
        --page Bordeaux --dry-run

    MARIADB_HOST=localhost QDRANT_URL=http://localhost:6333 \\
    OLLAMA_BASE_URL=http://localhost:11434 \\
    .venv/bin/python scripts/ingest-wikivoyage-qdrant.py --page Bordeaux

    MARIADB_HOST=localhost .venv/bin/python scripts/ingest-wikivoyage-qdrant.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid

import httpx

try:
    import pymysql
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        FieldCondition,
        Filter,
        MatchValue,
        PointStruct,
        VectorParams,
    )
except ImportError:
    print("Missing: pip install pymysql httpx qdrant-client")
    sys.exit(1)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "travel_chunks")

DB_CONFIG = {
    "host": os.getenv("MARIADB_HOST", "localhost"),
    "port": int(os.getenv("MARIADB_PORT", "3306")),
    "user": os.getenv("MARIADB_USER", "wikivoyage"),
    "password": os.getenv("MARIADB_PASSWORD", "changeme"),
    "database": os.getenv("MARIADB_DATABASE", "wikivoyage"),
    "charset": "utf8mb4",
}

EXCLUDED_TOPICS = frozenset({"drink", "stay_safe", "stay_healthy"})
LISTING_TYPES = frozenset({"see", "do", "go", "eat", "sleep", "marker", "listing"})

MAX_SECTION_CHARS = int(os.getenv("WV_MAX_CHUNK_CHARS", "1800"))
SECTION_OVERLAP = int(os.getenv("WV_CHUNK_OVERLAP", "200"))

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


def strip_remaining_templates(text: str) -> str:
    for start, end, _ in sorted(find_template_spans(text), key=lambda x: x[0], reverse=True):
        text = text[:start] + " " + text[end:]
    text = WIKILINK_RE.sub(r"\1", text)
    text = BOLD_RE.sub(r"\1", text)
    text = ITALIC_RE.sub(r"\1", text)
    text = HTML_TAG_RE.sub(" ", text)
    text = MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


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

        for i, piece in enumerate(split_long_text(plain, MAX_SECTION_CHARS, SECTION_OVERLAP)):
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


def load_cities(page_filter: str | None = None) -> list[dict]:
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            if page_filter:
                cur.execute(
                    """
                    SELECT page_name, country, source_url, wikidata_qid, revid, wikitext
                    FROM wv_cities
                    WHERE page_name = %s OR wiki_title = %s OR resolved_title = %s
                    """,
                    (page_filter, page_filter, page_filter),
                )
            else:
                cur.execute(
                    """
                    SELECT page_name, country, source_url, wikidata_qid, revid, wikitext
                    FROM wv_cities
                    WHERE wikitext IS NOT NULL AND wikitext != ''
                    ORDER BY country, page_name
                    """
                )
            return list(cur.fetchall())
    finally:
        conn.close()


async def embed_text(client: httpx.AsyncClient, text: str) -> list[float]:
    resp = await client.post(
        f"{OLLAMA_BASE_URL}/api/embed",
        json={"model": OLLAMA_EMBED_MODEL, "input": text},
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def ensure_collection(client: QdrantClient, dim: int) -> None:
    names = {c.name for c in client.get_collections().collections}
    if QDRANT_COLLECTION not in names:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )


def delete_city_points(client: QdrantClient, source_url: str) -> None:
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="source_url", match=MatchValue(value=source_url))]
        ),
    )


def qdrant_payload(chunk: dict) -> dict:
    skip = {"chunk_index"}
    return {k: v for k, v in chunk.items() if v is not None and k not in skip}


async def ingest_city(
    qdrant: QdrantClient,
    http: httpx.AsyncClient,
    row: dict,
    dry_run: bool,
) -> tuple[int, int, int]:
    chunks = build_chunks(row)
    listing_n = sum(1 for c in chunks if c["chunk_kind"] == "listing")
    section_n = sum(1 for c in chunks if c["chunk_kind"] == "section")
    print(f"  {len(chunks)} chunk(s): {listing_n} listing, {section_n} section")

    if dry_run:
        for chunk in chunks[:5]:
            kind = chunk["chunk_kind"]
            label = chunk.get("name") or chunk["section_path"]
            print(f"    [{kind}] {chunk['topic']} / {label}: {chunk['text'][:100]}...")
        if len(chunks) > 5:
            print(f"    ... +{len(chunks) - 5} more")
        return listing_n, section_n, len(chunks)

    delete_city_points(qdrant, row["source_url"])

    points: list[PointStruct] = []
    for i, chunk in enumerate(chunks):
        vector = await embed_text(http, chunk["text"])
        if not points:
            ensure_collection(qdrant, len(vector))
        points.append(
            PointStruct(
                id=make_point_id(chunk),
                vector=vector,
                payload=qdrant_payload(chunk),
            )
        )
        if (i + 1) % 20 == 0:
            print(f"  embedded {i + 1}/{len(chunks)}")

    qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)
    return listing_n, section_n, len(chunks)


async def main_async(page: str | None, dry_run: bool) -> None:
    rows = load_cities(page)
    if not rows:
        print("No rows in wv_cities (run parse-wikivoyage.py first)")
        sys.exit(1)

    print(f"Loaded {len(rows)} city/cities from wv_cities")
    qdrant = QdrantClient(url=QDRANT_URL)
    totals = {"listing": 0, "section": 0, "all": 0}

    async with httpx.AsyncClient(timeout=120.0) as http:
        for i, row in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] {row['country']} / {row['page_name']}")
            try:
                listing_n, section_n, all_n = await ingest_city(
                    qdrant, http, row, dry_run
                )
                totals["listing"] += listing_n
                totals["section"] += section_n
                totals["all"] += all_n
                if not dry_run:
                    print(f"  upserted {all_n} point(s)")
            except Exception as exc:
                print(f"  ERROR: {exc}")

    print(
        f"Done. Total: {totals['all']} chunks "
        f"({totals['listing']} listing, {totals['section']} section)"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest wv_cities into Qdrant")
    parser.add_argument("--page", help="Single city (page_name or wiki title)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no embed")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main_async(args.page, args.dry_run))
