from qdrant_client import QdrantClient

from app.config import settings
from app.services.embeddings import embed


def _get_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


async def search(query: str, top_k: int = 5) -> list[dict]:
    """Embed the query and return the top-k matching chunks from Qdrant."""
    vector = await embed(query)
    client = _get_client()

    results = client.query_points(
        collection_name=settings.qdrant_collection,
        query=vector,
        limit=top_k,
        with_payload=True,
    )

    return [
        {
            "text": point.payload.get("text", ""),
            "city_name": point.payload.get("city_name", ""),
            "country_name": point.payload.get("country_name", ""),
            "section": point.payload.get("section")
            or point.payload.get("section_path", ""),
            "source_url": point.payload.get("source_url", ""),
            "source": point.payload.get("source", ""),
            "name": point.payload.get("name", ""),
            "poi_type": point.payload.get("poi_type", ""),
            "score": point.score,
        }
        for point in (results.points or [])
    ]
