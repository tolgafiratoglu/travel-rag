"""Shared PostgreSQL access for Wikipedia POI (museum / historical site) scripts."""

from __future__ import annotations

from typing import Literal

from lib.pg import DB_CONFIG, connect, connect_dict

POIType = Literal["museum", "historical_site"]

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wp_pois (
    id              BIGSERIAL PRIMARY KEY,
    poi_type        TEXT NOT NULL
        CHECK (poi_type IN ('museum', 'historical_site')),
    name            VARCHAR(512)  NOT NULL,
    city_name       VARCHAR(255)  NOT NULL,
    country_name    VARCHAR(128)  NOT NULL,
    source_url      VARCHAR(768)  NOT NULL,
    wiki_title      VARCHAR(512)  NULL,
    resolved_title  VARCHAR(512)  NULL,
    page_id         INTEGER       NULL,
    revid           BIGINT        NULL,
    wikidata_qid    VARCHAR(16)   NULL,
    html            TEXT          NULL,
    fetched_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_wp_pois_source_url UNIQUE (source_url)
)
"""

CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_wp_pois_type_city "
    "ON wp_pois (poi_type, city_name)",
    "CREATE INDEX IF NOT EXISTS idx_wp_pois_country ON wp_pois (country_name)",
    "CREATE INDEX IF NOT EXISTS idx_wp_pois_type ON wp_pois (poi_type)",
)

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


def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        for stmt in CREATE_INDEXES:
            cur.execute(stmt)
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

    conn = connect_dict()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()
