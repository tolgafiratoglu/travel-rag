"""Shared helpers for retrieval eval / golden validation."""

from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import unquote, urlparse, urlunparse

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

# Heading synonyms for soft match + validator suggestions.
SECTION_SYNONYMS: dict[str, frozenset[str]] = {
    "transport": frozenset({"transport", "transportation"}),
    "airport": frozenset({"airport", "airports", "air"}),
    "park": frozenset({"park", "parks", "parks and gardens"}),
    "museum": frozenset({"museum", "museums", "museums and galleries"}),
    "sight": frozenset({"main sights", "landmarks"}),
}

_CITY_ALIASES: dict[str, list[str]] = {
    "istanbul": ["Istanbul", "İstanbul"],
    "i̇stanbul": ["Istanbul", "İstanbul"],
    "antalya": ["Antalya"],
}


def norm_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip().lower()


def norm_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    path = unquote(parsed.path or "").rstrip("/")
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", "")
    )


def section_leaf(section: str | None) -> str:
    raw = (section or "").strip()
    if not raw:
        return ""
    return norm_text(raw.split("/")[-1])


def relevant_key(item: dict) -> tuple[str, str]:
    return (norm_url(item.get("source_url")), section_leaf(item.get("section")))


def section_equiv(a: str, b: str) -> bool:
    """Exact leaf match or known synonym group."""
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    for group in SECTION_SYNONYMS.values():
        if na in group and nb in group:
            return True
    return False


def city_filter_values(city: str) -> list[str]:
    name = city.strip()
    if not name:
        return []
    aliases = _CITY_ALIASES.get(name.casefold())
    if aliases:
        return list(dict.fromkeys([*aliases, name]))
    return [name]


def build_city_filter(city: str | None) -> Filter | None:
    if not city:
        return None
    values = city_filter_values(city)
    if not values:
        return None
    if len(values) == 1:
        return Filter(
            must=[FieldCondition(key="city_name", match=MatchValue(value=values[0]))]
        )
    return Filter(
        must=[FieldCondition(key="city_name", match=MatchAny(any=values))]
    )


def load_inventory(
    client: QdrantClient,
    collection: str,
) -> dict[str, dict[str, set[str]]]:
    """url_norm → { display_url, sections: set[display leaf], section_norms }."""
    by_url: dict[str, dict] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            url = (payload.get("source_url") or "").strip()
            if not url:
                continue
            key = norm_url(url)
            bucket = by_url.setdefault(
                key,
                {
                    "source_url": url,
                    "sections": set(),
                    "section_norms": set(),
                    "cities": set(),
                },
            )
            leaf_raw = (payload.get("section") or payload.get("section_path") or "").strip()
            leaf = leaf_raw.split("/")[-1].strip() if leaf_raw else ""
            if leaf:
                bucket["sections"].add(leaf)
                bucket["section_norms"].add(norm_text(leaf))
            city = (payload.get("city_name") or "").strip()
            if city:
                bucket["cities"].add(city)
        if offset is None:
            break
    return by_url


def inventory_has(
    inventory: dict[str, dict],
    source_url: str,
    section: str,
    *,
    allow_synonyms: bool = True,
) -> tuple[bool, str | None]:
    """Return (ok, matched_display_section)."""
    url_key = norm_url(source_url)
    bucket = inventory.get(url_key)
    if not bucket:
        return False, None
    want = section_leaf(section)
    if not want:
        return False, None
    for display in bucket["sections"]:
        if norm_text(display) == want:
            return True, display
        if allow_synonyms and section_equiv(display, want):
            return True, display
    return False, None


def suggest_sections(
    inventory: dict[str, dict],
    source_url: str,
    section: str,
    *,
    limit: int = 5,
) -> list[str]:
    bucket = inventory.get(norm_url(source_url))
    if not bucket:
        return []
    want = section_leaf(section)
    available = sorted(bucket["sections"])
    if not want:
        return available[:limit]
    # Prefer synonym / substring / token overlap.
    scored: list[tuple[int, str]] = []
    want_tokens = set(want.split())
    for display in available:
        n = norm_text(display)
        score = 0
        if section_equiv(n, want):
            score += 100
        if want in n or n in want:
            score += 50
        score += len(want_tokens & set(n.split())) * 10
        scored.append((score, display))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s for score, s in scored if score > 0][:limit] or available[:limit]


def filter_reachable(
    relevant: list[dict],
    inventory: dict[str, dict],
    *,
    allow_synonyms: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Split relevant into (reachable, unreachable)."""
    ok: list[dict] = []
    bad: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in relevant:
        key = relevant_key(item)
        if not key[0] or not key[1] or key in seen:
            continue
        present, matched = inventory_has(
            inventory,
            item.get("source_url") or "",
            item.get("section") or "",
            allow_synonyms=allow_synonyms,
        )
        if present:
            seen.add(key)
            entry = {
                "source_url": (item.get("source_url") or "").strip(),
                "section": matched or (item.get("section") or "").strip(),
            }
            ok.append(entry)
        else:
            bad.append(
                {
                    "source_url": (item.get("source_url") or "").strip(),
                    "section": (item.get("section") or "").strip(),
                    "similar": suggest_sections(
                        inventory,
                        item.get("source_url") or "",
                        item.get("section") or "",
                    ),
                }
            )
    return ok, bad
