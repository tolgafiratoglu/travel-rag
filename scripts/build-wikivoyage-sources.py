#!/usr/bin/env python3
"""Build data/wikivoyage/sources.json from en.wikivoyage.org country pages.

Reads the Cities and Other destinations sections (flat, one level) and writes:

  {
    "Turkey": {
      "wikivoyage_link": "https://en.wikivoyage.org/wiki/Turkey",
      "pages": { "Istanbul": "https://en.wikivoyage.org/wiki/Istanbul", ... }
    },
    ...
  }

Usage (from repo root):
  python scripts/build-wikivoyage-sources.py
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://en.wikivoyage.org/w/api.php"
USER_AGENT = "TravelRAG/0.1 (offline-ingestion; contact@example.com)"
BASE_URL = "https://en.wikivoyage.org/wiki/"
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "wikivoyage" / "sources.json"

COUNTRIES: dict[str, str] = {
    "Turkey": "Turkey",
    "France": "France",
    "United States": "United_States",
    "Germany": "Germany",
    "Spain": "Spain",
    "Italy": "Italy",
    "Japan": "Japan",
}

SECTIONS = ("Cities", "Other destinations")
SKIP_PREFIXES = (
    "File:",
    "Category:",
    "Template:",
    "Wikivoyage:",
    "Talk:",
    "MediaWiki:",
    "Draft:",
)
SKIP_TITLES = {
    "Main Page",
    "Travel topics",
    "Phrasebooks",
    "Europe",
    "Asia",
    "North America",
    "Middle East",
    "Mediterranean",
    "European Union",
    "Schengen Area",
    "Africa",
    "Oceania",
    "South America",
    "Central America",
    "Caribbean",
    "Turkey",
    "France",
    "United States",
    "Germany",
    "Spain",
    "Italy",
    "Japan",
}


def api_get(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def wiki_url(title: str) -> str:
    return BASE_URL + title.replace(" ", "_")


def extract_links(wikitext: str) -> dict[str, str]:
    pages: dict[str, str] = {}
    for match in re.finditer(r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]", wikitext):
        page = match.group(1).strip()
        label = (match.group(2) or page).strip()
        if any(page.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        normalized = page.replace("_", " ")
        if normalized in SKIP_TITLES or page in SKIP_TITLES:
            continue
        pages[label] = wiki_url(page)
    return pages


def section_wikitext(page_title: str, section_name: str) -> str | None:
    sections = api_get({"action": "parse", "page": page_title, "prop": "sections"})[
        "parse"
    ]["sections"]
    for section in sections:
        if section["line"].strip().lower() == section_name.lower():
            time.sleep(1.5)
            parsed = api_get(
                {
                    "action": "parse",
                    "page": page_title,
                    "section": section["index"],
                    "prop": "wikitext",
                }
            )
            return parsed["parse"]["wikitext"]["*"]
    return None


def build_country(country_name: str, wiki_title: str) -> dict:
    pages: dict[str, str] = {}
    for section in SECTIONS:
        wikitext = section_wikitext(wiki_title, section)
        if not wikitext:
            continue
        for label, url in extract_links(wikitext).items():
            pages.setdefault(label, url)
        time.sleep(1.5)
    return {
        "wikivoyage_link": wiki_url(wiki_title),
        "pages": dict(sorted(pages.items(), key=lambda item: item[0].lower())),
    }


def main() -> None:
    result: dict[str, dict] = {}
    for country, wiki_title in COUNTRIES.items():
        time.sleep(1.5)
        entry = build_country(country, wiki_title)
        result[country] = entry
        print(f"{country}: {len(entry['pages'])} pages")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
