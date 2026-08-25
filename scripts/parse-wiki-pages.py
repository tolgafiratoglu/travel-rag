#!/usr/bin/env python3
"""Fetch Wikipedia city/guide pages from data/wiki/sources.json into PostgreSQL.

Writes:
  wp_pages      — HTML for Qdrant (travel_chunks)
  wp_gov_links  — full URLs whose host matches *.gov.* (from page HTML)

By default ingests `pages` and `tourism_pages`. Skip with --pages-only.

Usage:
    POSTGRES_HOST=localhost .venv/bin/python scripts/parse-wiki-pages.py \\
        --country Turkey --city Istanbul

    POSTGRES_HOST=localhost .venv/bin/python scripts/parse-wiki-pages.py --limit 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import psycopg  # noqa: F401
except ImportError:
    print("Missing dependency: pip install psycopg[binary] httpx beautifulsoup4")
    sys.exit(1)

from lib.pg import connect
from lib.wp_gov import extract_gov_links
from lib.wp_pages_db import ensure_tables, replace_gov_links

API_URL = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "TravelRAGBot/0.1 (mailto:tolgafiratoglu@gmail.com)"
SOURCES_PATH = ROOT / "data" / "wiki" / "sources.json"
SLEEP_SECONDS = float(os.getenv("WP_FETCH_SLEEP", "1"))


def url_to_wiki_title(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    title = urllib.parse.unquote(path.split("/wiki/", 1)[-1])
    # Drop fragment for API title
    title = title.split("#", 1)[0]
    return title.replace("_", " ")


def load_jobs(
    *,
    country: str | None,
    city: str | None,
    pages_only: bool,
    limit: int | None,
) -> list[dict]:
    if not SOURCES_PATH.is_file():
        print(f"Missing {SOURCES_PATH}")
        sys.exit(1)

    data = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    jobs: list[dict] = []

    def add(kind: str, country_name: str, city_name: str, url: str) -> None:
        url = (url or "").strip()
        if not url:
            return
        jobs.append(
            {
                "page_kind": kind,
                "city_name": city_name,
                "country_name": country_name,
                "source_url": url.split("#", 1)[0],
                "wiki_title": url_to_wiki_title(url),
            }
        )

    for country_name, block in data.items():
        if country and country_name.lower() != country.lower():
            continue
        if not isinstance(block, dict):
            continue

        pages = block.get("pages") or {}
        for city_name, url in pages.items():
            if city and city_name.lower() != city.lower():
                continue
            add("city", country_name, city_name, url)
            if limit is not None and len(jobs) >= limit:
                return jobs

        if pages_only:
            continue

        for city_name, url in (block.get("tourism_pages") or {}).items():
            if city and city_name.lower() != city.lower():
                continue
            add("tourism", country_name, city_name, url)
            if limit is not None and len(jobs) >= limit:
                return jobs

        for city_name, url in (block.get("attraction_pages") or {}).items():
            if city and city_name.lower() != city.lower():
                continue
            add("attraction_list", country_name, city_name, url)
            if limit is not None and len(jobs) >= limit:
                return jobs

    return jobs


def fetch_parse(client: httpx.Client, wiki_title: str) -> dict:
    params = {
        "action": "parse",
        "page": wiki_title,
        "prop": "text|revid|displaytitle",
        "disableeditsection": "1",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
    }
    resp = client.get(API_URL, params=params)
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(body["error"].get("info") or body["error"])
    parsed = body.get("parse")
    if not parsed:
        raise RuntimeError(f"No parse result for: {wiki_title}")

    q = client.get(
        API_URL,
        params={
            "action": "query",
            "titles": parsed.get("title") or wiki_title,
            "prop": "pageprops",
            "ppprop": "wikibase_item",
            "redirects": "1",
            "format": "json",
            "formatversion": "2",
        },
    )
    q.raise_for_status()
    pages = q.json().get("query", {}).get("pages") or []
    if pages:
        pageprops = pages[0].get("pageprops") or {}
        parsed["pageprops"] = pageprops
        if pages[0].get("pageid") and not parsed.get("pageid"):
            parsed["pageid"] = pages[0]["pageid"]
    return parsed


def upsert_page(conn, job: dict, parsed: dict) -> str:
    text = parsed.get("text") or ""
    if isinstance(text, dict):
        text = text.get("*") or ""
    pageprops = parsed.get("pageprops") or {}
    resolved = parsed.get("title") or parsed.get("displaytitle") or job["wiki_title"]

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO wp_pages (
                page_kind, city_name, country_name, source_url,
                wiki_title, resolved_title, title, page_id, revid, wikidata_qid,
                html, fetched_at
            ) VALUES (
                %(page_kind)s, %(city_name)s, %(country_name)s, %(source_url)s,
                %(wiki_title)s, %(resolved_title)s, %(title)s, %(page_id)s, %(revid)s,
                %(wikidata_qid)s, %(html)s, %(fetched_at)s
            )
            ON CONFLICT (source_url) DO UPDATE SET
                page_kind = EXCLUDED.page_kind,
                city_name = EXCLUDED.city_name,
                country_name = EXCLUDED.country_name,
                wiki_title = EXCLUDED.wiki_title,
                resolved_title = EXCLUDED.resolved_title,
                title = EXCLUDED.title,
                page_id = EXCLUDED.page_id,
                revid = EXCLUDED.revid,
                wikidata_qid = EXCLUDED.wikidata_qid,
                html = EXCLUDED.html,
                fetched_at = EXCLUDED.fetched_at
            """,
            {
                **job,
                "resolved_title": resolved,
                "title": resolved,
                "page_id": parsed.get("pageid"),
                "revid": parsed.get("revid"),
                "wikidata_qid": pageprops.get("wikibase_item"),
                "html": text,
                "fetched_at": datetime.now(timezone.utc),
            },
        )
    conn.commit()
    return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Wikipedia pages from sources.json into PostgreSQL"
    )
    parser.add_argument("--country", help="Filter country (e.g. Turkey)")
    parser.add_argument("--city", help="Filter city key (e.g. Istanbul)")
    parser.add_argument("--limit", type=int, help="Max pages to fetch")
    parser.add_argument(
        "--pages-only",
        action="store_true",
        help="Only sources.pages (skip tourism_pages / attraction_pages)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    jobs = load_jobs(
        country=args.country,
        city=args.city,
        pages_only=args.pages_only,
        limit=args.limit,
    )
    if not jobs:
        print("No matching pages in sources.json")
        sys.exit(1)

    print(f"Loaded {len(jobs)} page job(s)")
    conn = connect()
    ensure_tables(conn)

    headers = {"User-Agent": USER_AGENT}
    ok = 0
    gov_total = 0
    with httpx.Client(headers=headers, timeout=90.0, follow_redirects=True) as client:
        for i, job in enumerate(jobs, 1):
            print(
                f"[{i}/{len(jobs)}] {job['page_kind']} | "
                f"{job['country_name']} / {job['city_name']} | {job['wiki_title']!r}"
            )
            try:
                parsed = fetch_parse(client, job["wiki_title"])
                html = upsert_page(conn, job, parsed)
                gov_links = extract_gov_links(html, page_url=job["source_url"])
                n_gov = replace_gov_links(
                    conn,
                    country_name=job["country_name"],
                    city_name=job["city_name"],
                    page_url=job["source_url"],
                    links=gov_links,
                )
                gov_total += n_gov
                print(
                    f"  OK pageid={parsed.get('pageid')} "
                    f"html_chars={len(html)} title={parsed.get('title')!r} "
                    f"gov_links={n_gov}"
                )
                ok += 1
            except Exception as exc:
                print(f"  ERROR: {exc}")

            if i < len(jobs):
                time.sleep(SLEEP_SECONDS)

    conn.close()
    print(f"Done. {ok}/{len(jobs)} pages upserted; {gov_total} gov link row(s).")


if __name__ == "__main__":
    main()
