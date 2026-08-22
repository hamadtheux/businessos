import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError


TEST_DATABASE_URL = "postgresql+asyncpg://database.invalid/test"
TEST_AUTH_SECRET = (
    "authentication-test-auth-secret-with-at-least-thirty-two-bytes"
)
os.environ["AIBOS_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["AIBOS_AUTH_SECRET_KEY"] = TEST_AUTH_SECRET

from app.core.security import hash_password  # noqa: E402
from app.exceptions.auth import (  # noqa: E402
    InvalidCredentialsError,
    UserAccountUnavailableError,
    UserAuthenticationPersistenceError,
)
from app.models.business import Business  # noqa: E402
from app.models.business_membership import BusinessMembership  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.auth import UserLoginInput, UserPublic  # noqa: E402
from app.services.auth import authenticate_user  # noqa: E402


class LoginSchemaTests(unittest.TestCase):
    def test_valid_login_input_is_accepted(self) -> None:
        credentials = self._credentials()

        self.assertEqual(str(credentials.email), "user@example.com")

    def test_email_is_stripped_and_normalized_to_lowercase(self) -> None:
        credentials = self._credentials(email="  USER@EXAMPLE.COM  ")

        self.assertEqual(str(credentials.email), "user@example.com")

    def test_invalid_email_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._credentials(email="invalid-email")

    def test_unknown_fields_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._credentials(status="active")

    def test_password_is_redacted_from_repr_and_serialization(self) -> None:
        credentials = self._credentials()
        supplied_password = credentials.password.get_secret_value()

        self.assertNotIn(supplied_password, repr(credentials))
        self.assertNotIn(supplied_password, str(credentials))
        self.assertNotIn(supplied_password, credentials.model_dump_json())

    def test_password_whitespace_is_preserved(self) -> None:
        password_with_whitespace = "  preserve this exactly  "

        credentials = self._credentials(password=password_with_whitespace)

        self.assertEqual(
            credentials.password.get_secret_value(),
            password_with_whitespace,
        )

    def test_login_does_not_apply_registration_minimum_password_length(self) -> None:
        credentials = self._credentials(password="x")

        self.assertEqual(credentials.password.get_secret_value(), "x")

    def test_password_longer_than_128_characters_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._credentials(password="x" * 129)

    def _credentials(self, **overrides: object) -> UserLoginInput:
        values: dict[str, object] = {
            "email": "user@example.com",
            "password": "login test phrase",
        }
        values.update(overrides)
        return UserLoginInput(**values)


class AuthenticationServiceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.correct_password = "correct authentication phrase"
        cls.password_hash = hash_password(cls.correct_password)

    async def test_correct_credentials_return_existing_user(self) -> None:
        user = self._user()
        session = _FakeAsyncSession(user=user)

        authenticated_user = await authenticate_user(
            session,
            self._credentials(),
        )

        self.assertIs(authenticated_user, user)
        self.assertEqual(session.scalar_calls, 1)

    async def test_wrong_password_raises_invalid_credentials(self) -> None:
        session = _FakeAsyncSession(user=self._user())

        with self.assertRaises(InvalidCredentialsError):
            await authenticate_user(
                session,
                self._credentials(password="wrong authentication phrase"),
            )

    async def test_unknown_email_raises_invalid_credentials(self) -> None:
        session = _FakeAsyncSession()

        with self.assertRaises(InvalidCredentialsError):
            await authenticate_user(session, self._credentials())

    async def test_unknown_email_still_performs_password_verification(self) -> None:
        session = _FakeAsyncSession()

        with patch(
            "app.services.auth.verify_password",
            return_value=False,
        ) as verify_password_mock:
            with self.assertRaises(InvalidCredentialsError):
                await authenticate_user(session, self._credentials())

        verify_password_mock.assert_called_once()
        self.assertEqual(
            verify_password_mock.call_args.args[0],
            self.correct_password,
        )

    async def test_unknown_email_does_not_generate_a_hash_per_request(self) -> None:
        session = _FakeAsyncSession()

        with patch("app.services.auth.hash_password") as hash_password_mock:
            for _ in range(2):
                with self.assertRaises(InvalidCredentialsError):
                    await authenticate_user(session, self._credentials())

        hash_password_mock.assert_not_called()

    async def test_unavailable_account_status_raises_domain_error(self) -> None:
        for account_status in ("inactive", "suspended"):
            with self.subTest(account_status=account_status):
                session = _FakeAsyncSession(user=self._user(status=account_status))

                with self.assertRaises(UserAccountUnavailableError):
                    await authenticate_user(session, self._credentials())

    async def test_wrong_password_on_unavailable_account_is_invalid(self) -> None:
        for account_status in ("inactive", "suspended"):
            with self.subTest(account_status=account_status):
                session = _FakeAsyncSession(user=self._user(status=account_status))

                with self.assertRaises(InvalidCredentialsError):
                    await authenticate_user(
                        session,
                        self._credentials(password="wrong authentication phrase"),
                    )

    async def test_database_lookup_failure_raises_persistence_error(self) -> None:
        session = _FakeAsyncSession(
            scalar_error=SQLAlchemyError("internal lookup failure")
        )

        with self.assertRaises(UserAuthenticationPersistenceError) as raised:
            await authenticate_user(session, self._credentials())

        self.assertNotIsInstance(raised.exception, SQLAlchemyError)

    async def test_service_does_not_commit_create_tokens_or_domain_objects(
        self,
    ) -> None:
        session = _FakeAsyncSession(user=self._user())

        with patch("app.core.security.create_access_token") as create_token_mock:
            await authenticate_user(session, self._credentials())

        self.assertEqual(session.commit_calls, 0)
        self.assertEqual(session.added, [])
        self.assertFalse(any(isinstance(item, Business) for item in session.added))
        self.assertFalse(
            any(isinstance(item, BusinessMembership) for item in session.added)
        )
        create_token_mock.assert_not_called()

    async def test_recommended_hash_upgrade_is_assigned_without_commit(self) -> None:
        user = self._user()
        original_hash = user.password_hash
        session = _FakeAsyncSession(user=user)

        def verify_with_upgrade(
            plain_password: str,
            stored_hash: str,
            *,
            on_hash_update,
        ) -> bool:
            self.assertEqual(plain_password, self.correct_password)
            self.assertEqual(stored_hash, original_hash)
            on_hash_update("replacement-test-hash")
            return True

        with patch(
            "app.services.auth.verify_password",
            side_effect=verify_with_upgrade,
        ):
            authenticated_user = await authenticate_user(
                session,
                self._credentials(),
            )

        self.assertIs(authenticated_user, user)
        self.assertEqual(user.password_hash, "replacement-test-hash")
        self.assertEqual(session.commit_calls, 0)

    async def test_unavailable_account_does_not_apply_hash_upgrade(self) -> None:
        user = self._user(status="inactive")
        original_hash = user.password_hash
        session = _FakeAsyncSession(user=user)

        def verify_with_upgrade(
            plain_password: str,
            stored_hash: str,
            *,
            on_hash_update,
        ) -> bool:
            on_hash_update("replacement-test-hash")
            return True

        with patch(
            "app.services.auth.verify_password",
            side_effect=verify_with_upgrade,
        ):
            with self.assertRaises(UserAccountUnavailableError):
                await authenticate_user(session, self._credentials())

        self.assertEqual(user.password_hash, original_hash)

    async def test_secrets_do_not_appear_in_exceptions_or_public_schema(
        self,
    ) -> None:
        supplied_password = "wrong authentication phrase"
        user = self._user()
        session = _FakeAsyncSession(user=user)

        with self.assertRaises(InvalidCredentialsError) as raised:
            await authenticate_user(
                session,
                self._credentials(password=supplied_password),
            )

        exception_text = str(raised.exception)
        self.assertNotIn(supplied_password, exception_text)
        self.assertNotIn(user.password_hash, exception_text)
        self.assertNotIn("password", UserPublic.model_fields)
        self.assertNotIn("password_hash", UserPublic.model_fields)

    def _credentials(self, **overrides: object) -> UserLoginInput:
        values: dict[str, object] = {
            "email": "user@example.com",
            "password": self.correct_password,
        }
        values.update(overrides)
        return UserLoginInput(**values)

    def _user(self, *, status: str = "active") -> User:
        return User(
            email="user@example.com",
            password_hash=self.password_hash,
            first_name="Ada",
            last_name="Lovelace",
            status=status,
            is_email_verified=True,
        )


class _FakeAsyncSession:
    def __init__(
        self,
        *,
        user: User | None = None,
        scalar_error: SQLAlchemyError | None = None,
    ) -> None:
        self.user = user
        self.scalar_error = scalar_error
        self.scalar_calls = 0
        self.commit_calls = 0
        self.added: list[object] = []

    async def scalar(self, statement: object) -> User | None:
        self.scalar_calls += 1
        if self.scalar_error is not None:
            raise self.scalar_error
        return self.user

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def commit(self) -> None:
        self.commit_calls += 1
