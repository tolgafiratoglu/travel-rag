"""Shared MariaDB access for Wikipedia POI (museum / historical site) scripts."""

from __future__ import annotations

import os
from typing import Literal

import pymysql

POIType = Literal["museum", "historical_site"]

DB_CONFIG = {
    "host": os.getenv("MARIADB_HOST", "localhost"),
    "port": int(os.getenv("MARIADB_PORT", "3306")),
    "user": os.getenv("MARIADB_USER", "wikivoyage"),
    "password": os.getenv("MARIADB_PASSWORD", "changeme"),
    "database": os.getenv("MARIADB_DATABASE", "wikivoyage"),
    "charset": "utf8mb4",
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wp_pois (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    poi_type        ENUM('museum','historical_site') NOT NULL,
    name            VARCHAR(512)  NOT NULL,
    city_name       VARCHAR(255)  NOT NULL,
    country_name    VARCHAR(128)  NOT NULL,
    source_url      VARCHAR(768)  NOT NULL,
    wiki_title      VARCHAR(512)  NULL,
    resolved_title  VARCHAR(512)  NULL,
    page_id         INT UNSIGNED  NULL,
    revid           BIGINT UNSIGNED NULL,
    wikidata_qid    VARCHAR(16)   NULL,
    html            LONGTEXT      NULL,
    fetched_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_source_url (source_url),
    KEY idx_type_city (poi_type, city_name),
    KEY idx_country (country_name),
    KEY idx_poi_type (poi_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_COLUMNS = (
    "id",
    "poi_type",
    "name",
    "city_name",
    "country_name",
    "source_url",
    "wiki_title",
    "resolved_title",
    "page_id",
    "revid",
    "wikidata_qid",
    "html",
    "fetched_at",
)


def connect():
    return pymysql.connect(**DB_CONFIG)


def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()


def load_pois(
    poi_type: POIType | None = None,
    *,
    country: str | None = None,
    city: str | None = None,
    name: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    clauses = ["html IS NOT NULL", "html != ''"]
    params: list[object] = []
    if poi_type:
        clauses.append("poi_type = %s")
        params.append(poi_type)
    if country:
        clauses.append("country_name = %s")
        params.append(country)
    if city:
        clauses.append("city_name = %s")
        params.append(city)
    if name:
        clauses.append("name = %s")
        params.append(name)

    where = " AND ".join(clauses)
    sql = f"""
        SELECT {", ".join(_COLUMNS)}
        FROM wp_pois
        WHERE {where}
        ORDER BY country_name, city_name, name
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
