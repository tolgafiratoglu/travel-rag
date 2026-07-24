"""Qwen3Guard input moderation via a dedicated Ollama client."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_SAFE_PATTERN = re.compile(r"Safety:\s*(Safe|Unsafe|Controversial)", re.IGNORECASE)
_CATEGORY_PATTERN = re.compile(
    r"(Violent|Non-violent Illegal Acts|Sexual Content or Sexual Acts|PII|"
    r"Suicide & Self-Harm|Unethical Acts|Politically Sensitive Topics|"
    r"Copyright Violation|Jailbreak|None)"
)

REJECT_MESSAGE = "I can't process this request."


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    label: str | None
    categories: list[str]
    raw: str


class GuardClient:
    """Own Ollama HTTP client for the guard model (not shared with LLM provider)."""

    def __init__(self) -> None:
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = settings.ollama_guard_model

    async def check_prompt(self, text: str) -> GuardResult:
        raw = await self._chat(text)
        label, categories = _parse_guard_output(raw)
        # Only exact Safe is allowed; Unsafe, Controversial, or unparseable → block.
        allowed = label is not None and label.lower() == "safe"
        return GuardResult(
            allowed=allowed,
            label=label,
            categories=categories,
            raw=raw,
        )

    async def _chat(self, user_text: str) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": user_text}],
                    "stream": False,
                    "think": False,
                    "keep_alive": -1,
                    "options": {
                        "temperature": 0.0,
                        "num_predict": 128,
                    },
                },
            )
            resp.raise_for_status()
            body = resp.json()
            message = body.get("message") or {}
            content = message.get("content") or body.get("response") or ""
            return str(content).strip()

    async def warmup(self) -> None:
        try:
            await self._chat("hi")
            logger.info("Guard model warmed (%s, keep_alive=-1)", self._model)
        except Exception:
            logger.exception("Guard model warmup failed (%s)", self._model)


_client: GuardClient | None = None


def get_guard_client() -> GuardClient:
    global _client
    if _client is None:
        _client = GuardClient()
    return _client


def _parse_guard_output(content: str) -> tuple[str | None, list[str]]:
    match = _SAFE_PATTERN.search(content or "")
    label = match.group(1) if match else None
    categories = _CATEGORY_PATTERN.findall(content or "")
    return label, categories
