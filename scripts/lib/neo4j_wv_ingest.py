"""Deprecated — use lib.neo4j_ingest (Wikipedia-sourced)."""

from lib.neo4j_ingest import *  # noqa: F403
from lib.neo4j_ingest import (  # noqa: F401
    ENTITY_LABEL,
    LLM_SOURCE,
    PAGE_SOURCE,
    IngestCounts,
    airport_graph_id,
    build_city_anchor,
    build_country_anchor,
    build_place_from_poi,
    delete_city_llm_derived,
    ensure_constraints,
    find_city_graph_id,
    upsert_city_graph,
    upsert_place_for_city,
    wipe_all_entities,
)

# Back-compat alias
delete_city_wv_derived = delete_city_llm_derived
