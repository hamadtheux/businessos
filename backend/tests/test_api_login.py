import os
import unittest
from datetime import UTC, datetime
from types import TracebackType
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from sqlalchemy.exc import SQLAlchemyError


TEST_DATABASE_URL = "postgresql+asyncpg://database.invalid/test"
TEST_AUTH_SECRET = "x" * 32
os.environ["AIBOS_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["AIBOS_AUTH_SECRET_KEY"] = TEST_AUTH_SECRET

from app.core.config import settings  # noqa: E402
from app.core.security import (  # noqa: E402
    InvalidAuthenticationTokenError,
    create_access_token as issue_access_token,
    decode_access_token,
)
from app.db.session import get_db_session  # noqa: E402
from app.exceptions.auth import (  # noqa: E402
    InvalidCredentialsError,
    RefreshSessionPersistenceError,
    UserAccountUnavailableError,
    UserAuthenticationPersistenceError,
)
from app.main import app  # noqa: E402
from app.models.auth_session import AuthSession  # noqa: E402
from app.models.business import Business  # noqa: E402
from app.models.business_membership import BusinessMembership  # noqa: E402
from app.models.user import User  # noqa: E402


class LoginApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user = self._user()
        self.session = _FakeAsyncSession(user=self.user)
        self.original_dependency_overrides = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        app.dependency_overrides[get_db_session] = override_session
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_dependency_overrides)

    def test_login_route_and_openapi_contract_exist(self) -> None:
        operation = app.openapi()["paths"]["/api/v1/auth/login"]["post"]
        request_schema = operation["requestBody"]["content"]["application/json"][
            "schema"
        ]
        response_schema = operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"]

        self.assertEqual(
            request_schema["$ref"],
            "#/components/schemas/UserLoginInput",
        )
        self.assertEqual(
            response_schema["$ref"],
            "#/components/schemas/UserLoginResponse",
        )

    @patch("app.api.v1.auth.authenticate_user", new_callable=AsyncMock)
    async def test_correct_credentials_return_safe_token_response(
        self,
        authenticate_user_mock: AsyncMock,
    ) -> None:
        authenticate_user_mock.return_value = self.user

        response = await self.client.post(
            "/api/v1/auth/login",
            json=self._valid_payload(),
        )

        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertEqual(
            set(response_data),
            {"access_token", "token_type", "expires_in", "user"},
        )
        self.assertIsInstance(response_data["access_token"], str)
        self.assertTrue(response_data["access_token"])
        self.assertEqual(response_data["token_type"], "bearer")
        self.assertEqual(
            response_data["expires_in"],
            settings.auth_access_token_expire_minutes * 60,
        )
        self.assertEqual(
            set(response_data["user"]),
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
        self.assertEqual(response_data["user"]["id"], str(self.user.id))
        self.assertNotIn("password", response_data)
        self.assertNotIn("password_hash", response_data)
        self.assertNotIn("password", response_data["user"])
        self.assertNotIn("password_hash", response_data["user"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertTrue(self.session.transaction_committed)
        self.assertFalse(self.session.transaction_rolled_back)
        self.assertEqual(len(self.session.committed_auth_sessions), 1)
        refresh_session = self.session.committed_auth_sessions[0]
        self.assertEqual(refresh_session.user_id, self.user.id)
        self.assertFalse(hasattr(refresh_session, "refresh_token"))
        self.assertIn(settings.auth_refresh_cookie_name, response.cookies)
        set_cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", set_cookie)
        self.assertIn("path=/api/v1/auth", set_cookie)
        self.assertIn("samesite=lax", set_cookie)
        self.assertNotIn("secure", set_cookie)
        self.assertNotIn("refresh_token", response_data)
        refresh_cookie = response.cookies.get(settings.auth_refresh_cookie_name)
        self.assertIsNotNone(refresh_cookie)
        self.assertNotIn(refresh_cookie, response.text)
        self.assertFalse(
            any(isinstance(item, Business) for item in self.session.added)
        )
        self.assertFalse(
            any(
                isinstance(item, BusinessMembership)
                for item in self.session.added
            )
        )

    @patch("app.api.v1.auth.authenticate_user", new_callable=AsyncMock)
    async def test_returned_token_retains_approved_validation_contract(
        self,
        authenticate_user_mock: AsyncMock,
    ) -> None:
        authenticate_user_mock.return_value = self.user

        response = await self.client.post(
            "/api/v1/auth/login",
            json=self._valid_payload(),
        )
        access_token = response.json()["access_token"]
        claims = decode_access_token(access_token)

        self.assertEqual(claims.sub, self.user.id)
        self.assertEqual(claims.type, "access")
        self.assertEqual(claims.iss, settings.auth_issuer)
        self.assertEqual(claims.aud, settings.auth_audience)

        wrong_issuer_config = settings.model_copy(
            update={"auth_issuer": "unexpected-issuer"}
        )
        with self.assertRaises(InvalidAuthenticationTokenError):
            decode_access_token(access_token, config=wrong_issuer_config)

        wrong_audience_config = settings.model_copy(
            update={"auth_audience": "unexpected-audience"}
        )
        with self.assertRaises(InvalidAuthenticationTokenError):
            decode_access_token(access_token, config=wrong_audience_config)

    async def test_wrong_password_and_unknown_email_have_identical_401s(
        self,
    ) -> None:
        responses: list[httpx.Response] = []

        with (
            patch(
                "app.api.v1.auth.authenticate_user",
                new_callable=AsyncMock,
                side_effect=InvalidCredentialsError("internal detail"),
            ),
            patch("app.api.v1.auth.create_access_token") as token_mock,
        ):
            for email in ("known@example.com", "unknown@example.com"):
                responses.append(
                    await self.client.post(
                        "/api/v1/auth/login",
                        json=self._valid_payload(email=email),
                    )
                )

        first_response, second_response = responses
        self.assertEqual(first_response.status_code, 401)
        self.assertEqual(second_response.status_code, 401)
        self.assertEqual(first_response.json(), second_response.json())
        self.assertEqual(
            first_response.headers["WWW-Authenticate"],
            "Bearer",
        )
        self.assertEqual(
            second_response.headers["WWW-Authenticate"],
            "Bearer",
        )
        self.assertFalse(self.session.transaction_committed)
        self.assertTrue(self.session.transaction_rolled_back)
        token_mock.assert_not_called()

    async def test_inactive_and_suspended_accounts_map_to_403(self) -> None:
        with patch(
            "app.api.v1.auth.authenticate_user",
            new_callable=AsyncMock,
            side_effect=UserAccountUnavailableError("internal detail"),
        ):
            for account_status in ("inactive", "suspended"):
                with self.subTest(account_status=account_status):
                    response = await self.client.post(
                        "/api/v1/auth/login",
                        json=self._valid_payload(),
                    )

                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(
                        response.json(),
                        {"detail": "This account is unavailable."},
                    )

        self.assertFalse(self.session.transaction_committed)
        self.assertTrue(self.session.transaction_rolled_back)

    async def test_persistence_failure_maps_to_safe_503_without_token(self) -> None:
        with (
            patch(
                "app.api.v1.auth.authenticate_user",
                new_callable=AsyncMock,
                side_effect=UserAuthenticationPersistenceError(
                    "internal persistence detail"
                ),
            ),
            patch("app.api.v1.auth.create_access_token") as token_mock,
        ):
            response = await self.client.post(
                "/api/v1/auth/login",
                json=self._valid_payload(),
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Authentication is temporarily unavailable."},
        )
        self.assertFalse(self.session.transaction_committed)
        self.assertTrue(self.session.transaction_rolled_back)
        token_mock.assert_not_called()

    async def test_refresh_session_failure_rolls_back_without_token_or_cookie(
        self,
    ) -> None:
        with (
            patch(
                "app.api.v1.auth.authenticate_user",
                new_callable=AsyncMock,
                return_value=self.user,
            ),
            patch(
                "app.api.v1.auth.create_refresh_session",
                new_callable=AsyncMock,
                side_effect=RefreshSessionPersistenceError(
                    "internal refresh persistence detail"
                ),
            ),
            patch("app.api.v1.auth.create_access_token") as token_mock,
        ):
            response = await self.client.post(
                "/api/v1/auth/login",
                json=self._valid_payload(),
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Authentication is temporarily unavailable."},
        )
        self.assertTrue(self.session.transaction_rolled_back)
        self.assertFalse(self.session.transaction_committed)
        self.assertNotIn("set-cookie", response.headers)
        token_mock.assert_not_called()

    async def test_commit_failure_maps_to_safe_503_without_token(self) -> None:
        self.session.commit_error = SQLAlchemyError("internal commit failure")

        with (
            patch(
                "app.api.v1.auth.authenticate_user",
                new_callable=AsyncMock,
                return_value=self.user,
            ),
            patch("app.api.v1.auth.create_access_token") as token_mock,
        ):
            response = await self.client.post(
                "/api/v1/auth/login",
                json=self._valid_payload(),
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Authentication is temporarily unavailable."},
        )
        self.assertFalse(self.session.transaction_committed)
        self.assertTrue(self.session.transaction_rolled_back)
        self.assertNotIn("set-cookie", response.headers)
        token_mock.assert_not_called()

    @patch("app.api.v1.auth.authenticate_user", new_callable=AsyncMock)
    async def test_token_is_created_only_after_transaction_commits(
        self,
        authenticate_user_mock: AsyncMock,
    ) -> None:
        authenticate_user_mock.return_value = self.user

        def create_token_after_commit(user_id):
            self.assertTrue(self.session.transaction_committed)
            self.assertFalse(self.session.transaction_active)
            return issue_access_token(user_id)

        with patch(
            "app.api.v1.auth.create_access_token",
            side_effect=create_token_after_commit,
        ) as token_mock:
            response = await self.client.post(
                "/api/v1/auth/login",
                json=self._valid_payload(),
            )

        self.assertEqual(response.status_code, 200)
        token_mock.assert_called_once_with(self.user.id)

    async def test_password_hash_upgrade_persists_with_api_transaction(
        self,
    ) -> None:
        original_hash = self.user.password_hash

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
            response = await self.client.post(
                "/api/v1/auth/login",
                json=self._valid_payload(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(self.user.password_hash, original_hash)
        self.assertEqual(
            self.session.committed_password_hash,
            self.user.password_hash,
        )
        self.assertTrue(self.session.transaction_committed)

    async def test_existing_status_route_still_works(self) -> None:
        response = await self.client.get("/api/v1/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "available", "api_version": "v1"},
        )

    def test_registration_route_remains_available(self) -> None:
        paths = app.openapi()["paths"]

        self.assertIn("/api/v1/auth/register", paths)
        self.assertIn("post", paths["/api/v1/auth/register"])

    def _valid_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "email": "user@example.com",
            "password": "login test phrase",
        }
        payload.update(overrides)
        return payload

    def _user(self) -> User:
        user = User(
            email="user@example.com",
            password_hash="stored-test-hash",
            first_name="Ada",
            last_name="Lovelace",
            status="active",
            is_email_verified=True,
        )
        user.id = uuid4()
        user.created_at = datetime.now(UTC)
        return user


class _FakeTransaction:
    def __init__(self, session: "_FakeAsyncSession") -> None:
        self.session = session

    async def __aenter__(self) -> "_FakeTransaction":
        self.session.transaction_active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.session.transaction_active = False
        if exc_type is not None:
            self.session.transaction_rolled_back = True
            return False

        if self.session.commit_error is not None:
            self.session.transaction_rolled_back = True
            raise self.session.commit_error

        self.session.transaction_committed = True
        self.session.committed_auth_sessions = [
            item for item in self.session.added if isinstance(item, AuthSession)
        ]
        if self.session.user is not None:
            self.session.committed_password_hash = self.session.user.password_hash
        return False


class _FakeNestedTransaction:
    async def __aenter__(self) -> "_FakeNestedTransaction":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False


class _FakeAsyncSession:
    def __init__(self, *, user: User | None = None) -> None:
        self.user = user
        self.added: list[object] = []
        self.begin_calls = 0
        self.scalar_calls = 0
        self.transaction_active = False
        self.transaction_committed = False
        self.transaction_rolled_back = False
        self.committed_password_hash: str | None = None
        self.committed_auth_sessions: list[AuthSession] = []
        self.commit_error: SQLAlchemyError | None = None

    def begin(self) -> _FakeTransaction:
        self.begin_calls += 1
        return _FakeTransaction(self)

    def begin_nested(self) -> _FakeNestedTransaction:
        return _FakeNestedTransaction()

    async def scalar(self, statement: object) -> User | None:
        self.scalar_calls += 1
        return self.user

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        now = datetime.now(UTC)
        for item in self.added:
            if isinstance(item, AuthSession):
                item.id = item.id or uuid4()
                item.created_at = now
                item.updated_at = now
