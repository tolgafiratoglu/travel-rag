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

# Guide pages only (wp_pages). Leaf heading → canonical group.
# Lead and unlisted sections are dropped for city/tourism/attraction_list.
GUIDE_PAGE_KINDS = frozenset({"city", "tourism", "attraction_list"})

SECTION_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "sights": ("Main sights", "Landmarks"),
    "museums": ("Museums", "Museums and galleries"),
    "architecture": ("Architecture", "Cityscape"),
    "parks": ("Parks", "Parks and gardens", "National Park"),
    "tourism": ("Tourism",),
    "transport": ("Transport", "Transportation"),
    "public_transport": ("Public transport", "Metro", "Tram", "Bus", "Buses"),
    "rail": ("Rail", "Railway", "Railways"),
    "airport": ("Airport", "Airports", "Air"),
    "road": ("Roads",),
    "port": ("Port",),
    "cycling": ("Cycling",),
    "food": ("Cuisine", "Food", "Gastronomy"),
    "nature": (
        "Geography",
        "Geography and climate",
        "Physical geography",
        "Climate",
        "Flora",
        "Fauna",
        "Wildlife",
    ),
    "districts": ("Districts",),
    "activities": ("Festivals", "Recreation", "Leisure and entertainment"),
}

MAX_CHARS = int(os.getenv("WP_MAX_CHUNK_CHARS", "1800"))
COMBINE_UNDER = int(os.getenv("WP_COMBINE_UNDER_N_CHARS", "200"))


def _normalize_heading(title: str | None) -> str:
    if not title:
        return ""
    return re.sub(r"\s+", " ", title).strip().lower()


def _section_group_map() -> dict[str, str]:
    """normalized heading → canonical group (built once)."""
    out: dict[str, str] = {}
    for group, titles in SECTION_ALLOWLIST.items():
        for title in titles:
            out[_normalize_heading(title)] = group
    return out


_SECTION_GROUP_BY_HEADING = _section_group_map()


def _skip_section(title: str | None) -> bool:
    if not title:
        return False
    normalized = _normalize_heading(title)
    return any(re.match(pat, normalized) for pat in SKIP_SECTION_PATTERNS)


def _guide_section_group(section_path: str) -> str | None:
    """Return canonical group for a guide section leaf, or None if not allowed."""
    leaf = section_path.split("/")[-1].strip()
    return _SECTION_GROUP_BY_HEADING.get(_normalize_heading(leaf))


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


def derive_source_kind(row: dict) -> str:
    """Uniform source axis across collections: guide | museum | historical_site.

    ``poi_type`` only exists on wp_pois rows; guide pages fall back to "guide".
    """
    poi_type = (row.get("poi_type") or "").strip().lower()
    if poi_type == "museum":
        return "museum"
    if poi_type in ("historical_site", "historical"):
        return "historical_site"
    return "guide"


def make_section_id(source_url: str, section_path: str) -> str:
    """Stable id shared by every chunk of one page section."""
    key = f"{source_url}:section:{section_path}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def make_chunk_id(source_url: str, section_path: str, chunk_index: int) -> str:
    """Stable per-chunk id; also used as the Qdrant point id."""
    key = f"{source_url}:wiki_section:{section_path}:{chunk_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def section_prefix(name: str, section_path: str) -> str:
    """The ``[Name / Section]`` header prepended to embedded chunk text."""
    label = f"{name} / {section_path}" if name else section_path
    if not label:
        return ""
    return f"[{label}]\n\n"


def strip_section_prefix(payload: dict) -> tuple[str, bool]:
    """Recover chunk body from embedded text. Returns (body, prefix_matched).

    The prefix is rebuilt exactly from ``name`` + ``section_path``, so a miss
    means the point predates the current label format and must be left alone.
    """
    text = payload.get("text") or ""
    prefix = section_prefix(
        (payload.get("name") or "").strip(),
        (payload.get("section_path") or payload.get("section") or "").strip(),
    )
    if prefix and text.startswith(prefix):
        return text[len(prefix) :], True
    return text, False


def assign_section_sequence(chunks: list[dict]) -> None:
    """Number chunks inside each section (0-based seq + per-section total).

    ``section_chunk_total`` cannot be derived from a partial retrieval result,
    so it has to be stamped here where the whole section is visible.
    """
    by_section: dict[str, list[dict]] = {}
    for chunk in chunks:
        by_section.setdefault(chunk["section_id"], []).append(chunk)
    for group in by_section.values():
        group.sort(key=lambda c: c["chunk_index"])
        for seq, chunk in enumerate(group):
            chunk["section_chunk_seq"] = seq
            chunk["section_chunk_total"] = len(group)


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
        "source_kind": derive_source_kind(row),
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
        leaf = section_path.split("/")[-1].strip()
        if _skip_section(leaf):
            continue

        section_group: str | None = None
        if page_kind in GUIDE_PAGE_KINDS:
            section_group = _guide_section_group(section_path)
            if section_group is None:
                continue

        payload = {
            **base,
            "chunk_kind": "wiki_section",
            "chunk_index": i,
            "section_path": section_path,
            "section": section_path,
            "section_id": make_section_id(row["source_url"], section_path),
            "chunk_id": make_chunk_id(row["source_url"], section_path, i),
            # ``text`` stays the embedding input (the header carries city /
            # section signal); ``body_text`` is what gets shown to the model.
            "body_text": text,
            "text": f"{section_prefix(display_name, section_path)}{text}",
        }
        if section_group:
            payload["section_group"] = section_group
        out.append(payload)

    assign_section_sequence(out)
    return out


def make_point_id(chunk: dict) -> str:
    if chunk.get("chunk_id"):
        return str(chunk["chunk_id"])
    return make_chunk_id(
        chunk["source_url"], chunk["section_path"], chunk["chunk_index"]
    )
