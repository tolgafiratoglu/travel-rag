import asyncio

import httpx

from app.config import settings

_semaphore = asyncio.Semaphore(1)


async def generate(prompt: str, *, stream: bool = False) -> str:
    """Send a prompt to Qwen via Ollama and return the response text.

    Uses a Semaphore(1) so only one LLM call runs at a time — Ollama
    on a single GPU handles concurrent requests poorly.
    """
    async with _semaphore:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "keep_alive": -1,
                    "options": {
                        "num_ctx": 2048,
                        "temperature": 0.3,
                        "top_p": 0.9,
                    },
                },
            )
            resp.raise_for_status()
            return resp.json()["response"]


async def warmup():
    """Load the model into memory so the first real request is fast."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        await client.post(
            f"{settings.ollama_base_url}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": "hi",
                "think": False,
                "stream": False,
                "keep_alive": -1,
                "options": {"num_ctx": 2048},
            },
        )
