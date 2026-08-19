from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


Environment = Literal["development", "testing", "staging", "production"]


class Settings(BaseSettings):
    # Application
    app_name: str = "AI Business OS API"
    app_version: str = "0.1.0"
    environment: Environment = "development"
    debug: bool = True

    # API
    api_v1_prefix: str = "/api/v1"

    # Frontend / CORS
    cors_origins: list[str] = [
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]

    # Database
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://aibos:aibos@127.0.0.1:5432/aibos"
    )
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AIBOS_",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def sqlalchemy_database_url(self) -> str:
        return self.database_url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
