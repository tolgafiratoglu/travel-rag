from collections.abc import AsyncIterator
from typing import Protocol


class LLMProvider(Protocol):
    async def generate(
        self,
        prompt: str,
        *,
        stream: bool = False,
        model: str | None = None,
    ) -> str: ...

    def stream_generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
    ) -> AsyncIterator[str]: ...

    async def warmup(self) -> None: ...
