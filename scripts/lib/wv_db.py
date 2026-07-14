"""Shared MariaDB access for Wikivoyage ingest scripts."""

from __future__ import annotations

import os
import re

import pymysql

DB_CONFIG = {
    "host": os.getenv("MARIADB_HOST", "localhost"),
    "port": int(os.getenv("MARIADB_PORT", "3306")),
    "user": os.getenv("MARIADB_USER", "wikivoyage"),
    "password": os.getenv("MARIADB_PASSWORD", "changeme"),
    "database": os.getenv("MARIADB_DATABASE", "wikivoyage"),
    "charset": "utf8mb4",
}

_BASE_COLUMNS = (
    "page_name",
    "country",
    "source_url",
    "wikidata_qid",
    "revid",
    "wikitext",
)
_COORD_COLUMNS = ("latitude", "longitude")


def slugify_page(name: str) -> str:
    text = name.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text, flags=re.UNICODE)
    return text.strip("-") or "unknown"


def load_cities(
    page_filter: str | None = None,
    *,
    include_coords: bool = False,
    add_page_slug: bool = False,
) -> list[dict]:
    columns = list(_BASE_COLUMNS)
    if include_coords:
        columns.extend(_COORD_COLUMNS)
    col_sql = ", ".join(columns)

    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            if page_filter:
                cur.execute(
                    f"""
                    SELECT {col_sql}
                    FROM wv_cities
                    WHERE page_name = %s OR wiki_title = %s OR resolved_title = %s
                    """,
                    (page_filter, page_filter, page_filter),
                )
            else:
                cur.execute(
                    f"""
                    SELECT {col_sql}
                    FROM wv_cities
                    WHERE wikitext IS NOT NULL AND wikitext != ''
                    ORDER BY country, page_name
                    """
                )
            rows = list(cur.fetchall())
    finally:
        conn.close()

    if add_page_slug:
        for row in rows:
            row["page_slug"] = slugify_page(row["page_name"])
    return rows
