"""MariaDB access for Wikipedia city/guide pages + gov reference links."""

from __future__ import annotations

import os

import pymysql

DB_CONFIG = {
    "host": os.getenv("MARIADB_HOST", "localhost"),
    "port": int(os.getenv("MARIADB_PORT", "3306")),
    "user": os.getenv("MARIADB_USER", "wikivoyage"),
    "password": os.getenv("MARIADB_PASSWORD", "changeme"),
    "database": os.getenv("MARIADB_DATABASE", "wikivoyage"),
    "charset": "utf8mb4",
}

CREATE_PAGES_SQL = """
CREATE TABLE IF NOT EXISTS wp_pages (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    page_kind       ENUM('city','tourism','attraction_list') NOT NULL DEFAULT 'city',
    city_name       VARCHAR(255)  NOT NULL,
    country_name    VARCHAR(128)  NOT NULL,
    source_url      VARCHAR(768)  NOT NULL,
    wiki_title      VARCHAR(512)  NULL,
    resolved_title  VARCHAR(512)  NULL,
    title           VARCHAR(512)  NULL,
    page_id         INT UNSIGNED  NULL,
    revid           BIGINT UNSIGNED NULL,
    wikidata_qid    VARCHAR(16)   NULL,
    html            LONGTEXT      NULL,
    fetched_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_source_url (source_url),
    KEY idx_city (city_name),
    KEY idx_country (country_name),
    KEY idx_kind (page_kind)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

CREATE_GOV_SQL = """
CREATE TABLE IF NOT EXISTS wp_gov_links (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    country_name    VARCHAR(128)  NOT NULL,
    city_name       VARCHAR(255)  NULL,
    page_url        VARCHAR(768)  NOT NULL,
    link_url        VARCHAR(768)  NOT NULL,
    link_text       VARCHAR(512)  NULL,
    domain          VARCHAR(255)  NULL,
    fetched_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_page_link (page_url(384), link_url(384)),
    KEY idx_city (city_name),
    KEY idx_domain (domain)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_PAGE_COLUMNS = (
    "id",
    "page_kind",
    "city_name",
    "country_name",
    "source_url",
    "wiki_title",
    "resolved_title",
    "title",
    "page_id",
    "revid",
    "wikidata_qid",
    "html",
    "fetched_at",
)


def connect():
    return pymysql.connect(**DB_CONFIG)


def ensure_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(CREATE_PAGES_SQL)
        cur.execute(CREATE_GOV_SQL)
    conn.commit()


def replace_gov_links(
    conn,
    *,
    country_name: str,
    city_name: str | None,
    page_url: str,
    links: list[dict[str, str]],
) -> int:
    """Delete prior gov links for this page, then insert fresh rows."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM wp_gov_links WHERE page_url = %s", (page_url,))
        n = 0
        for link in links:
            cur.execute(
                """
                INSERT INTO wp_gov_links (
                    country_name, city_name, page_url, link_url, link_text, domain
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    country_name,
                    city_name,
                    page_url,
                    link["link_url"],
                    link.get("link_text") or None,
                    link.get("domain") or None,
                ),
            )
            n += 1
    conn.commit()
    return n


def load_pages(
    *,
    country: str | None = None,
    city: str | None = None,
    page_kind: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    clauses = ["html IS NOT NULL", "html != ''"]
    params: list[object] = []
    if country:
        clauses.append("country_name = %s")
        params.append(country)
    if city:
        clauses.append("city_name = %s")
        params.append(city)
    if page_kind:
        clauses.append("page_kind = %s")
        params.append(page_kind)

    where = " AND ".join(clauses)
    sql = f"""
        SELECT {", ".join(_PAGE_COLUMNS)}
        FROM wp_pages
        WHERE {where}
        ORDER BY country_name, city_name, page_kind
    """
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)

    conn = connect()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()
