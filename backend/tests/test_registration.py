import os
import unittest
from datetime import UTC, datetime
from types import TracebackType
from unittest.mock import patch
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError


TEST_DATABASE_URL = "postgresql+asyncpg://database.invalid/test"
TEST_AUTH_SECRET = (
    "registration-test-auth-secret-with-at-least-thirty-two-bytes"
)
os.environ["AIBOS_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["AIBOS_AUTH_SECRET_KEY"] = TEST_AUTH_SECRET

from app.core.security import verify_password  # noqa: E402
from app.exceptions.auth import (  # noqa: E402
    UserAlreadyExistsError,
    UserRegistrationPersistenceError,
)
from app.models.business import Business  # noqa: E402
from app.models.business_membership import BusinessMembership  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.auth import UserPublic, UserRegistrationInput  # noqa: E402
from app.services.auth import register_user  # noqa: E402


class RegistrationSchemaTests(unittest.TestCase):
    def test_valid_registration_input_is_accepted_and_normalized(self) -> None:
        registration = self._registration(
            email="  USER@EXAMPLE.COM  ",
            first_name="  Ada  ",
            last_name="  Lovelace  ",
        )

        self.assertEqual(str(registration.email), "user@example.com")
        self.assertEqual(registration.first_name, "Ada")
        self.assertEqual(registration.last_name, "Lovelace")

    def test_invalid_email_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._registration(email="invalid-email")

    def test_unknown_registration_fields_are_rejected(self) -> None:
        for field_name in (
            "status",
            "is_admin",
            "business_id",
            "is_email_verified",
            "password_hash",
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    self._registration(**{field_name: True})

    def test_password_is_redacted_from_registration_representation(self) -> None:
        registration = self._registration()
        secret_value = registration.password.get_secret_value()

        self.assertNotIn(secret_value, repr(registration))
        self.assertNotIn(secret_value, str(registration))

    def test_password_shorter_than_12_characters_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._registration(password="short-value")

    def test_password_longer_than_128_characters_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._registration(password="x" * 129)

    def test_password_whitespace_is_not_stripped(self) -> None:
        password_with_whitespace = "  keep this whitespace  "

        registration = self._registration(password=password_with_whitespace)

        self.assertEqual(
            registration.password.get_secret_value(),
            password_with_whitespace,
        )

    def test_blank_first_name_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._registration(first_name="   ")

    def test_blank_optional_last_name_becomes_none(self) -> None:
        registration = self._registration(last_name="   ")

        self.assertIsNone(registration.last_name)

    def _registration(self, **overrides: object) -> UserRegistrationInput:
        values: dict[str, object] = {
            "email": "user@example.com",
            "password": "registration test phrase",
            "first_name": "Ada",
            "last_name": None,
        }
        values.update(overrides)
        return UserRegistrationInput(**values)


class RegistrationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_hashes_password_without_other_domain_work(self) -> None:
        session = _FakeAsyncSession()
        registration = self._registration()

        with patch("app.core.security.create_access_token") as create_access_token:
            user = await register_user(session, registration)

        self.assertIsInstance(user, User)
        self.assertEqual(user.email, str(registration.email))
        self.assertNotEqual(
            user.password_hash,
            registration.password.get_secret_value(),
        )
        self.assertTrue(
            verify_password(
                registration.password.get_secret_value(),
                user.password_hash,
            )
        )
        self.assertEqual(user.status, "active")
        self.assertFalse(user.is_email_verified)
        self.assertEqual(session.scalar_calls, 1)
        self.assertEqual(session.flush_calls, 1)
        self.assertEqual(session.commit_calls, 0)
        self.assertTrue(session.savepoint_released)
        self.assertFalse(session.savepoint_rolled_back)
        self.assertEqual(len(session.added), 1)
        self.assertFalse(any(isinstance(item, Business) for item in session.added))
        self.assertFalse(
            any(isinstance(item, BusinessMembership) for item in session.added)
        )
        create_access_token.assert_not_called()

    async def test_existing_email_raises_domain_error(self) -> None:
        session = _FakeAsyncSession(existing_user_id=uuid4())

        with self.assertRaises(UserAlreadyExistsError):
            await register_user(session, self._registration())

        self.assertEqual(session.scalar_calls, 1)
        self.assertEqual(session.flush_calls, 0)
        self.assertEqual(session.added, [])

    async def test_concurrent_duplicate_is_translated_and_savepoint_rolled_back(
        self,
    ) -> None:
        duplicate_error = IntegrityError(
            "statement omitted",
            {},
            _ConstraintViolation("ix_users_email"),
        )
        session = _FakeAsyncSession(flush_error=duplicate_error)

        with self.assertRaises(UserAlreadyExistsError) as raised:
            await register_user(session, self._registration())

        self.assertNotIsInstance(raised.exception, IntegrityError)
        self.assertTrue(session.savepoint_rolled_back)
        self.assertFalse(session.transaction_broken)
        self.assertEqual(session.commit_calls, 0)

    async def test_unrelated_integrity_error_is_not_reported_as_duplicate(self) -> None:
        unrelated_error = IntegrityError(
            "statement omitted",
            {},
            _ConstraintViolation("ck_users_valid_status"),
        )
        session = _FakeAsyncSession(flush_error=unrelated_error)

        with self.assertRaises(UserRegistrationPersistenceError):
            await register_user(session, self._registration())

        self.assertTrue(session.savepoint_rolled_back)
        self.assertFalse(session.transaction_broken)

    async def test_public_user_schema_never_exposes_password_hash(self) -> None:
        user = await register_user(_FakeAsyncSession(), self._registration())

        public_user = UserPublic.model_validate(user)
        public_data = public_user.model_dump()

        self.assertNotIn("password", UserPublic.model_fields)
        self.assertNotIn("password_hash", UserPublic.model_fields)
        self.assertNotIn("password", public_data)
        self.assertNotIn("password_hash", public_data)

    def _registration(self) -> UserRegistrationInput:
        return UserRegistrationInput(
            email="user@example.com",
            password="registration test phrase",
            first_name="Ada",
            last_name="Lovelace",
        )


class _ConstraintViolation(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__("database integrity constraint violated")
        self.constraint_name = constraint_name
        self.sqlstate = "23505"


class _FakeNestedTransaction:
    def __init__(self, session: "_FakeAsyncSession") -> None:
        self.session = session

    async def __aenter__(self) -> "_FakeNestedTransaction":
        self.session.savepoint_started = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc_type is None:
            self.session.savepoint_released = True
        else:
            self.session.savepoint_rolled_back = True
            self.session.transaction_broken = False
        return False


class _FakeAsyncSession:
    def __init__(
        self,
        *,
        existing_user_id: UUID | None = None,
        flush_error: IntegrityError | None = None,
    ) -> None:
        self.existing_user_id = existing_user_id
        self.flush_error = flush_error
        self.added: list[object] = []
        self.scalar_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0
        self.savepoint_started = False
        self.savepoint_released = False
        self.savepoint_rolled_back = False
        self.transaction_broken = False

    async def scalar(self, statement: object) -> UUID | None:
        self.scalar_calls += 1
        return self.existing_user_id

    def begin_nested(self) -> _FakeNestedTransaction:
        return _FakeNestedTransaction(self)

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_error is not None:
            self.transaction_broken = True
            raise self.flush_error

        user = self.added[-1]
        if isinstance(user, User):
            user.id = uuid4()
            user.status = "active"
            user.is_email_verified = False
            user.created_at = datetime.now(UTC)
            user.updated_at = user.created_at

    async def commit(self) -> None:
        self.commit_calls += 1
