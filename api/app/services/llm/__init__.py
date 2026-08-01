from collections.abc import AsyncIterator

from app.config import settings
from app.services.llm.gemini import GeminiProvider
from app.services.llm.ollama import OllamaProvider

_provider: GeminiProvider | OllamaProvider | None = None


def _get_provider() -> GeminiProvider | OllamaProvider:
    global _provider
    if _provider is None:
        if settings.llm_provider == "gemini":
            _provider = GeminiProvider()
        elif settings.llm_provider == "qwen":
            _provider = OllamaProvider()
        else:
            raise RuntimeError(f"Unsupported LLM provider: {settings.llm_provider}")
    return _provider


async def generate(
    prompt: str,
    *,
    stream: bool = False,
    model: str | None = None,
) -> str:
    return await _get_provider().generate(prompt, stream=stream, model=model)


async def stream_generate(
    prompt: str,
    *,
    model: str | None = None,
) -> AsyncIterator[str]:
    async for delta in _get_provider().stream_generate(prompt, model=model):
        yield delta


async def warmup() -> None:
    await _get_provider().warmup()
