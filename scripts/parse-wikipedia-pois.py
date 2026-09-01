#!/usr/bin/env python3
"""Fetch Wikipedia museum / historical-site pages into PostgreSQL (wp_pois).

Reads data/wiki/museums.json and historical_sites.json, pulls article
HTML via MediaWiki action=parse, upserts by source_url.

Usage (from repo root, Postgres on localhost:5433):
    POSTGRES_HOST=localhost .venv/bin/python scripts/parse-wikipedia-pois.py \\
        --type museum --city Ankara --limit 3

    POSTGRES_HOST=localhost .venv/bin/python scripts/parse-wikipedia-pois.py

Env:
    POSTGRES_HOST / POSTGRES_PORT / POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD
    WP_FETCH_SLEEP=1
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
    print("Missing dependency: pip install psycopg[binary] httpx")
    sys.exit(1)

from lib.pg import connect
from lib.wp_db import ensure_table

API_URL = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "TravelRAGBot/0.1 (mailto:tolgafiratoglu@gmail.com)"
DATA_DIR = ROOT / "data" / "wiki"
MUSEUMS_PATH = DATA_DIR / "museums.json"
HISTORICAL_PATH = DATA_DIR / "historical_sites.json"
SLEEP_SECONDS = float(os.getenv("WP_FETCH_SLEEP", "1"))


def url_to_wiki_title(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    return urllib.parse.unquote(path.split("/wiki/", 1)[-1]).replace("_", " ")


def load_jobs(
    poi_type: str | None,
    country: str | None,
    city: str | None,
    limit: int | None,
) -> list[dict]:
    specs: list[tuple[str, Path, str]] = [
        ("museum", MUSEUMS_PATH, "museums"),
        ("historical_site", HISTORICAL_PATH, "historical_sites"),
    ]
    jobs: list[dict] = []
    for ptype, path, key in specs:
        if poi_type and ptype != poi_type:
            continue
        if not path.is_file():
            print(f"WARN: missing {path}")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for country_name, block in data.items():
            if country and country_name.lower() != country.lower():
                continue
            cities = block.get(key) or {}
            for city_name, items in cities.items():
                if city and city_name.lower() != city.lower():
                    continue
                for item in items:
                    wiki_url = (item.get("wikipedia") or "").strip()
                    name = (item.get("name") or "").strip()
                    if not wiki_url or not name:
                        continue
                    jobs.append(
                        {
                            "poi_type": ptype,
                            "name": name,
                            "city_name": city_name,
                            "country_name": country_name,
                            "source_url": wiki_url,
                            "wiki_title": url_to_wiki_title(wiki_url),
                        }
                    )
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

    # Wikidata QID (not available on action=parse)
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


def upsert_poi(conn, job: dict, parsed: dict) -> None:
    text = parsed.get("text") or ""
    if isinstance(text, dict):
        text = text.get("*") or ""
    pageprops = parsed.get("pageprops") or {}
    wikidata_qid = pageprops.get("wikibase_item")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO wp_pois (
                poi_type, name, city_name, country_name, source_url,
                wiki_title, resolved_title, page_id, revid, wikidata_qid,
                html, fetched_at
            ) VALUES (
                %(poi_type)s, %(name)s, %(city_name)s, %(country_name)s, %(source_url)s,
                %(wiki_title)s, %(resolved_title)s, %(page_id)s, %(revid)s, %(wikidata_qid)s,
                %(html)s, %(fetched_at)s
            )
            ON CONFLICT (source_url) DO UPDATE SET
                poi_type = EXCLUDED.poi_type,
                name = EXCLUDED.name,
                city_name = EXCLUDED.city_name,
                country_name = EXCLUDED.country_name,
                wiki_title = EXCLUDED.wiki_title,
                resolved_title = EXCLUDED.resolved_title,
                page_id = EXCLUDED.page_id,
                revid = EXCLUDED.revid,
                wikidata_qid = EXCLUDED.wikidata_qid,
                html = EXCLUDED.html,
                fetched_at = EXCLUDED.fetched_at
            """,
            {
                **job,
                "resolved_title": parsed.get("title") or parsed.get("displaytitle"),
                "page_id": parsed.get("pageid"),
                "revid": parsed.get("revid"),
                "wikidata_qid": wikidata_qid,
                "html": text,
                "fetched_at": datetime.now(timezone.utc),
            },
        )
    conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Wikipedia POIs into PostgreSQL")
    parser.add_argument(
        "--type",
        choices=("museum", "historical_site", "all"),
        default="all",
        help="POI type to fetch (default: all)",
    )
    parser.add_argument("--country", help="Filter country name (e.g. Turkey)")
    parser.add_argument("--city", help="Filter city key (e.g. Ankara)")
    parser.add_argument("--limit", type=int, help="Max jobs to fetch")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    poi_type = None if args.type == "all" else args.type
    jobs = load_jobs(poi_type, args.country, args.city, args.limit)
    if not jobs:
        print("No matching POIs in JSON files")
        sys.exit(1)

    print(f"Loaded {len(jobs)} POI job(s)")
    conn = connect()
    ensure_table(conn)

    headers = {"User-Agent": USER_AGENT}
    ok = 0
    with httpx.Client(headers=headers, timeout=90.0, follow_redirects=True) as client:
        for i, job in enumerate(jobs, 1):
            title = job["wiki_title"]
            print(
                f"[{i}/{len(jobs)}] {job['poi_type']} | "
                f"{job['country_name']} / {job['city_name']} / {job['name']}"
            )
            try:
                parsed = fetch_parse(client, title)
                upsert_poi(conn, job, parsed)
                raw_text = parsed.get("text") or ""
                if isinstance(raw_text, dict):
                    raw_text = raw_text.get("*") or ""
                print(
                    f"  OK pageid={parsed.get('pageid')} revid={parsed.get('revid')} "
                    f"html_chars={len(raw_text)} title={parsed.get('title')!r}"
                )
                ok += 1
            except Exception as exc:
                print(f"  ERROR: {exc}")

            if i < len(jobs):
                time.sleep(SLEEP_SECONDS)

    conn.close()
    print(f"Done. {ok}/{len(jobs)} upserted.")


if __name__ == "__main__":
    main()
