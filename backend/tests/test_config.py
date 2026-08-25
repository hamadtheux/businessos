import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError


TEST_DATABASE_URL = "postgresql+asyncpg://database.invalid/test"
TEST_AUTH_SECRET = "configuration-test-auth-secret-with-at-least-thirty-two-bytes"
os.environ["AIBOS_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["AIBOS_AUTH_SECRET_KEY"] = TEST_AUTH_SECRET

from app.core.config import Settings  # noqa: E402


class SettingsValidationTests(unittest.TestCase):
    def test_current_real_development_configuration_loads(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = Settings()

        self.assertEqual(config.environment, "development")

    def test_development_widget_urls_share_the_frontend_origin(self) -> None:
        config = self._settings()

        self.assertEqual(str(config.widget_loader_url), "http://localhost:5174/widget-loader.js")
        self.assertEqual(str(config.widget_app_url), "http://localhost:5174/widget.html")

    def test_missing_database_url_fails(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, auth_secret_key=TEST_AUTH_SECRET)

    def test_missing_auth_secret_fails(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, database_url=TEST_DATABASE_URL)

    def test_auth_secret_shorter_than_32_bytes_fails(self) -> None:
        with self.assertRaises(ValidationError):
            self._settings(auth_secret_key="fewer-than-thirty-two-bytes")

    def test_auth_secret_with_at_least_32_bytes_succeeds(self) -> None:
        config = self._settings(auth_secret_key="x" * 32)

        self.assertEqual(
            len(config.auth_secret_key.get_secret_value().encode("utf-8")),
            32,
        )

    def test_unsupported_jwt_algorithm_fails(self) -> None:
        with self.assertRaises(ValidationError):
            self._settings(auth_algorithm="HS512")

    def test_zero_token_expiration_fails(self) -> None:
        with self.assertRaises(ValidationError):
            self._settings(auth_access_token_expire_minutes=0)

    def test_token_expiration_over_60_fails(self) -> None:
        with self.assertRaises(ValidationError):
            self._settings(auth_access_token_expire_minutes=61)

    def test_production_with_debug_enabled_fails(self) -> None:
        with self.assertRaises(ValidationError):
            self._settings(environment="production", debug=True)

    def test_production_with_debug_disabled_succeeds(self) -> None:
        config = self._settings(
            environment="production",
            debug=False,
            auth_refresh_cookie_secure=True,
            **self._s3_settings(),
            **self._public_widget_settings(),
        )

        self.assertEqual(config.environment, "production")
        self.assertFalse(config.debug)
        self.assertTrue(config.auth_refresh_cookie_secure)

    def test_production_rejects_insecure_refresh_cookie(self) -> None:
        with self.assertRaises(ValidationError):
            self._settings(
                environment="production",
                debug=False,
                auth_refresh_cookie_secure=False,
                **self._s3_settings(),
                **self._public_widget_settings(),
            )

    def test_production_and_staging_reject_local_storage(self) -> None:
        for environment in ("staging", "production"):
            with self.subTest(environment=environment):
                with self.assertRaises(ValidationError):
                    self._settings(
                        environment=environment,
                        debug=False,
                        auth_refresh_cookie_secure=True,
                        storage_backend="local",
                    )

    def test_s3_storage_requires_complete_durable_configuration(self) -> None:
        required_fields = (
            "storage_bucket",
            "storage_access_key_id",
            "storage_secret_access_key",
            "storage_public_base_url",
        )
        valid = self._s3_settings()
        for field_name in required_fields:
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    self._settings(
                        **{**valid, field_name: None},
                    )

    def test_s3_storage_rejects_blank_required_values(self) -> None:
        valid = self._s3_settings()
        for field_name in (
            "storage_bucket",
            "storage_access_key_id",
            "storage_secret_access_key",
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    self._settings(
                        **{**valid, field_name: "   "},
                    )

    def test_storage_credentials_are_secret_values(self) -> None:
        config = self._settings(**self._s3_settings())

        self.assertNotIn("storage-access-key", repr(config))
        self.assertNotIn("storage-secret-key", repr(config))

    def test_refresh_cookie_samesite_none_is_not_supported(self) -> None:
        with self.assertRaises(ValidationError):
            self._settings(auth_refresh_cookie_samesite="none")

    def test_credentialed_cors_rejects_wildcard_origin(self) -> None:
        with self.assertRaises(ValidationError):
            self._settings(cors_origins=["*"])

    def test_database_pool_bounds(self) -> None:
        invalid_values = {
            "database_pool_size": 0,
            "database_max_overflow": -1,
            "database_pool_timeout": 0,
        }

        for field_name, value in invalid_values.items():
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    self._settings(**{field_name: value})

    def test_empty_issuer_and_audience_fail(self) -> None:
        for field_name in ("auth_issuer", "auth_audience"):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    self._settings(**{field_name: ""})

    def _settings(self, **overrides: object) -> Settings:
        values: dict[str, object] = {
            "database_url": TEST_DATABASE_URL,
            "auth_secret_key": TEST_AUTH_SECRET,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    @staticmethod
    def _s3_settings() -> dict[str, object]:
        return {
            "storage_backend": "s3",
            "storage_bucket": "business-assets",
            "storage_region": "auto",
            "storage_endpoint_url": "https://objects.example.test",
            "storage_access_key_id": "storage-access-key",
            "storage_secret_access_key": "storage-secret-key",
            "storage_public_base_url": "https://cdn.example.test",
        }

    @staticmethod
    def _public_widget_settings() -> dict[str, object]:
        return {
            "public_api_base_url": "https://widgets.example.test",
            "widget_loader_url": "https://widgets.example.test/widget-loader.js",
            "widget_app_url": "https://widgets.example.test/widget.html",
        }
