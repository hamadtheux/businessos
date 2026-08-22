import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import jwt
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError


TEST_DATABASE_URL = "postgresql+asyncpg://database.invalid/test"
TEST_AUTH_SECRET = "x" * 32
os.environ["AIBOS_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["AIBOS_AUTH_SECRET_KEY"] = TEST_AUTH_SECRET

from app.api.dependencies.auth import CurrentUserDependency  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.main import app as production_app  # noqa: E402
from app.models.user import User  # noqa: E402


class CurrentUserDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user = self._user()
        self.session = _FakeAsyncSession(user=self.user)
        self.test_app = FastAPI()

        @self.test_app.get("/protected")
        async def protected(
            current_user: CurrentUserDependency,
        ) -> dict[str, str]:
            return {
                "id": str(current_user.id),
                "email": current_user.email,
                "status": current_user.status,
            }

        async def override_session():
            yield self.session

        self.test_app.dependency_overrides[get_db_session] = override_session
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.test_app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_valid_token_resolves_active_database_user(self) -> None:
        response = await self._authenticated_request(
            create_access_token(self.user.id)
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": str(self.user.id),
                "email": self.user.email,
                "status": "active",
            },
        )
        self.assertEqual(self.session.requested_user_id, self.user.id)

    async def test_missing_authorization_header_returns_safe_401(self) -> None:
        response = await self.client.get("/protected")

        self._assert_invalid_authentication(response)

    async def test_wrong_authorization_scheme_returns_safe_401(self) -> None:
        response = await self.client.get(
            "/protected",
            headers={"Authorization": "Basic credentials"},
        )

        self._assert_invalid_authentication(response)

    async def test_malformed_bearer_credentials_return_safe_401(self) -> None:
        response = await self.client.get(
            "/protected",
            headers={"Authorization": "Bearer"},
        )

        self._assert_invalid_authentication(response)

    async def test_invalid_jwt_returns_safe_401(self) -> None:
        response = await self._authenticated_request("invalid-token-value")

        self._assert_invalid_authentication(response)

    async def test_expired_jwt_returns_safe_401(self) -> None:
        response = await self._authenticated_request(
            self._encode_token(self.user.id, expired=True)
        )

        self._assert_invalid_authentication(response)

    async def test_valid_token_for_nonexistent_user_returns_safe_401(self) -> None:
        response = await self._authenticated_request(
            create_access_token(uuid4())
        )

        self._assert_invalid_authentication(response)

    async def test_inactive_and_suspended_users_return_safe_403(self) -> None:
        for account_status in ("inactive", "suspended"):
            with self.subTest(account_status=account_status):
                self.user.status = account_status

                response = await self._authenticated_request(
                    create_access_token(self.user.id)
                )

                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    response.json(),
                    {"detail": "This account is unavailable."},
                )

    async def test_database_failure_returns_safe_503(self) -> None:
        self.session.scalar_error = SQLAlchemyError("internal lookup failure")

        response = await self._authenticated_request(
            create_access_token(self.user.id)
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Authentication is temporarily unavailable."},
        )

    async def test_identity_and_status_are_loaded_from_database(self) -> None:
        token = self._encode_token(
            self.user.id,
            extra_claims={
                "email": "untrusted@example.com",
                "first_name": "Untrusted",
                "status": "suspended",
            },
        )

        response = await self._authenticated_request(token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], self.user.email)
        self.assertEqual(response.json()["status"], self.user.status)
        self.assertEqual(self.session.requested_user_id, self.user.id)

    def test_existing_login_and_registration_routes_remain_unchanged(self) -> None:
        paths = production_app.openapi()["paths"]

        self.assertIn("post", paths["/api/v1/auth/login"])
        self.assertIn("post", paths["/api/v1/auth/register"])

    async def _authenticated_request(self, token: str) -> httpx.Response:
        return await self.client.get(
            "/protected",
            headers={"Authorization": f"Bearer {token}"},
        )

    def _assert_invalid_authentication(self, response: httpx.Response) -> None:
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"detail": "Invalid or expired authentication token."},
        )
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")

    def _encode_token(
        self,
        subject: UUID,
        *,
        expired: bool = False,
        extra_claims: dict[str, str] | None = None,
    ) -> str:
        now = datetime.now(UTC)
        expires_at = (
            now - timedelta(minutes=1)
            if expired
            else now + timedelta(minutes=15)
        )
        payload: dict[str, str | datetime] = {
            "sub": str(subject),
            "jti": str(uuid4()),
            "iat": now - timedelta(minutes=2) if expired else now,
            "nbf": now - timedelta(minutes=2) if expired else now,
            "exp": expires_at,
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
            "type": "access",
        }
        if extra_claims is not None:
            payload.update(extra_claims)

        return jwt.encode(
            payload,
            settings.auth_secret_key.get_secret_value(),
            algorithm=settings.auth_algorithm,
        )

    def _user(self) -> User:
        user = User(
            email="database-user@example.com",
            password_hash="stored-test-hash",
            first_name="Database",
            last_name="User",
            status="active",
            is_email_verified=True,
        )
        user.id = uuid4()
        user.created_at = datetime.now(UTC)
        return user


class _FakeAsyncSession:
    def __init__(self, *, user: User | None = None) -> None:
        self.user = user
        self.requested_user_id: UUID | None = None
        self.scalar_error: SQLAlchemyError | None = None

    async def scalar(self, statement: object) -> User | None:
        if self.scalar_error is not None:
            raise self.scalar_error

        parameters = statement.compile().params
        self.requested_user_id = next(iter(parameters.values()))
        if self.user is not None and self.user.id == self.requested_user_id:
            return self.user
        return None
