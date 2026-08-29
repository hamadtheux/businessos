from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

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
DatabaseSslMode = Literal["disable", "prefer", "require", "verify-ca", "verify-full"]
ExternalConnectorWriteMode = Literal["disabled", "test", "enabled"]
LogFormat = Literal["text", "json"]
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _origin(url: AnyHttpUrl) -> str:
    default_port = 443 if url.scheme == "https" else 80
    port = url.port or default_port
    suffix = "" if port == default_port else f":{port}"
    return f"{url.scheme}://{url.host}{suffix}"


def _is_local_or_private_host(host: str) -> bool:
    normalized = host.rstrip(".").casefold()
    if normalized == "localhost" or normalized.endswith((".localhost", ".local")):
        return True
    try:
        return not ip_address(normalized.strip("[]")).is_global
    except ValueError:
        return False


class Settings(BaseSettings):
    # Application
    app_name: str = "AI Business OS API"
    app_version: str = "0.1.0"
    environment: Environment = "development"
    debug: bool = True

    # API
    api_v1_prefix: str = "/api/v1"
    docs_enabled: bool = True
    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    request_max_bytes: int = Field(default=2_097_152, ge=65_536, le=10_485_760)

    # Frontend / CORS
    frontend_base_url: AnyHttpUrl = "http://localhost:5174"
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
    database_pool_recycle_seconds: int = Field(default=1_800, ge=60, le=86_400)
    database_connect_timeout_seconds: int = Field(default=10, ge=1, le=60)
    database_command_timeout_seconds: int = Field(default=60, ge=1, le=600)
    database_ssl_mode: DatabaseSslMode = "disable"

    # Observability and ingress contracts. The edge flag represents a real
    # reverse-proxy/WAF rate-limit policy; the application never pretends its
    # single-process chatbot limiter protects a multi-replica deployment.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: LogFormat = "text"
    edge_rate_limiting_enabled: bool = False
    # Operator-controlled kill switch for first-client activation. Production
    # configuration may be deployed safely with this disabled; the
    # tenant-scoped activation readiness endpoint will not report ready until
    # an operator enables it after deployment, backup, monitoring, and smoke
    # gates have been completed.
    first_client_activation_enabled: bool = False

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
    external_connector_write_mode: ExternalConnectorWriteMode = "disabled"
    # Deprecated compatibility switch. Both values are normalized and must
    # describe the same trusted server-side decision.
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
        normalized: list[str] = []
        for candidate in value:
            origin = candidate.strip().rstrip("/")
            parsed = urlsplit(origin)
            if (
                origin == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "CORS origins must be exact HTTP(S) origins without credentials or paths"
                )
            normalized.append(origin)
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("CORS origins must be non-empty and unique")
        return normalized

    @field_validator("trusted_hosts")
    @classmethod
    def validate_trusted_hosts(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().casefold().rstrip(".") for item in value]
        if (
            not normalized
            or len(set(normalized)) != len(normalized)
            or any(
                not item
                or "/" in item
                or "://" in item
                or item.startswith(".")
                for item in normalized
            )
        ):
            raise ValueError("Trusted hosts must be unique hostnames")
        return normalized

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
        if self.external_connector_writes_enabled != (
            self.external_connector_write_mode != "disabled"
        ):
            raise ValueError(
                "External connector write mode and compatibility switch must agree"
            )

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
                or self.storage_region is None
                or not self.storage_region.strip()
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
            if self.docs_enabled:
                raise ValueError("Interactive API documentation must be disabled in production")
            if self.database_ssl_mode not in {"require", "verify-ca", "verify-full"}:
                raise ValueError("PostgreSQL TLS is required in production")
            if self.log_format != "json":
                raise ValueError("Structured JSON logging is required in production")
            if not self.edge_rate_limiting_enabled:
                raise ValueError("A production edge rate-limit policy must be confirmed")
            if any("*" in host for host in self.trusted_hosts):
                raise ValueError("Wildcard trusted hosts are forbidden in production")

            database_url = urlsplit(self.sqlalchemy_database_url)
            if database_url.scheme != "postgresql+asyncpg" or not database_url.hostname:
                raise ValueError(
                    "Production database URL must use postgresql+asyncpg with an explicit host"
                )

            public_urls = (
                self.frontend_base_url,
                self.public_api_base_url,
                self.widget_loader_url,
                self.widget_app_url,
            )
            if any(
                url.scheme != "https" or _is_local_or_private_host(url.host)
                for url in public_urls
            ):
                raise ValueError(
                    "Public chatbot API, loader, and app URLs must use non-local HTTPS in production"
                )
            if any(
                url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
                or url.path not in {"", "/", "/widget-loader.js", "/widget.html"}
                for url in public_urls
            ):
                raise ValueError(
                    "Production public URLs must not contain credentials, query strings, fragments, or unexpected paths"
                )
            storage_public = self.storage_public_base_url
            storage_endpoint = self.storage_endpoint_url
            if (
                storage_public is None
                or storage_public.scheme != "https"
                or _is_local_or_private_host(storage_public.host)
                or storage_public.query
                or storage_public.fragment
                or (
                    storage_endpoint is not None
                    and (
                        storage_endpoint.scheme != "https"
                        or _is_local_or_private_host(storage_endpoint.host)
                        or storage_endpoint.query
                        or storage_endpoint.fragment
                    )
                )
            ):
                raise ValueError(
                    "Production object storage URLs must use non-local HTTPS without query or fragment"
                )
            widget_origins = {
                (url.scheme, url.host, url.port or 443)
                for url in (
                    self.public_api_base_url,
                    self.widget_loader_url,
                    self.widget_app_url,
                )
            }
            if len(widget_origins) != 1:
                raise ValueError(
                    "Public chatbot API, loader, and app must share one browser origin; proxy /api to the backend"
                )
            frontend_origin = _origin(self.frontend_base_url)
            if frontend_origin not in self.cors_origins:
                raise ValueError("The production frontend origin must be explicitly allowed by CORS")
            public_api_host = self.public_api_base_url.host.casefold()
            if public_api_host not in self.trusted_hosts:
                raise ValueError("The public API hostname must be explicitly trusted")
            if any(
                urlsplit(origin).scheme != "https"
                or _is_local_or_private_host(urlsplit(origin).hostname or "")
                for origin in self.cors_origins
            ):
                raise ValueError("Production CORS origins must use non-local HTTPS")
            secret = self.auth_secret_key.get_secret_value().strip().casefold()
            weak_markers = ("change-me", "changeme", "development", "example", "test-secret")
            if any(marker in secret for marker in weak_markers) or len(set(secret)) < 8:
                raise ValueError("Production authentication signing secret is not sufficiently unique")

            configured_secrets = {
                "OpenAI API key": self.openai_api_key,
                "storage access key": self.storage_access_key_id,
                "storage secret key": self.storage_secret_access_key,
                "Google OAuth client secret": self.google_oauth_client_secret,
                "Google Ads developer token": self.google_ads_developer_token,
                "Meta OAuth client secret": self.meta_oauth_client_secret,
                "Meta webhook verify token": self.meta_webhook_verify_token,
                "Meta webhook signing secret": self.meta_webhook_signing_secret,
                "Microsoft OAuth client secret": self.microsoft_oauth_client_secret,
            }
            unsafe_secret_markers = (
                "change-me", "changeme", "placeholder", "not-a-real",
                "example-secret", "dummy-secret", "sk-test-",
            )
            for label, configured_secret in configured_secrets.items():
                if configured_secret is None:
                    continue
                normalized = configured_secret.get_secret_value().strip().casefold()
                if any(marker in normalized for marker in unsafe_secret_markers):
                    raise ValueError(f"{label} contains a non-production placeholder")

            if self.integration_oauth_callback_url is not None:
                callback = self.integration_oauth_callback_url
                if (
                    callback.scheme != "https"
                    or callback.host in {"localhost", "127.0.0.1"}
                    or _origin(callback) != _origin(self.public_api_base_url)
                    or callback.path != f"{self.api_v1_prefix}/integrations/oauth/callback"
                    or callback.query
                    or callback.fragment
                ):
                    raise ValueError(
                        "Production OAuth callback must be the HTTPS public API /integrations/oauth/callback endpoint"
                    )

            if self.meta_oauth_client_id and (
                self.meta_webhook_verify_token is None
                or self.meta_webhook_signing_secret is None
            ):
                raise ValueError(
                    "Configured Meta OAuth requires webhook verification and signing secrets"
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
