import asyncio
import json
from collections.abc import AsyncIterator

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
        if stream:
            parts: list[str] = []
            async for delta in self.stream_generate(prompt, model=model):
                parts.append(delta)
            return "".join(parts)

        model_name = model or settings.qwen_model
        async with _semaphore:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": model_name,
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

    async def stream_generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        model_name = model or settings.qwen_model
        async with _semaphore:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST",
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": model_name,
                        "prompt": prompt,
                        "stream": True,
                        "think": False,
                        "keep_alive": -1,
                        "options": {
                            "num_ctx": 2048,
                            "temperature": 0.3,
                            "top_p": 0.9,
                        },
                    },
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        piece = data.get("response") or ""
                        if piece:
                            yield piece
                        if data.get("done"):
                            break

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
