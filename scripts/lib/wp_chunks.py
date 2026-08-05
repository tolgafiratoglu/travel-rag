"""Wikipedia HTML → Unstructured chunk_by_title → Qdrant-ready payloads.

Used for:
  - City / tourism guide pages (wp_pages → travel_chunks)
  - Museum / historical POIs (wp_pois → travel_museums / historical)
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime

from bs4 import BeautifulSoup

SKIP_SECTION_PATTERNS = (
    r"^references$",
    r"^see also$",
    r"^external links$",
    r"^notes$",
    r"^further reading$",
    r"^bibliography$",
    r"^citations$",
    r"^sources$",
    r"^footnotes$",
    r"^notes and references$",
    r"^gallery$",
)

MAX_CHARS = int(os.getenv("WP_MAX_CHUNK_CHARS", "1800"))
COMBINE_UNDER = int(os.getenv("WP_COMBINE_UNDER_N_CHARS", "200"))


def _skip_section(title: str | None) -> bool:
    if not title:
        return False
    normalized = re.sub(r"\s+", " ", title).strip().lower()
    return any(re.match(pat, normalized) for pat in SKIP_SECTION_PATTERNS)


def clean_wikipedia_html(html: str) -> str:
    """Strip Wikipedia chrome before Unstructured partition."""
    soup = BeautifulSoup(html, "html.parser")

    for selector in (
        "script",
        "style",
        "noscript",
        ".mw-editsection",
        ".navbox",
        ".vertical-navbox",
        ".sistersitebox",
        ".hatnote",
        ".ambox",
        ".tmbox",
        "#toc",
        ".toc",
        ".reflist",
        ".references",
        "sup.reference",
        ".mw-references-wrap",
        "table.infobox",
        "table.sidebar",
        "table.mbox-small",
        ".thumb",
        ".metadata",
        ".noprint",
    ):
        for node in soup.select(selector):
            node.decompose()

    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        title = heading.get_text(" ", strip=True)
        if not _skip_section(title):
            continue
        sibling = heading.next_sibling
        heading.decompose()
        while sibling is not None:
            nxt = sibling.next_sibling
            if getattr(sibling, "name", None) and re.match(
                r"^h[1-6]$", sibling.name or ""
            ):
                break
            sibling.extract()
            sibling = nxt

    body = soup.body or soup
    return str(body)


def _element_title(el) -> str:
    """Read section/title from Unstructured element metadata (often empty on chunks)."""
    meta = getattr(el, "metadata", None)
    if meta is None:
        return ""
    if hasattr(meta, "to_dict"):
        data = meta.to_dict()
        for key in ("section", "title", "category_name"):
            val = data.get(key)
            if val:
                return str(val).strip()
    for attr in ("section", "title"):
        val = getattr(meta, attr, None)
        if val:
            return str(val).strip()
    return ""


def _titles_from_orig_elements(chunk) -> list[str]:
    """Title texts embedded in a CompositeElement (chunk_by_title)."""
    meta = getattr(chunk, "metadata", None)
    orig = getattr(meta, "orig_elements", None) if meta is not None else None
    if not orig:
        return []
    titles: list[str] = []
    for el in orig:
        if type(el).__name__ != "Title":
            continue
        text = (getattr(el, "text", None) or "").strip()
        if text:
            titles.append(text)
    return titles


def _chunk_section(chunk, *, previous: str = "Lead") -> str:
    """Resolve Wikipedia section for a chunk_by_title result.

    Unstructured often leaves CompositeElement.metadata.section empty even when
    the chunk starts with a Title (e.g. "Airports"). Prefer metadata, then the
    first Title in orig_elements, else carry the previous section (continuation
    chunks), else Lead.
    """
    from_meta = _element_title(chunk)
    if from_meta:
        return from_meta
    titles = _titles_from_orig_elements(chunk)
    if titles:
        return titles[0]
    return previous or "Lead"


def _page_title(row: dict) -> str:
    for key in ("title", "resolved_title", "wiki_title", "name"):
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def build_chunks(row: dict) -> list[dict]:
    """Partition cleaned HTML and emit payload dicts with rich metadata."""
    from unstructured.chunking.title import chunk_by_title
    from unstructured.partition.html import partition_html

    cleaned = clean_wikipedia_html(row["html"] or "")
    if not cleaned.strip():
        return []

    elements = partition_html(text=cleaned)
    raw_chunks = chunk_by_title(
        elements,
        max_characters=MAX_CHARS,
        combine_text_under_n_chars=COMBINE_UNDER,
    )

    fetched_at = row.get("fetched_at")
    if isinstance(fetched_at, datetime):
        fetched_at = fetched_at.isoformat()
    elif fetched_at is not None:
        fetched_at = str(fetched_at)

    title = _page_title(row)
    display_name = (row.get("name") or title or row.get("city_name") or "").strip()
    city_name = (row.get("city_name") or "").strip()
    page_kind = (row.get("page_kind") or "").strip()
    poi_type = (row.get("poi_type") or "").strip()

    base = {
        "source": "wikipedia",
        "lang": "en",
        "title": title,
        "wiki_title": row.get("wiki_title") or title,
        "resolved_title": row.get("resolved_title") or title,
        "name": display_name,
        "city_name": city_name,
        "country_name": row.get("country_name") or "",
        "source_url": row["source_url"],
        "wikipedia_url": row["source_url"],
        "page_id": row.get("page_id"),
        "revision_id": row.get("revid"),
        "wikidata_qid": row.get("wikidata_qid"),
        "fetched_at": fetched_at,
        "page_kind": page_kind or None,
        "poi_type": poi_type or None,
    }

    out: list[dict] = []
    previous_section = "Lead"
    for i, chunk in enumerate(raw_chunks):
        text = (getattr(chunk, "text", None) or str(chunk)).strip()
        if not text:
            continue
        section_path = _chunk_section(chunk, previous=previous_section)
        # Remember explicit titles for continuation chunks that omit Title.
        if _element_title(chunk) or _titles_from_orig_elements(chunk):
            previous_section = section_path
        if _skip_section(section_path.split("/")[-1].strip()):
            continue

        label = f"{display_name} / {section_path}" if display_name else section_path
        out.append(
            {
                **base,
                "chunk_kind": "wiki_section",
                "chunk_index": i,
                "section_path": section_path,
                "section": section_path,
                "text": f"[{label}]\n\n{text}",
            }
        )
    return out


def make_point_id(chunk: dict) -> str:
    key = (
        f"{chunk['source_url']}:wiki_section:{chunk['section_path']}:"
        f"{chunk['chunk_index']}"
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
