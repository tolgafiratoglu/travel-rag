from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Travel RAG"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "info"

    llm_provider: Literal["gemini", "qwen"] = "gemini"
    gemini_model: str = "gemini-2.5-flash-lite"
    secrets_env_path: str = "~/.config/rag/.env"
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")

    ollama_base_url: str = "http://ollama:11434"
    qwen_llm: Literal["1b", "8b"] = "1b"
    qwen8b: str = "qwen3:8b"
    qwen1b: str = "qwen3:1.7b-q4_K_M"
    ollama_embed_model: str = "bge-m3"
    ollama_guard_model: str = "sileader/qwen3guard:0.6b"

    qdrant_url: str = "http://qdrant:6333"
    # Runtime: use read-only key when Qdrant auth is enabled. Empty = no auth header.
    qdrant_api_key: str = ""
    qdrant_collection: str = "travel_chunks"
    qdrant_collection_museums: str = "travel_museums"
    qdrant_collection_historical: str = "travel_historical_sites"

    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme"

    rate_limit_enabled: bool = True
    rate_limit_burst: int = 5
    rate_limit_burst_window_seconds: int = 60
    rate_limit_hourly: int = 30

    # Hard cap on the current user message (chars), applied in intake.
    max_question_chars: int = 8000
    # Client chat history (attached under the question when include_history).
    max_history_turns: int = 6
    max_history_message_chars: int = 1000
    max_history_total_chars: int = 4000

    @field_validator("llm_provider", mode="before")
    @classmethod
    def _normalize_llm_provider(cls, value: str) -> str:
        if value == "ollama":
            return "qwen"
        return value

    @property
    def qwen_model(self) -> str:
        return self.qwen1b if self.qwen_llm == "1b" else self.qwen8b


def _load_gemini_api_key(settings: Settings) -> Settings:
    if settings.gemini_api_key:
        return settings
    path = Path(settings.secrets_env_path).expanduser()
    if not path.is_file():
        return settings
    key = (dotenv_values(path).get("GEMINI_API_KEY") or "").strip()
    if key:
        return settings.model_copy(update={"gemini_api_key": key})
    return settings


settings = _load_gemini_api_key(Settings())
