import httpx

from app.config import settings


async def embed(text: str) -> list[float]:
    """Embed a text string using nomic-embed-text via Ollama."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{settings.ollama_base_url}/api/embed",
            json={
                "model": settings.ollama_embed_model,
                "input": text,
                "keep_alive": -1,
            },
        )
        resp.raise_for_status()
        return resp.json()["embeddings"][0]
