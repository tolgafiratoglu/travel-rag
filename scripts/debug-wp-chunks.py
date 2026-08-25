#!/usr/bin/env python3
"""Debug Unstructured partition_html + chunk_by_title for a wp_pages row.

Usage:
    POSTGRES_HOST=localhost .venv/bin/python scripts/debug-wp-chunks.py --city Istanbul
    POSTGRES_HOST=localhost .venv/bin/python scripts/debug-wp-chunks.py --city Istanbul --contains Airport
    POSTGRES_HOST=localhost .venv/bin/python scripts/debug-wp-chunks.py --city Istanbul --json > /tmp/wp-debug.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.wp_chunks import (  # noqa: E402
    COMBINE_UNDER,
    MAX_CHARS,
    _chunk_section,
    clean_wikipedia_html,
)
from lib.wp_pages_db import load_pages  # noqa: E402


def _meta_dict(el) -> dict:
    meta = getattr(el, "metadata", None)
    if meta is None:
        return {}
    if hasattr(meta, "to_dict"):
        raw = meta.to_dict()
        # Drop huge / non-serializable fields for readable dump
        skip = {"orig_elements", "coordinates", "detection_origin"}
        return {k: v for k, v in raw.items() if k not in skip and v not in (None, "", [])}
    return {}


def _snip(text: str | None, n: int = 120) -> str:
    t = (text or "").replace("\n", " | ").strip()
    return t if len(t) <= n else t[: n - 3] + "..."


def run(
    *,
    country: str | None,
    city: str | None,
    contains: str | None,
    limit_elements: int | None,
    as_json: bool,
) -> None:
    rows = load_pages(country=country, city=city, limit=1)
    if not rows:
        print("No wp_pages row (run parse-wiki-pages.py first)", file=sys.stderr)
        sys.exit(1)

    row = rows[0]
    from unstructured.chunking.title import chunk_by_title
    from unstructured.partition.html import partition_html

    cleaned = clean_wikipedia_html(row["html"] or "")
    elements = partition_html(text=cleaned)
    chunks = chunk_by_title(
        elements,
        max_characters=MAX_CHARS,
        combine_text_under_n_chars=COMBINE_UNDER,
    )

    needle = (contains or "").strip()

    if as_json:
        payload = {
            "page": {
                "title": row.get("title"),
                "city_name": row.get("city_name"),
                "source_url": row.get("source_url"),
                "html_chars": len(row.get("html") or ""),
            },
            "params": {
                "max_characters": MAX_CHARS,
                "combine_text_under_n_chars": COMBINE_UNDER,
            },
            "elements": [
                {
                    "i": i,
                    "type": type(el).__name__,
                    "meta": _meta_dict(el),
                    "text": el.text or "",
                }
                for i, el in enumerate(elements)
                if not needle or needle.lower() in (el.text or "").lower()
            ],
            "chunks": [
                {
                    "i": i,
                    "our_section": _chunk_section(ch, previous="Lead"),
                    "meta": _meta_dict(ch),
                    "text": getattr(ch, "text", None) or str(ch),
                }
                for i, ch in enumerate(chunks)
                if not needle
                or needle.lower() in (getattr(ch, "text", None) or str(ch)).lower()
            ],
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return

    print(
        f"page: {row.get('title')!r} | {row.get('city_name')} | "
        f"{row.get('source_url')} | html_chars={len(row.get('html') or '')}"
    )
    print(f"params: max_characters={MAX_CHARS} combine_under={COMBINE_UNDER}")
    print(f"partition_html: {len(elements)} elements")
    print(f"chunk_by_title: {len(chunks)} chunks")
    if needle:
        print(f"filter --contains {needle!r}")

    print("\n=== elements ===")
    shown = 0
    for i, el in enumerate(elements):
        text = el.text or ""
        if needle and needle.lower() not in text.lower():
            continue
        if limit_elements is not None and shown >= limit_elements and not needle:
            print(f"... truncated ({limit_elements}/{len(elements)}); use --all or --json")
            break
        meta = _meta_dict(el)
        focus = {
            k: meta.get(k)
            for k in ("section", "title", "category_depth", "parent_id")
            if meta.get(k) is not None
        }
        print(f"{i:04d} {type(el).__name__:22s} {focus} | {_snip(text)!r}")
        shown += 1

    print("\n=== chunks (our section = _chunk_section) ===")
    previous = "Lead"
    for i, ch in enumerate(chunks):
        text = (getattr(ch, "text", None) or str(ch)).strip()
        if needle and needle.lower() not in text.lower():
            continue
        our = _chunk_section(ch, previous=previous)
        # Keep carry-forward in sync with build_chunks even when filtering print.
        # (Full previous chain only accurate without --contains.)
        from lib.wp_chunks import _element_title, _titles_from_orig_elements

        if _element_title(ch) or _titles_from_orig_elements(ch):
            previous = our
        meta = _meta_dict(ch)
        focus = {k: meta.get(k) for k in ("section", "title", "category_name") if meta.get(k)}
        print(f"\n--- chunk {i} our_section={our!r} meta={focus} ---")
        print(_snip(text, 500))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Debug Unstructured chunks for wp_pages")
    p.add_argument("--country", help="Filter country_name")
    p.add_argument("--city", required=True, help="Filter city_name (e.g. Istanbul)")
    p.add_argument(
        "--contains",
        help="Only print elements/chunks whose text contains this substring",
    )
    p.add_argument(
        "--limit-elements",
        type=int,
        default=80,
        help="Max elements to print in text mode (default 80; ignored with --contains)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Print all elements (no limit)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable dump to stdout",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        country=args.country,
        city=args.city,
        contains=args.contains,
        limit_elements=None if args.all else args.limit_elements,
        as_json=args.json,
    )
