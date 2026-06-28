from typing import Protocol


class LLMProvider(Protocol):
    async def generate(self, prompt: str, *, stream: bool = False) -> str: ...

    async def warmup(self) -> None: ...
