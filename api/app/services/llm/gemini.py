from collections.abc import AsyncIterator

from google import genai
from google.genai import types

from app.config import settings

_client: genai.Client | None = None
_MAX_OUTPUT_TOKENS = 4096


def _generation_config() -> types.GenerateContentConfig:
    # Gemini 3.x: do not set temperature/top_p/top_k (deprecated → future 400).
    # Flash-Lite: thinking_level=minimal keeps latency low for travel RAG.
    return types.GenerateContentConfig(
        max_output_tokens=_MAX_OUTPUT_TOKENS,
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
    )


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Add it to "
                f"{settings.secrets_env_path} or the environment."
            )
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _extract_text(response) -> str:
    if not response.candidates:
        raise RuntimeError("Gemini returned no candidates")
    text = response.text
    if not text:
        raise RuntimeError("Gemini returned empty text")
    return text


class GeminiProvider:
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

        client = _get_client()
        response = await client.aio.models.generate_content(
            model=model or settings.gemini_model,
            contents=prompt,
            config=_generation_config(),
        )
        return _extract_text(response)

    async def stream_generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        client = _get_client()
        async for chunk in await client.aio.models.generate_content_stream(
            model=model or settings.gemini_model,
            contents=prompt,
            config=_generation_config(),
        ):
            text = getattr(chunk, "text", None)
            if text:
                yield text

    async def warmup(self) -> None:
        pass
