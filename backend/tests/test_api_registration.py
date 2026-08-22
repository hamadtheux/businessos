import os
import unittest
from datetime import UTC, datetime
from types import TracebackType
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx


os.environ["AIBOS_DATABASE_URL"] = (
    "postgresql+asyncpg:" + "//" + "database.invalid" + "/test"
)
os.environ["AIBOS_AUTH_SECRET_KEY"] = "x" * 32

from app.db.session import get_db_session  # noqa: E402
from app.exceptions.auth import (  # noqa: E402
    UserAlreadyExistsError,
    UserRegistrationPersistenceError,
)
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.auth import UserRegistrationInput  # noqa: E402


VALID_REGISTRATION_SECRET = "x" * 12


class RegistrationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = _FakeAsyncSession()
        self.original_dependency_overrides = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        app.dependency_overrides[get_db_session] = override_session
        transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_dependency_overrides)

    def test_registration_route_exists(self) -> None:
        paths = app.openapi()["paths"]

        self.assertIn("/api/v1/auth/register", paths)
        self.assertIn("post", paths["/api/v1/auth/register"])

    @patch("app.api.v1.auth.register_user", new_callable=AsyncMock)
    async def test_valid_registration_returns_safe_201_response(
        self,
        register_user_mock: AsyncMock,
    ) -> None:
        register_user_mock.side_effect = self._registered_user
        payload = self._valid_payload(email="  USER@EXAMPLE.COM  ")

        response = await self.client.post("/api/v1/auth/register", json=payload)

        self.assertEqual(response.status_code, 201)
        response_data = response.json()
        self.assertEqual(
            set(response_data),
            {
                "id",
                "email",
                "first_name",
                "last_name",
                "status",
                "is_email_verified",
                "created_at",
            },
        )
        self.assertEqual(response_data["email"], "user@example.com")
        self.assertNotIn("password", response_data)
        self.assertNotIn("password_hash", response_data)
        self.assertNotIn("access_token", response_data)
        self.assertEqual(self.session.begin_calls, 1)
        self.assertTrue(self.session.transaction_committed)
        self.assertFalse(self.session.transaction_rolled_back)

    @patch("app.api.v1.auth.register_user", new_callable=AsyncMock)
    async def test_invalid_email_returns_422(
        self,
        register_user_mock: AsyncMock,
    ) -> None:
        payload = self._valid_payload(email="invalid-email")

        response = await self.client.post("/api/v1/auth/register", json=payload)

        self.assertEqual(response.status_code, 422)
        register_user_mock.assert_not_awaited()
        self.assertFalse(self.session.transaction_committed)

    @patch("app.api.v1.auth.register_user", new_callable=AsyncMock)
    async def test_short_password_returns_422(
        self,
        register_user_mock: AsyncMock,
    ) -> None:
        payload = self._valid_payload(password="x" * 11)

        response = await self.client.post("/api/v1/auth/register", json=payload)

        self.assertEqual(response.status_code, 422)
        register_user_mock.assert_not_awaited()
        self.assertFalse(self.session.transaction_committed)

    @patch("app.api.v1.auth.register_user", new_callable=AsyncMock)
    async def test_unknown_field_returns_422(
        self,
        register_user_mock: AsyncMock,
    ) -> None:
        payload = self._valid_payload(status="suspended")

        response = await self.client.post("/api/v1/auth/register", json=payload)

        self.assertEqual(response.status_code, 422)
        register_user_mock.assert_not_awaited()
        self.assertFalse(self.session.transaction_committed)

    @patch("app.api.v1.auth.register_user", new_callable=AsyncMock)
    async def test_duplicate_email_maps_to_safe_409_and_rolls_back(
        self,
        register_user_mock: AsyncMock,
    ) -> None:
        internal_detail = "internal duplicate-registration detail"
        register_user_mock.side_effect = UserAlreadyExistsError(internal_detail)

        response = await self.client.post(
            "/api/v1/auth/register",
            json=self._valid_payload(),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"detail": "An account with this email address already exists."},
        )
        self.assertNotIn(internal_detail, response.text)
        self.assertTrue(self.session.transaction_rolled_back)
        self.assertFalse(self.session.transaction_committed)

    @patch("app.api.v1.auth.register_user", new_callable=AsyncMock)
    async def test_persistence_failure_maps_to_503_and_rolls_back(
        self,
        register_user_mock: AsyncMock,
    ) -> None:
        register_user_mock.side_effect = UserRegistrationPersistenceError(
            "internal persistence detail"
        )

        response = await self.client.post(
            "/api/v1/auth/register",
            json=self._valid_payload(),
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Registration is temporarily unavailable."},
        )
        self.assertTrue(self.session.transaction_rolled_back)
        self.assertFalse(self.session.transaction_committed)

    async def test_existing_status_route_still_works(self) -> None:
        response = await self.client.get("/api/v1/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "available", "api_version": "v1"},
        )

    def test_openapi_documents_registration_contract(self) -> None:
        operation = app.openapi()["paths"]["/api/v1/auth/register"]["post"]
        request_schema = operation["requestBody"]["content"]["application/json"][
            "schema"
        ]
        response_schema = operation["responses"]["201"]["content"][
            "application/json"
        ]["schema"]

        self.assertEqual(
            request_schema["$ref"],
            "#/components/schemas/UserRegistrationInput",
        )
        self.assertEqual(
            response_schema["$ref"],
            "#/components/schemas/UserPublic",
        )

    def _valid_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "email": "user@example.com",
            "password": VALID_REGISTRATION_SECRET,
            "first_name": "Ada",
            "last_name": "Lovelace",
        }
        payload.update(overrides)
        return payload

    async def _registered_user(
        self,
        session: object,
        registration: UserRegistrationInput,
    ) -> User:
        user = User(
            email=str(registration.email),
            first_name=registration.first_name,
            last_name=registration.last_name,
            status="active",
            is_email_verified=False,
        )
        user.id = uuid4()
        user.created_at = datetime.now(UTC)
        return user


class _FakeTransaction:
    def __init__(self, session: "_FakeAsyncSession") -> None:
        self.session = session

    async def __aenter__(self) -> "_FakeTransaction":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc_type is None:
            self.session.transaction_committed = True
        else:
            self.session.transaction_rolled_back = True
        return False


class _FakeAsyncSession:
    def __init__(self) -> None:
        self.begin_calls = 0
        self.transaction_committed = False
        self.transaction_rolled_back = False

    def begin(self) -> _FakeTransaction:
        self.begin_calls += 1
        return _FakeTransaction(self)
