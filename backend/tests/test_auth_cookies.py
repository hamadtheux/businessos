import os
import unittest

from fastapi import Response


os.environ["AIBOS_DATABASE_URL"] = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = "x" * 32

from app.core.auth_cookies import (  # noqa: E402
    AUTH_REFRESH_COOKIE_PATH,
    clear_refresh_cookie,
    set_refresh_cookie,
)
from app.core.config import Settings  # noqa: E402


class RefreshCookieHelperTests(unittest.TestCase):
    def test_development_cookie_has_secure_path_scoped_policy(self) -> None:
        response = Response()
        config = self._settings(
            auth_refresh_cookie_name="test_refresh",
            auth_refresh_token_expire_days=12,
        )

        set_refresh_cookie(response, "opaque-test-value", config=config)

        header = response.headers["set-cookie"].lower()
        self.assertIn("test_refresh=", header)
        self.assertIn("httponly", header)
        self.assertIn(f"path={AUTH_REFRESH_COOKIE_PATH}", header)
        self.assertIn("samesite=lax", header)
        self.assertIn("max-age=1036800", header)
        self.assertNotIn("secure", header)

    def test_production_cookie_is_secure(self) -> None:
        response = Response()
        config = self._settings(
            environment="production",
            debug=False,
            auth_refresh_cookie_secure=True,
            storage_backend="s3",
            storage_bucket="test-bucket",
            storage_access_key_id="test-access-key",
            storage_secret_access_key="test-secret-key",
            storage_public_base_url="https://media.invalid",
        )

        set_refresh_cookie(response, "opaque-test-value", config=config)

        self.assertIn("secure", response.headers["set-cookie"].lower())

    def test_cookie_deletion_matches_cookie_scope_and_attributes(self) -> None:
        response = Response()
        config = self._settings(
            auth_refresh_cookie_name="test_refresh",
            auth_refresh_cookie_samesite="strict",
        )

        clear_refresh_cookie(response, config=config)

        header = response.headers["set-cookie"].lower()
        self.assertIn("test_refresh=", header)
        self.assertIn("max-age=0", header)
        self.assertIn("httponly", header)
        self.assertIn(f"path={AUTH_REFRESH_COOKIE_PATH}", header)
        self.assertIn("samesite=strict", header)

    def test_empty_refresh_token_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            set_refresh_cookie(Response(), "", config=self._settings())

    def _settings(self, **overrides: object) -> Settings:
        values: dict[str, object] = {
            "database_url": "postgresql+asyncpg://database.invalid/test",
            "auth_secret_key": "x" * 32,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)
