from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Travel RAG"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "info"

    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:8b"
    ollama_embed_model: str = "nomic-embed-text"

    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "travel_chunks"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
