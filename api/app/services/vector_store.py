from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from app.config import settings
from app.services.embeddings import embed

# Planner / user spellings → payload city_name values stored at ingest.
_CITY_ALIASES: dict[str, list[str]] = {
    "istanbul": ["Istanbul", "İstanbul"],
    "i̇stanbul": ["Istanbul", "İstanbul"],
    "antalya": ["Antalya"],
}


def _get_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def _payload_dict(point) -> dict:
    payload = point.payload or {}
    return {
        "text": payload.get("text", ""),
        "city_name": payload.get("city_name", ""),
        "country_name": payload.get("country_name", ""),
        "section": payload.get("section") or payload.get("section_path", ""),
        "source_url": payload.get("source_url", ""),
        "source": payload.get("source", ""),
        "name": payload.get("name", ""),
        "title": payload.get("title", ""),
        "wiki_title": payload.get("wiki_title", ""),
        "page_kind": payload.get("page_kind", ""),
        "poi_type": payload.get("poi_type", ""),
        "score": point.score,
    }


def _city_filter_values(city: str) -> list[str]:
    """Expand a city label to match variants stored in Qdrant payloads."""
    name = city.strip()
    if not name:
        return []
    aliases = _CITY_ALIASES.get(name.casefold())
    if aliases:
        return list(dict.fromkeys([*aliases, name]))
    return [name]


def _build_city_filter(city: str | None) -> Filter | None:
    if not city:
        return None
    values = _city_filter_values(city)
    if not values:
        return None
    if len(values) == 1:
        return Filter(
            must=[FieldCondition(key="city_name", match=MatchValue(value=values[0]))]
        )
    return Filter(
        must=[FieldCondition(key="city_name", match=MatchAny(any=values))]
    )


async def search(
    query: str,
    top_k: int = 5,
    *,
    collection: str | None = None,
    city: str | None = None,
    dedupe_section: bool = True,
) -> list[dict]:
    """Embed the query and return the top-k matching chunks from Qdrant.

    When ``city`` is set, restrict results to that city_name (and known aliases).
    When ``city`` is None (planner ANY), no metadata filter is applied.
    When ``dedupe_section`` is true (default), keep at most one hit per
    ``source_url`` + section leaf so duplicate chunks do not fill top-k.
    """
    vector = await embed(query)
    client = _get_client()
    collection_name = collection or settings.qdrant_collection
    query_filter = _build_city_filter(city)
    fetch_limit = max(top_k * 4, top_k) if dedupe_section else top_k

    results = client.query_points(
        collection_name=collection_name,
        query=vector,
        query_filter=query_filter,
        limit=fetch_limit,
        with_payload=True,
    )

    points = results.points or []
    if not dedupe_section:
        return [_payload_dict(point) for point in points[:top_k]]

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for point in points:
        payload = point.payload or {}
        url = (payload.get("source_url") or "").strip().lower()
        section = (
            payload.get("section") or payload.get("section_path") or ""
        ).strip()
        leaf = section.split("/")[-1].strip().lower()
        key = (url, leaf)
        if key in seen:
            continue
        seen.add(key)
        out.append(_payload_dict(point))
        if len(out) >= top_k:
            break
    return out
