from __future__ import annotations

from typing import Any

from app.core.config import Settings


def build_asyncpg_connect_args(settings: Settings) -> dict[str, Any]:
    """
    Build the asyncpg connection arguments shared by application runtime
    engines and Alembic migrations.

    Keeping this configuration centralized prevents migrations from silently
    using weaker TLS, timeout, or session settings than the API/worker runtime.
    """
    return {
        "timeout": settings.database_connect_timeout_seconds,
        "command_timeout": settings.database_command_timeout_seconds,
        "ssl": settings.database_ssl_mode,
        "server_settings": {
            "application_name": f"ai-business-os-{settings.environment}",
            "timezone": "UTC",
        },
    }
