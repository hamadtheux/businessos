from __future__ import annotations

import os
import unittest

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.main import app  # noqa: E402


class Phase7OpenApiSecurityTests(unittest.TestCase):
    def test_every_business_owned_operation_declares_authentication(self) -> None:
        schema = app.openapi()
        methods = {"get", "post", "put", "patch", "delete"}
        operations = []

        for path, path_item in schema["paths"].items():
            if not path.startswith("/api/v1/businesses/"):
                continue
            for method, operation in path_item.items():
                if method not in methods:
                    continue
                operations.append((method, path))
                self.assertTrue(
                    operation.get("security"),
                    f"{method.upper()} {path} lacks an authentication contract",
                )

        self.assertGreaterEqual(len(operations), 250)

    def test_integration_secrets_are_not_in_browser_facing_schemas(self) -> None:
        schema = app.openapi()
        protected_schema_names = (
            "ConnectorDefinitionResponse",
            "IntegrationConnectionResponse",
            "AuthorizationStartResponse",
            "AuthorizationCallbackResponse",
        )
        forbidden = {
            "access_token",
            "refresh_token",
            "client_secret",
            "credential_reference",
            "webhook_secret",
            "signing_secret",
            "api_key",
        }

        for name in protected_schema_names:
            properties = set(
                schema["components"]["schemas"][name].get("properties", {})
            )
            self.assertTrue(
                properties.isdisjoint(forbidden),
                f"{name} exposes a server-only integration field",
            )


if __name__ == "__main__":
    unittest.main()
