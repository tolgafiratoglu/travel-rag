from qdrant_client import QdrantClient

from app.config import settings
from app.services.embeddings import embed


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
        "poi_type": payload.get("poi_type", ""),
        "score": point.score,
    }


async def search(
    query: str,
    top_k: int = 5,
    *,
    collection: str | None = None,
) -> list[dict]:
    """Embed the query and return the top-k matching chunks from Qdrant."""
    vector = await embed(query)
    client = _get_client()
    collection_name = collection or settings.qdrant_collection

    results = client.query_points(
        collection_name=collection_name,
        query=vector,
        limit=top_k,
        with_payload=True,
    )

    return [_payload_dict(point) for point in (results.points or [])]
