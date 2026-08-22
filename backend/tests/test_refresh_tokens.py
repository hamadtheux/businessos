import os
import re
import unittest
from unittest.mock import patch


os.environ["AIBOS_DATABASE_URL"] = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = "x" * 32

from app.core.config import Settings  # noqa: E402
from app.core.refresh_tokens import (  # noqa: E402
    generate_refresh_token,
    hash_refresh_token,
)
from app.models.auth_session import AuthSession  # noqa: E402


class RefreshTokenUtilityTests(unittest.TestCase):
    def test_generated_token_is_nonempty(self) -> None:
        self.assertTrue(bool(generate_refresh_token()))

    def test_generated_tokens_differ(self) -> None:
        self.assertTrue(generate_refresh_token() != generate_refresh_token())

    def test_configured_entropy_is_passed_to_secure_generator(self) -> None:
        config = self._settings(auth_refresh_token_bytes=64)

        with patch(
            "app.core.refresh_tokens.token_urlsafe",
            return_value="test-token",
        ) as token_urlsafe_mock:
            token = generate_refresh_token(config=config)

        self.assertEqual(token, "test-token")
        token_urlsafe_mock.assert_called_once_with(64)

    def test_hash_is_deterministic_sha256_lowercase_hex(self) -> None:
        first = hash_refresh_token("deterministic-test-token")
        second = hash_refresh_token("deterministic-test-token")

        self.assertTrue(first == second)
        self.assertEqual(len(first), 64)
        self.assertIsNotNone(re.fullmatch(r"[0-9a-f]{64}", first))

    def test_different_tokens_have_different_hashes(self) -> None:
        first = hash_refresh_token("first-test-token")
        second = hash_refresh_token("second-test-token")

        self.assertTrue(first != second)

    def test_empty_and_non_string_tokens_are_rejected(self) -> None:
        for invalid_token in ("", None, b"bytes-are-not-accepted"):
            with self.subTest(token_type=type(invalid_token).__name__):
                with self.assertRaises(ValueError):
                    hash_refresh_token(invalid_token)  # type: ignore[arg-type]

    def test_auth_session_has_hash_but_no_raw_token_storage(self) -> None:
        columns = set(AuthSession.__table__.columns.keys())

        self.assertIn("token_hash", columns)
        self.assertTrue(
            {
                "refresh_token",
                "raw_token",
                "plaintext_token",
                "access_token",
            }.isdisjoint(columns)
        )

    def _settings(self, **overrides: object) -> Settings:
        values: dict[str, object] = {
            "database_url": "postgresql+asyncpg://database.invalid/test",
            "auth_secret_key": "x" * 32,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)
