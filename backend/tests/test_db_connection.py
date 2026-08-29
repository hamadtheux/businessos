from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.core.config import Settings  # noqa: E402
from app.db.connection import build_asyncpg_connect_args  # noqa: E402


class DatabaseConnectionConfigTests(unittest.TestCase):
    def test_asyncpg_connection_args_preserve_security_and_runtime_limits(self) -> None:
        settings = Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://database.invalid/test",
            auth_secret_key="A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2W3x4Y5z6",
            environment="production",
            debug=False,
            database_ssl_mode="verify-full",
            database_connect_timeout_seconds=7,
            database_command_timeout_seconds=45,
            docs_enabled=False,
            log_format="json",
            edge_rate_limiting_enabled=True,
            trusted_hosts=["widgets.example.test"],
            frontend_base_url="https://widgets.example.test",
            cors_origins=["https://widgets.example.test"],
            public_api_base_url="https://widgets.example.test",
            widget_loader_url="https://widgets.example.test/widget-loader.js",
            widget_app_url="https://widgets.example.test/widget.html",
            storage_backend="s3",
            storage_bucket="business-assets",
            storage_region="auto",
            storage_endpoint_url="https://objects.example.test",
            storage_access_key_id="storage-access-key",
            storage_secret_access_key="storage-secret-key",
            storage_public_base_url="https://cdn.example.test",
            auth_refresh_cookie_secure=True,
        )

        connect_args = build_asyncpg_connect_args(settings)

        self.assertEqual(connect_args["timeout"], 7)
        self.assertEqual(connect_args["command_timeout"], 45)
        self.assertEqual(connect_args["ssl"], "verify-full")
        self.assertEqual(
            connect_args["server_settings"],
            {
                "application_name": "ai-business-os-production",
                "timezone": "UTC",
            },
        )

    def test_runtime_and_alembic_both_use_shared_connection_builder(self) -> None:
        session_source = Path("app/db/session.py").read_text()
        alembic_source = Path("alembic/env.py").read_text()

        self.assertIn(
            "build_asyncpg_connect_args(settings)",
            session_source,
        )
        self.assertIn(
            "connect_args=build_asyncpg_connect_args(settings)",
            alembic_source,
        )


if __name__ == "__main__":
    unittest.main()
