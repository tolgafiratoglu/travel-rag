"""PostgreSQL access for Wikipedia city/guide pages + gov reference links."""

from __future__ import annotations

from lib.pg import DB_CONFIG, connect, connect_dict

CREATE_PAGES_SQL = """
CREATE TABLE IF NOT EXISTS wp_pages (
    id              BIGSERIAL PRIMARY KEY,
    page_kind       TEXT NOT NULL DEFAULT 'city'
        CHECK (page_kind IN ('city', 'tourism', 'attraction_list')),
    city_name       VARCHAR(255)  NOT NULL,
    country_name    VARCHAR(128)  NOT NULL,
    source_url      VARCHAR(768)  NOT NULL,
    wiki_title      VARCHAR(512)  NULL,
    resolved_title  VARCHAR(512)  NULL,
    title           VARCHAR(512)  NULL,
    page_id         INTEGER       NULL,
    revid           BIGINT        NULL,
    wikidata_qid    VARCHAR(16)   NULL,
    html            TEXT          NULL,
    fetched_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_wp_pages_source_url UNIQUE (source_url)
)
"""

CREATE_PAGES_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_wp_pages_city ON wp_pages (city_name)",
    "CREATE INDEX IF NOT EXISTS idx_wp_pages_country ON wp_pages (country_name)",
    "CREATE INDEX IF NOT EXISTS idx_wp_pages_kind ON wp_pages (page_kind)",
)

CREATE_GOV_SQL = """
CREATE TABLE IF NOT EXISTS wp_gov_links (
    id              BIGSERIAL PRIMARY KEY,
    country_name    VARCHAR(128)  NOT NULL,
    city_name       VARCHAR(255)  NULL,
    page_url        VARCHAR(768)  NOT NULL,
    link_url        VARCHAR(768)  NOT NULL,
    link_text       VARCHAR(512)  NULL,
    domain          VARCHAR(255)  NULL,
    fetched_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_wp_gov_page_link UNIQUE (page_url, link_url)
)
"""

CREATE_GOV_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_wp_gov_city ON wp_gov_links (city_name)",
    "CREATE INDEX IF NOT EXISTS idx_wp_gov_domain ON wp_gov_links (domain)",
)

CREATE_SECTION_STATS_SQL = """
CREATE TABLE IF NOT EXISTS wp_section_stats (
    id            BIGSERIAL PRIMARY KEY,
    section       VARCHAR(512)  NOT NULL,
    chunk_count   INTEGER       NOT NULL DEFAULT 0,
    page_count    INTEGER       NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_wp_section_stats_section UNIQUE (section)
)
"""

CREATE_SECTION_STATS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_wp_section_stats_chunk "
    "ON wp_section_stats (chunk_count)",
)

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


def ensure_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(CREATE_PAGES_SQL)
        for stmt in CREATE_PAGES_INDEXES:
            cur.execute(stmt)
        cur.execute(CREATE_GOV_SQL)
        for stmt in CREATE_GOV_INDEXES:
            cur.execute(stmt)
        cur.execute(CREATE_SECTION_STATS_SQL)
        for stmt in CREATE_SECTION_STATS_INDEXES:
            cur.execute(stmt)
    conn.commit()


def ensure_section_stats_table() -> None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_SECTION_STATS_SQL)
            for stmt in CREATE_SECTION_STATS_INDEXES:
                cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def reset_section_stats() -> None:
    """Clear global section counters (call when wiping travel_chunks)."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_SECTION_STATS_SQL)
            for stmt in CREATE_SECTION_STATS_INDEXES:
                cur.execute(stmt)
            cur.execute("TRUNCATE TABLE wp_section_stats")
        conn.commit()
    finally:
        conn.close()


def bump_section_stats(chunks: list[dict]) -> int:
    """Increment chunk_count / page_count from one page's chunks. Returns distinct sections."""
    from collections import Counter

    counts: Counter[str] = Counter()
    for chunk in chunks:
        section = (chunk.get("section") or chunk.get("section_path") or "").strip()
        if not section:
            continue
        counts[section] += 1
    if not counts:
        return 0

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_SECTION_STATS_SQL)
            for stmt in CREATE_SECTION_STATS_INDEXES:
                cur.execute(stmt)
            for section, n in counts.items():
                cur.execute(
                    """
                    INSERT INTO wp_section_stats (section, chunk_count, page_count)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (section) DO UPDATE SET
                        chunk_count = wp_section_stats.chunk_count
                            + EXCLUDED.chunk_count,
                        page_count = wp_section_stats.page_count + 1,
                        updated_at = NOW()
                    """,
                    (section[:512], int(n)),
                )
        conn.commit()
    finally:
        conn.close()
    return len(counts)


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

    conn = connect_dict()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()
