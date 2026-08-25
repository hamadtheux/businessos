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
IntegrationCredentialBackend = Literal["disabled", "aws_secrets_manager"]
ChatbotRateLimitBackend = Literal["memory"]
BillingProviderBackend = Literal["disabled"]
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

    # PostgreSQL-backed internal processing. API, worker, and scheduler are
    # separate processes sharing the same database.
    job_poll_interval_seconds: float = Field(default=1.0, ge=0.25, le=60)
    job_batch_size: int = Field(default=10, ge=1, le=100)
    job_lease_seconds: int = Field(default=300, ge=10, le=900)
    worker_heartbeat_seconds: int = Field(default=15, ge=5, le=120)
    scheduler_poll_interval_seconds: float = Field(default=5.0, ge=1, le=300)
    scheduler_batch_size: int = Field(default=100, ge=1, le=500)

    # Billing is server-authoritative even while commercial checkout is not
    # configured. Platform administrators are identities, never tenant roles.
    billing_provider: BillingProviderBackend = "disabled"
    platform_admin_emails: list[str] = []

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

    # External integrations
    #
    # Provider tokens are never stored in application database columns. The
    # safe default is disabled; production may opt into AWS Secrets Manager
    # with workload identity and optional KMS configuration.
    integration_credential_backend: IntegrationCredentialBackend = "disabled"
    integration_secret_prefix: str = Field(
        default="aibos/integrations", min_length=3, max_length=80,
        pattern=r"^[A-Za-z0-9/_+=.@-]+$",
    )
    integration_secret_region: str | None = Field(default=None, max_length=64)
    integration_secret_kms_key_id: str | None = Field(default=None, max_length=2048)
    integration_oauth_state_ttl_seconds: int = Field(default=600, ge=300, le=900)
    integration_oauth_callback_url: AnyHttpUrl | None = None
    integration_webhook_max_bytes: int = Field(default=262_144, ge=1_024, le=1_048_576)
    external_connector_writes_enabled: bool = False
    connector_dispatch_timeout_seconds: float = Field(default=30.0, ge=1, le=120)

    google_oauth_client_id: str | None = Field(default=None, max_length=255)
    google_oauth_client_secret: SecretStr | None = None
    google_ads_developer_token: SecretStr | None = None
    google_ads_api_version: str = Field(default="v25", pattern=r"^v[0-9]{1,3}$")
    meta_oauth_client_id: str | None = Field(default=None, max_length=255)
    meta_oauth_client_secret: SecretStr | None = None
    meta_graph_api_version: str = Field(default="v26.0", pattern=r"^v[0-9]{1,3}\.0$")
    meta_webhook_verify_token: SecretStr | None = None
    meta_webhook_signing_secret: SecretStr | None = None
    microsoft_oauth_client_id: str | None = Field(default=None, max_length=255)
    microsoft_oauth_client_secret: SecretStr | None = None

    # Public website chatbot / isolated widget deployment
    public_api_base_url: AnyHttpUrl = "http://localhost:8000"
    # Keep the local public surfaces on the same origin as the development
    # frontend.  The Vite app is served on 5174 throughout this repository
    # (including the credentialed CORS defaults); pointing these URLs at 5173
    # can silently send hosted-chat links to an unrelated local process.
    widget_loader_url: AnyHttpUrl = "http://localhost:5174/widget-loader.js"
    widget_app_url: AnyHttpUrl = "http://localhost:5174/widget.html"
    chatbot_session_ttl_minutes: int = Field(default=1_440, ge=15, le=10_080)
    chatbot_rate_limit_backend: ChatbotRateLimitBackend = "memory"
    chatbot_session_creations_per_minute: int = Field(default=10, ge=1, le=100)
    chatbot_messages_per_minute: int = Field(default=20, ge=1, le=120)
    chatbot_leads_per_hour: int = Field(default=5, ge=1, le=50)
    chatbot_order_attempts_per_hour: int = Field(default=5, ge=1, le=50)
    chatbot_booking_attempts_per_hour: int = Field(default=5, ge=1, le=50)

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

    @field_validator("platform_admin_emails")
    @classmethod
    def normalize_platform_admin_emails(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip().casefold() for item in value if item.strip()})
        if any("@" not in item or len(item) > 320 for item in normalized):
            raise ValueError("Platform administrator emails must be valid email identities")
        return normalized

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

        if self.integration_credential_backend == "aws_secrets_manager":
            if not self.integration_secret_region or not self.integration_secret_region.strip():
                raise ValueError(
                    "AWS Secrets Manager integration storage requires a region"
                )
        if (
            self.external_connector_writes_enabled
            and self.integration_credential_backend == "disabled"
        ):
            raise ValueError(
                "External connector writes require secure credential storage"
            )

        for provider, client_id, client_secret in (
            ("Google", self.google_oauth_client_id, self.google_oauth_client_secret),
            ("Meta", self.meta_oauth_client_id, self.meta_oauth_client_secret),
            ("Microsoft", self.microsoft_oauth_client_id, self.microsoft_oauth_client_secret),
        ):
            has_id = bool(client_id and client_id.strip())
            has_secret = bool(
                client_secret and client_secret.get_secret_value().strip()
            )
            if has_id != has_secret:
                raise ValueError(f"{provider} OAuth client configuration is incomplete")
            if has_id and (
                self.integration_oauth_callback_url is None
                or self.integration_credential_backend == "disabled"
            ):
                raise ValueError(
                    f"{provider} OAuth requires a callback URL and secure credential storage"
                )

        if (
            self.google_ads_developer_token is not None
            and not self.google_ads_developer_token.get_secret_value().strip()
        ):
            raise ValueError("Google Ads developer token cannot be blank")

        if self.environment == "production":
            public_urls = (
                self.public_api_base_url,
                self.widget_loader_url,
                self.widget_app_url,
            )
            if any(url.scheme != "https" or url.host in {"localhost", "127.0.0.1"} for url in public_urls):
                raise ValueError(
                    "Public chatbot API, loader, and app URLs must use non-local HTTPS in production"
                )
            origins = {
                (url.scheme, url.host, url.port or 443)
                for url in public_urls
            }
            if len(origins) != 1:
                raise ValueError(
                    "Public chatbot API, loader, and app must share one browser origin; proxy /api to the backend"
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
