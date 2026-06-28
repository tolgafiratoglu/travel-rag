from app.config import settings
from app.services.llm.gemini import GeminiProvider
from app.services.llm.ollama import OllamaProvider

_provider: GeminiProvider | OllamaProvider | None = None


def _get_provider() -> GeminiProvider | OllamaProvider:
    global _provider
    if _provider is None:
        if settings.llm_provider == "gemini":
            _provider = GeminiProvider()
        else:
            _provider = OllamaProvider()
    return _provider


async def generate(prompt: str, *, stream: bool = False) -> str:
    return await _get_provider().generate(prompt, stream=stream)


async def warmup() -> None:
    await _get_provider().warmup()
