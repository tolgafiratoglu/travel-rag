from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field
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

    llm_provider: Literal["gemini", "ollama"] = "gemini"
    gemini_model: str = "gemini-2.5-flash-lite"
    secrets_env_path: str = "~/.config/rag/.env"
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")

    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:8b"
    ollama_embed_model: str = "nomic-embed-text"

    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "travel_chunks"

    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme"


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
