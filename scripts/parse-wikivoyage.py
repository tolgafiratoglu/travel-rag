#!/usr/bin/env python3
"""Fetch Wikivoyage pages from sources.json and store raw wikitext in MariaDB.

Uses the MediaWiki query API (revisions) for wikitext + metadata.
Rows are inserted or updated by source_url (ON DUPLICATE KEY UPDATE).

Usage (from repo root, MariaDB on localhost:3306):
    .venv/bin/pip install pymysql httpx
    MARIADB_HOST=localhost .venv/bin/python scripts/parse-wikivoyage.py

Single page test:
    MARIADB_HOST=localhost .venv/bin/python scripts/parse-wikivoyage.py --page Istanbul

Env:
    MARIADB_HOST=localhost
    MARIADB_PORT=3306
    MARIADB_DATABASE=wikivoyage
    MARIADB_USER=wikivoyage
    MARIADB_PASSWORD=changeme
    WV_FETCH_SLEEP=1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import httpx

try:
    import pymysql
except ImportError:
    print("Missing dependency: pip install pymysql httpx")
    sys.exit(1)

API_URL = "https://en.wikivoyage.org/w/api.php"
USER_AGENT = "TravelRAGBot/0.1 (mailto:tolgafiratoglu@gmail.com)"
SOURCES_PATH = Path(__file__).resolve().parent.parent / "data" / "wikivoyage" / "sources.json"
SLEEP_SECONDS = float(os.getenv("WV_FETCH_SLEEP", "1"))

DB_CONFIG = {
    "host": os.getenv("MARIADB_HOST", "localhost"),
    "port": int(os.getenv("MARIADB_PORT", "3306")),
    "user": os.getenv("MARIADB_USER", "wikivoyage"),
    "password": os.getenv("MARIADB_PASSWORD", "changeme"),
    "database": os.getenv("MARIADB_DATABASE", "wikivoyage"),
    "charset": "utf8mb4",
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wv_cities (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    page_name       VARCHAR(255)  NOT NULL,
    wiki_title      VARCHAR(255)  NOT NULL,
    resolved_title  VARCHAR(255)  NULL,
    country         VARCHAR(128)  NOT NULL,
    source_url      VARCHAR(512)  NOT NULL,
    page_id         INT UNSIGNED  NULL,
    revid           BIGINT UNSIGNED NULL,
    revision_ts     DATETIME      NULL,
    wikidata_qid    VARCHAR(16)   NULL,
    latitude        DECIMAL(9,6)  NULL,
    longitude       DECIMAL(9,6)  NULL,
    wikitext        LONGTEXT      NULL,
    categories_json JSON          NULL,
    fetched_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_source_url (source_url),
    KEY idx_country (country),
    KEY idx_wiki_title (wiki_title),
    KEY idx_wikidata_qid (wikidata_qid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def url_to_wiki_title(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    return urllib.parse.unquote(path.split("/wiki/", 1)[-1]).replace("_", " ")


def load_page_jobs(page_filter: str | None = None) -> list[dict]:
    data = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    jobs: list[dict] = []
    for country, meta in data.items():
        for page_name, source_url in meta["pages"].items():
            if page_filter and page_name.lower() != page_filter.lower():
                wiki_title = url_to_wiki_title(source_url)
                if wiki_title.lower() != page_filter.lower():
                    continue
            jobs.append(
                {
                    "page_name": page_name,
                    "wiki_title": url_to_wiki_title(source_url),
                    "country": country,
                    "source_url": source_url.rstrip(),
                }
            )
    return jobs


def fetch_page_raw(client: httpx.Client, wiki_title: str) -> dict:
    params = {
        "action": "query",
        "titles": wiki_title,
        "redirects": "1",
        "prop": "revisions|info|pageprops|coordinates|categories",
        "rvprop": "content|ids|timestamp",
        "rvslots": "main",
        "ppprop": "wikibase_item|disambiguation",
        "cllimit": "max",
        "format": "json",
        "formatversion": "2",
    }
    resp = client.get(API_URL, params=params)
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    pages = body.get("query", {}).get("pages", [])
    if not pages:
        raise RuntimeError(f"No page data for: {wiki_title}")
    page = pages[0]
    if page.get("missing"):
        raise RuntimeError(f"Page not found: {wiki_title}")
    return page


def _parse_revision(page: dict) -> tuple[str | None, int | None, str | None]:
    revisions = page.get("revisions") or []
    if not revisions:
        return None, None, None
    rev = revisions[0]
    slots = rev.get("slots") or {}
    main = slots.get("main") or {}
    content = main.get("content")
    revid = rev.get("revid")
    timestamp = rev.get("timestamp")
    if timestamp:
        timestamp = timestamp.replace("T", " ").replace("Z", "")
    return content, revid, timestamp


def _parse_coordinates(page: dict) -> tuple[float | None, float | None]:
    coords = page.get("coordinates") or []
    if not coords:
        return None, None
    primary = next((c for c in coords if c.get("primary")), coords[0])
    return primary.get("lat"), primary.get("lon")


def _wikitext_preview(wikitext: str, max_len: int = 120) -> str:
    one_line = re.sub(r"\s+", " ", wikitext).strip()
    return one_line[:max_len] + ("..." if len(one_line) > max_len else "")


def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        cur.execute(
            """
            ALTER TABLE wv_cities
            ADD COLUMN IF NOT EXISTS resolved_title VARCHAR(255) NULL AFTER wiki_title
            """
        )
    conn.commit()


def upsert_city(conn, job: dict, page: dict) -> None:
    wikitext, revid, revision_ts = _parse_revision(page)
    lat, lon = _parse_coordinates(page)
    pageprops = page.get("pageprops") or {}
    wikidata_qid = pageprops.get("wikibase_item")
    categories = page.get("categories") or []
    resolved_title = page.get("title")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO wv_cities (
                page_name, wiki_title, resolved_title, country, source_url,
                page_id, revid, revision_ts,
                wikidata_qid, latitude, longitude,
                wikitext, categories_json, fetched_at
            ) VALUES (
                %(page_name)s, %(wiki_title)s, %(resolved_title)s, %(country)s, %(source_url)s,
                %(page_id)s, %(revid)s, %(revision_ts)s,
                %(wikidata_qid)s, %(latitude)s, %(longitude)s,
                %(wikitext)s, %(categories_json)s, %(fetched_at)s
            )
            ON DUPLICATE KEY UPDATE
                page_name = VALUES(page_name),
                wiki_title = VALUES(wiki_title),
                resolved_title = VALUES(resolved_title),
                country = VALUES(country),
                page_id = VALUES(page_id),
                revid = VALUES(revid),
                revision_ts = VALUES(revision_ts),
                wikidata_qid = VALUES(wikidata_qid),
                latitude = VALUES(latitude),
                longitude = VALUES(longitude),
                wikitext = VALUES(wikitext),
                categories_json = VALUES(categories_json),
                fetched_at = VALUES(fetched_at)
            """,
            {
                **job,
                "resolved_title": resolved_title,
                "page_id": page.get("pageid"),
                "revid": revid,
                "revision_ts": revision_ts,
                "wikidata_qid": wikidata_qid,
                "latitude": lat,
                "longitude": lon,
                "wikitext": wikitext,
                "categories_json": json.dumps(categories, ensure_ascii=False),
                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
    conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Wikivoyage pages into MariaDB")
    parser.add_argument(
        "--page",
        help="Fetch only this page (sources.json key or wiki title, e.g. Istanbul)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not SOURCES_PATH.is_file():
        print(f"ERROR: sources file not found: {SOURCES_PATH}")
        sys.exit(1)

    jobs = load_page_jobs(page_filter=args.page)
    if not jobs:
        print("No matching pages in sources.json")
        sys.exit(1)

    print(f"Loaded {len(jobs)} page(s) from {SOURCES_PATH}")
    conn = pymysql.connect(**DB_CONFIG)
    ensure_table(conn)

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=60.0) as client:
        for i, job in enumerate(jobs, 1):
            title = job["wiki_title"]
            print(f"[{i}/{len(jobs)}] {job['country']} / {job['page_name']} ({title})")
            try:
                page = fetch_page_raw(client, title)
                upsert_city(conn, job, page)

                wikitext, revid, _ = _parse_revision(page)
                wikidata = (page.get("pageprops") or {}).get("wikibase_item")
                resolved = page.get("title")
                chars = len(wikitext or "")
                redirect_note = ""
                if resolved and resolved.lower() != title.lower():
                    redirect_note = f" resolved_title={resolved!r}"
                print(
                    f"  OK revid={revid} qid={wikidata}{redirect_note} "
                    f"wikitext_chars={chars} preview={_wikitext_preview(wikitext or '')!r}"
                )
            except Exception as exc:
                print(f"  ERROR: {exc}")

            if i < len(jobs):
                time.sleep(SLEEP_SECONDS)

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
