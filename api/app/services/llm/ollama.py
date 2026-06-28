import asyncio

import httpx

from app.config import settings

_semaphore = asyncio.Semaphore(1)


class OllamaProvider:
    async def generate(self, prompt: str, *, stream: bool = False) -> str:
        async with _semaphore:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_model,
                        "prompt": prompt,
                        "stream": stream,
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

    async def warmup(self) -> None:
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
