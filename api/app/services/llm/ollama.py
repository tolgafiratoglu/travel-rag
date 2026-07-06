import asyncio

import httpx

from app.config import settings

_semaphore = asyncio.Semaphore(1)


class OllamaProvider:
    async def generate(
        self,
        prompt: str,
        *,
        stream: bool = False,
        model: str | None = None,
    ) -> str:
        model_name = model or settings.qwen_model
        async with _semaphore:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": model_name,
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

    async def _warmup_model(self, model: str) -> None:
        async with httpx.AsyncClient(timeout=300.0) as client:
            await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": "hi",
                    "think": False,
                    "stream": False,
                    "keep_alive": -1,
                    "options": {"num_ctx": 2048},
                },
            )

    async def warmup(self) -> None:
        async with _semaphore:
            await self._warmup_model(settings.qwen_model)
