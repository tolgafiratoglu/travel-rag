from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Tourism GraphRAG"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
