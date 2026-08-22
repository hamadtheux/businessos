import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import jwt


TEST_DATABASE_URL = "postgresql+asyncpg://database.invalid/test"
TEST_AUTH_SECRET = "x" * 32
os.environ["AIBOS_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["AIBOS_AUTH_SECRET_KEY"] = TEST_AUTH_SECRET

from app.core.config import settings  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402


class CurrentUserApiTests(unittest.IsolatedAsyncioTestCase):
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

    def test_me_route_has_typed_authenticated_openapi_contract(self) -> None:
        openapi = app.openapi()
        operation = openapi["paths"]["/api/v1/auth/me"]["get"]
        response_schema = operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        security_requirements = operation["security"]

        self.assertEqual(
            response_schema["$ref"],
            "#/components/schemas/UserPublic",
        )
        self.assertTrue(security_requirements)
        security_scheme_name = next(iter(security_requirements[0]))
        security_scheme = openapi["components"]["securitySchemes"][
            security_scheme_name
        ]
        self.assertEqual(security_scheme["type"], "http")
        self.assertEqual(security_scheme["scheme"], "bearer")

    async def test_authenticated_user_returns_safe_200_response(self) -> None:
        original_password_hash = self.user.password_hash

        with patch("app.api.v1.auth.create_access_token") as token_mock:
            response = await self._authenticated_request(
                create_access_token(self.user.id)
            )

        self.assertEqual(response.status_code, 200)
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
        self.assertEqual(response_data["id"], str(self.user.id))
        self.assertEqual(response_data["email"], self.user.email)
        self.assertNotIn("password", response_data)
        self.assertNotIn("password_hash", response_data)
        self.assertNotIn("access_token", response_data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(self.session.scalar_calls, 1)
        self.assertEqual(self.session.commit_calls, 0)
        self.assertEqual(self.session.flush_calls, 0)
        self.assertEqual(self.session.added, [])
        self.assertEqual(self.user.password_hash, original_password_hash)
        token_mock.assert_not_called()

    async def test_missing_token_returns_401_with_bearer_challenge(self) -> None:
        response = await self.client.get("/api/v1/auth/me")

        self._assert_invalid_authentication(response)

    async def test_invalid_token_returns_401_with_bearer_challenge(self) -> None:
        response = await self._authenticated_request("invalid-token-value")

        self._assert_invalid_authentication(response)

    async def test_expired_token_returns_401_with_bearer_challenge(self) -> None:
        response = await self._authenticated_request(
            self._encode_token(self.user.id, expired=True)
        )

        self._assert_invalid_authentication(response)

    async def test_inactive_and_suspended_users_return_403(self) -> None:
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

    async def test_user_data_comes_from_database_authority(self) -> None:
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
        self.assertEqual(response.json()["first_name"], self.user.first_name)
        self.assertEqual(response.json()["status"], self.user.status)
        self.assertEqual(self.session.requested_user_id, self.user.id)

    async def test_existing_auth_and_status_routes_remain_available(self) -> None:
        paths = app.openapi()["paths"]

        self.assertIn("post", paths["/api/v1/auth/login"])
        self.assertIn("post", paths["/api/v1/auth/register"])

        response = await self.client.get("/api/v1/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "available", "api_version": "v1"},
        )

    async def _authenticated_request(self, token: str) -> httpx.Response:
        return await self.client.get(
            "/api/v1/auth/me",
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
        self.scalar_calls = 0
        self.commit_calls = 0
        self.flush_calls = 0
        self.added: list[object] = []

    async def scalar(self, statement: object) -> User | None:
        self.scalar_calls += 1
        parameters = statement.compile().params
        self.requested_user_id = next(iter(parameters.values()))
        if self.user is not None and self.user.id == self.requested_user_id:
            return self.user
        return None

    async def commit(self) -> None:
        self.commit_calls += 1

    async def flush(self) -> None:
        self.flush_calls += 1

    def add(self, instance: object) -> None:
        self.added.append(instance)
