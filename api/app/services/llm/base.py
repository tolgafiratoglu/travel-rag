from typing import Protocol


class LLMProvider(Protocol):
    async def generate(
        self,
        prompt: str,
        *,
        stream: bool = False,
        model: str | None = None,
    ) -> str: ...

    async def warmup(self) -> None: ...
