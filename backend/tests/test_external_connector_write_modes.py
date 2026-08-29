from __future__ import annotations

import os
import unittest
from uuid import uuid4

from pydantic import ValidationError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault(
    "AIBOS_AUTH_SECRET_KEY",
    "x" * 32,
)

from app.core.config import Settings  # noqa: E402
from app.domain.integrations import (  # noqa: E402
    ExternalConnectorWritesDisabledError,
)
from app.exceptions.commerce import (  # noqa: E402
    CommerceConfigurationRequiredError,
)
from app.integrations.action_boundary import (  # noqa: E402
    prepare_connector_dispatch_context,
)
from app.integrations.provider_action_adapters import (  # noqa: E402
    build_configured_action_adapters,
)
from app.integrations.registry import CONNECTOR_REGISTRY  # noqa: E402
from app.services.ad_commerce import _require_feed_writes  # noqa: E402


TEST_DATABASE_URL = "postgresql+asyncpg://database.invalid/test"
TEST_AUTH_SECRET = "external-write-mode-test-secret-with-more-than-32-bytes"


def _settings(mode: str) -> Settings:
    writes_enabled = mode != "disabled"

    return Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        auth_secret_key=TEST_AUTH_SECRET,
        integration_credential_backend="aws_secrets_manager",
        integration_secret_region="us-east-1",
        integration_oauth_callback_url=(
            "https://api.example.test/api/v1/integrations/oauth/callback"
        ),
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret="google-client-secret",
        google_ads_developer_token="google-developer-token",
        external_connector_writes_enabled=writes_enabled,
        external_connector_write_mode=mode,
    )


class ExternalConnectorWriteModeSettingsTests(unittest.TestCase):
    def test_three_mode_configuration_contract(self) -> None:
        disabled = _settings("disabled")
        test_mode = _settings("test")
        enabled = _settings("enabled")

        self.assertFalse(disabled.external_connector_writes_enabled)
        self.assertEqual(disabled.external_connector_write_mode, "disabled")

        self.assertTrue(test_mode.external_connector_writes_enabled)
        self.assertEqual(test_mode.external_connector_write_mode, "test")

        self.assertTrue(enabled.external_connector_writes_enabled)
        self.assertEqual(enabled.external_connector_write_mode, "enabled")

    def test_test_or_enabled_mode_requires_compatibility_switch(self) -> None:
        for mode in ("test", "enabled"):
            with self.subTest(mode=mode):
                with self.assertRaises(ValidationError):
                    Settings(
                        _env_file=None,
                        database_url=TEST_DATABASE_URL,
                        auth_secret_key=TEST_AUTH_SECRET,
                        external_connector_writes_enabled=False,
                        external_connector_write_mode=mode,
                    )

    def test_compatibility_switch_cannot_silently_promote_disabled_mode(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                _env_file=None,
                database_url=TEST_DATABASE_URL,
                auth_secret_key=TEST_AUTH_SECRET,
                external_connector_writes_enabled=True,
                external_connector_write_mode="disabled",
            )

    def test_oauth_write_scopes_are_absent_when_disabled(self) -> None:
        gmail = CONNECTOR_REGISTRY["gmail"]

        disabled = gmail.requested_oauth_scopes("disabled")
        test_mode = gmail.requested_oauth_scopes("test")
        enabled = gmail.requested_oauth_scopes("enabled")

        send_scope = "https://www.googleapis.com/auth/gmail.send"
        readonly_scope = "https://www.googleapis.com/auth/gmail.readonly"

        self.assertIn(readonly_scope, disabled)
        self.assertNotIn(send_scope, disabled)

        # Test mode may request the write permission so a controlled provider
        # authorization can be validated, but the mutation boundary below
        # must still prevent any real provider write.
        self.assertIn(send_scope, test_mode)
        self.assertIn(send_scope, enabled)

    def test_real_provider_adapters_exist_only_in_enabled_mode(self) -> None:
        self.assertEqual(
            build_configured_action_adapters(_settings("disabled")),
            {},
        )
        self.assertEqual(
            build_configured_action_adapters(_settings("test")),
            {},
        )

        enabled = build_configured_action_adapters(_settings("enabled"))

        self.assertIn("gmail", enabled)
        self.assertIn("google_ads", enabled)


class ExternalConnectorWriteModeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_and_test_modes_fail_before_database_access(self) -> None:
        class NeverSession:
            async def scalar(self, *_args, **_kwargs):
                raise AssertionError(
                    "disabled/test connector boundary reached database"
                )

        for mode in ("disabled", "test"):
            with self.subTest(mode=mode):
                with self.assertRaises(ExternalConnectorWritesDisabledError):
                    await prepare_connector_dispatch_context(
                        NeverSession(),  # type: ignore[arg-type]
                        business_id=uuid4(),
                        attempt_id=uuid4(),
                        connection_id=uuid4(),
                        configuration=_settings(mode),
                    )

    async def test_enabled_mode_crosses_global_write_gate(self) -> None:
        class ReachableSession:
            async def scalar(self, *_args, **_kwargs):
                raise AssertionError(
                    "enabled connector boundary reached database"
                )

        # The AssertionError proves enabled mode crossed the global external
        # write gate and proceeded into tenant/billing/database validation.
        # It does NOT prove a provider write is authorized; all downstream
        # governance remains mandatory.
        with self.assertRaisesRegex(
            AssertionError,
            "enabled connector boundary reached database",
        ):
            await prepare_connector_dispatch_context(
                ReachableSession(),  # type: ignore[arg-type]
                business_id=uuid4(),
                attempt_id=uuid4(),
                connection_id=uuid4(),
                configuration=_settings("enabled"),
            )

    def test_feed_mutations_are_blocked_in_disabled_and_test_modes(self) -> None:
        for mode in ("disabled", "test"):
            with self.subTest(mode=mode):
                with self.assertRaises(CommerceConfigurationRequiredError):
                    _require_feed_writes(_settings(mode))

    def test_feed_mutation_gate_accepts_enabled_mode(self) -> None:
        # Passing this gate does not itself perform a provider operation.
        _require_feed_writes(_settings("enabled"))


if __name__ == "__main__":
    unittest.main()
