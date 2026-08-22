import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from pwdlib.hashers.argon2 import Argon2Hasher


TEST_AUTH_SECRET = (
    "unit-test-only-auth-secret-with-at-least-sixty-four-bytes-do-not-use-in-production"
)
TEST_DATABASE_URL = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = TEST_AUTH_SECRET
os.environ["AIBOS_DATABASE_URL"] = TEST_DATABASE_URL

from app.core.config import Settings  # noqa: E402
from app.core.security import (  # noqa: E402
    InvalidAuthenticationTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class PasswordSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plain_password = "correct horse battery staple"
        cls.hashed_password = hash_password(cls.plain_password)

    def test_password_hash_does_not_equal_plaintext(self) -> None:
        self.assertNotEqual(self.hashed_password, self.plain_password)

    def test_correct_password_verifies(self) -> None:
        self.assertTrue(verify_password(self.plain_password, self.hashed_password))

    def test_incorrect_password_fails(self) -> None:
        self.assertFalse(verify_password("incorrect password", self.hashed_password))

    def test_outdated_password_hash_produces_recommended_upgrade(self) -> None:
        legacy_hasher = Argon2Hasher(
            time_cost=1,
            memory_cost=8192,
            parallelism=1,
        )
        legacy_hash = legacy_hasher.hash(self.plain_password)
        updated_hashes: list[str] = []

        is_valid = verify_password(
            self.plain_password,
            legacy_hash,
            on_hash_update=updated_hashes.append,
        )

        self.assertTrue(is_valid)
        self.assertEqual(len(updated_hashes), 1)
        self.assertTrue(verify_password(self.plain_password, updated_hashes[0]))


class AccessTokenSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = Settings(
            _env_file=None,
            database_url=TEST_DATABASE_URL,
            auth_secret_key=TEST_AUTH_SECRET,
            auth_algorithm="HS256",
            auth_access_token_expire_minutes=15,
            auth_issuer="test-issuer",
            auth_audience="test-audience",
        )

    def setUp(self) -> None:
        self.user_id = uuid4()

    def test_access_token_can_be_created_and_decoded(self) -> None:
        token = create_access_token(self.user_id, config=self.config)

        claims = decode_access_token(token, config=self.config)

        self.assertEqual(claims.sub, self.user_id)
        self.assertEqual(claims.type, "access")
        self.assertIsInstance(claims.jti, UUID)
        self.assertEqual(claims.iss, self.config.auth_issuer)
        self.assertEqual(claims.aud, self.config.auth_audience)
        self.assertIsNotNone(claims.iat.tzinfo)
        self.assertIsNotNone(claims.nbf.tzinfo)
        self.assertIsNotNone(claims.exp.tzinfo)

    def test_invalid_signature_is_rejected(self) -> None:
        token = self._encode_token(
            secret="different-unit-test-secret-that-is-also-at-least-sixty-four-bytes-long"
        )

        with self.assertRaises(InvalidAuthenticationTokenError):
            decode_access_token(token, config=self.config)

    def test_wrong_issuer_is_rejected(self) -> None:
        token = self._encode_token(issuer="wrong-issuer")

        with self.assertRaises(InvalidAuthenticationTokenError):
            decode_access_token(token, config=self.config)

    def test_wrong_audience_is_rejected(self) -> None:
        token = self._encode_token(audience="wrong-audience")

        with self.assertRaises(InvalidAuthenticationTokenError):
            decode_access_token(token, config=self.config)

    def test_expired_token_is_rejected(self) -> None:
        token = self._encode_token(expired=True)

        with self.assertRaises(InvalidAuthenticationTokenError):
            decode_access_token(token, config=self.config)

    def test_wrong_token_type_is_rejected(self) -> None:
        token = self._encode_token(token_type="refresh")

        with self.assertRaises(InvalidAuthenticationTokenError):
            decode_access_token(token, config=self.config)

    def test_missing_required_claim_is_rejected(self) -> None:
        token = self._encode_token(omit_claim="jti")

        with self.assertRaises(InvalidAuthenticationTokenError):
            decode_access_token(token, config=self.config)

    def test_invalid_subject_is_rejected(self) -> None:
        token = self._encode_token(subject="not-a-uuid")

        with self.assertRaises(InvalidAuthenticationTokenError):
            decode_access_token(token, config=self.config)

    def test_incoming_token_cannot_select_another_algorithm(self) -> None:
        token = self._encode_token(algorithm="HS512")

        with self.assertRaises(InvalidAuthenticationTokenError):
            decode_access_token(token, config=self.config)

    def _encode_token(
        self,
        *,
        secret: str | None = None,
        algorithm: str | None = None,
        issuer: str | None = None,
        audience: str | None = None,
        token_type: str = "access",
        subject: str | None = None,
        expired: bool = False,
        omit_claim: str | None = None,
    ) -> str:
        now = datetime.now(UTC)
        expires_at = now - timedelta(minutes=1) if expired else now + timedelta(minutes=15)
        payload: dict[str, str | datetime] = {
            "sub": subject or str(self.user_id),
            "jti": str(uuid4()),
            "iat": now - timedelta(minutes=2) if expired else now,
            "nbf": now - timedelta(minutes=2) if expired else now,
            "exp": expires_at,
            "iss": issuer or self.config.auth_issuer,
            "aud": audience or self.config.auth_audience,
            "type": token_type,
        }
        if omit_claim is not None:
            del payload[omit_claim]

        return jwt.encode(
            payload,
            secret or self.config.auth_secret_key.get_secret_value(),
            algorithm=algorithm or self.config.auth_algorithm,
        )


if __name__ == "__main__":
    unittest.main()
