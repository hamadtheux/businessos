from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


Environment = Literal["development", "testing", "staging", "production"]
StorageBackend = Literal["local", "s3"]
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


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
    database_url: SecretStr
    database_echo: bool = False
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=20, ge=0)
    database_pool_timeout: int = Field(default=30, ge=1)

    # Object storage
    storage_backend: StorageBackend = "local"
    storage_local_directory: Path = _BACKEND_ROOT / "storage"
    storage_bucket: str | None = None
    storage_region: str | None = None
    storage_endpoint_url: AnyHttpUrl | None = None
    storage_access_key_id: SecretStr | None = None
    storage_secret_access_key: SecretStr | None = None
    storage_public_base_url: AnyHttpUrl | None = None

    # Authentication
    auth_secret_key: SecretStr
    auth_algorithm: Literal["HS256"] = "HS256"
    auth_access_token_expire_minutes: int = Field(default=15, ge=1, le=60)
    auth_refresh_token_expire_days: int = Field(default=30, ge=1, le=90)
    auth_refresh_token_bytes: int = Field(default=48, ge=32, le=128)
    auth_refresh_cookie_name: str = Field(
        default="aibos_refresh",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    auth_refresh_cookie_secure: bool = False
    auth_refresh_cookie_samesite: Literal["lax", "strict"] = "lax"
    auth_issuer: str = Field(default="ai-business-os", min_length=1)
    auth_audience: str = Field(default="ai-business-os-api", min_length=1)

    # AI / OpenAI
    #
    # The API key is optional at application startup so non-AI commands,
    # migrations, tests, and development tooling can run without contacting
    # an external model provider. The OpenAI provider adapter will fail safely
    # when execution is requested without a configured key.
    openai_api_key: SecretStr | None = None

    openai_model: str = Field(
        default="gpt-5.6-terra",
        min_length=1,
        max_length=128,
    )

    openai_timeout_seconds: float = Field(
        default=45.0,
        gt=0,
        le=300,
    )

    openai_max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AIBOS_",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("auth_secret_key")
    @classmethod
    def validate_auth_secret_key_strength(
        cls,
        value: SecretStr,
    ) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError(
                "Authentication signing secret must be at least 32 bytes"
            )
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_credentialed_cors_origins(
        cls,
        value: list[str],
    ) -> list[str]:
        if any(origin.strip() == "*" for origin in value):
            raise ValueError(
                "Wildcard CORS origins cannot be used with credentials"
            )
        return value

    @field_validator("openai_api_key")
    @classmethod
    def validate_openai_api_key(
        cls,
        value: SecretStr | None,
    ) -> SecretStr | None:
        if value is None:
            return None

        if not value.get_secret_value().strip():
            raise ValueError(
                "OpenAI API key cannot be blank"
            )

        return value

    @field_validator("openai_model")
    @classmethod
    def normalize_openai_model(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "OpenAI model cannot be blank"
            )

        return normalized

    @model_validator(mode="after")
    def validate_production_security_settings(self) -> Self:
        if self.environment == "production" and self.debug:
            raise ValueError(
                "Debug mode must be disabled in production"
            )

        if (
            self.environment == "production"
            and not self.auth_refresh_cookie_secure
        ):
            raise ValueError(
                "Secure refresh cookies are required in production"
            )

        if self.environment in {"staging", "production"}:
            if self.storage_backend != "s3":
                raise ValueError(
                    "Durable S3-compatible storage is required "
                    "outside development"
                )

        if self.storage_backend == "s3":
            if (
                self.storage_bucket is None
                or not self.storage_bucket.strip()
                or self.storage_access_key_id is None
                or not self.storage_access_key_id.get_secret_value().strip()
                or self.storage_secret_access_key is None
                or not self.storage_secret_access_key.get_secret_value().strip()
                or self.storage_public_base_url is None
            ):
                raise ValueError(
                    "S3-compatible storage requires bucket, credentials, "
                    "and a public base URL"
                )

        return self

    @property
    def sqlalchemy_database_url(self) -> str:
        return self.database_url.get_secret_value()

    @property
    def openai_api_key_value(self) -> str | None:
        """
        Return the raw OpenAI key only for server-side provider construction.

        Callers must never log, serialize, or expose this value.
        """
        if self.openai_api_key is None:
            return None

        return self.openai_api_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()