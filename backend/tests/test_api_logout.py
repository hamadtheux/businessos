import os
import unittest
from datetime import UTC, datetime, timedelta
from types import TracebackType
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import jwt
from sqlalchemy.exc import SQLAlchemyError


os.environ["AIBOS_DATABASE_URL"] = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = "x" * 32

from app.core.auth_cookies import (  # noqa: E402
    clear_refresh_cookie as clear_cookie,
)
from app.core.config import settings  # noqa: E402
from app.core.refresh_tokens import hash_refresh_token  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.exceptions.auth import RefreshSessionPersistenceError  # noqa: E402
from app.main import app  # noqa: E402
from app.models.auth_session import AuthSession  # noqa: E402


class LogoutApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = _FakeAsyncSession()
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

    def test_logout_has_bodyless_204_openapi_contract(self) -> None:
        paths = app.openapi()["paths"]
        operation = paths["/api/v1/auth/logout"]["post"]

        self.assertNotIn("requestBody", operation)
        self.assertNotIn("security", operation)
        self.assertEqual(set(operation["responses"]), {"204", "422"})
        self.assertNotIn("content", operation["responses"]["204"])
        self.assertEqual(len(operation["parameters"]), 1)
        cookie_parameter = operation["parameters"][0]
        self.assertEqual(
            cookie_parameter["name"],
            settings.auth_refresh_cookie_name,
        )
        self.assertEqual(cookie_parameter["in"], "cookie")
        self.assertFalse(cookie_parameter["required"])
        for forbidden_path in (
            "/api/v1/auth/logout-all",
            "/api/v1/auth/sessions",
            "/api/v1/auth/devices",
            "/api/v1/auth/revoke-all",
        ):
            self.assertNotIn(forbidden_path, paths)

    async def test_valid_session_is_revoked_and_committed_before_204(self) -> None:
        raw_token = "valid-logout-api-test-credential"
        auth_session = _make_auth_session(raw_token)
        self.session = _FakeAsyncSession(auth_sessions=[auth_session])

        def clear_after_commit(response):
            self.assertTrue(self.session.transaction_committed)
            self.assertFalse(self.session.transaction_active)
            clear_cookie(response)

        with patch(
            "app.api.v1.auth.clear_refresh_cookie",
            side_effect=clear_after_commit,
        ) as clear_mock:
            response = await self._post_with_cookie(raw_token)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertIsNotNone(auth_session.revoked_at)
        self.assertTrue(self.session.transaction_committed)
        self.assertFalse(self.session.transaction_rolled_back)
        self.assertEqual(self.session.flush_calls, 1)
        self.assertEqual(len(self.session.lock_statements), 1)
        self.assertIsNotNone(self.session.lock_statements[0]._for_update_arg)
        clear_mock.assert_called_once()
        self._assert_deletion_cookie(response)
        self._assert_cache_headers(response)

    async def test_missing_cookie_is_successful_and_cleared_without_database(
        self,
    ) -> None:
        with patch(
            "app.api.v1.auth.revoke_refresh_session",
            new_callable=AsyncMock,
        ) as revoke_mock:
            response = await self.client.post("/api/v1/auth/logout")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(self.session.begin_calls, 0)
        revoke_mock.assert_not_awaited()
        self._assert_deletion_cookie(response)
        self._assert_cache_headers(response)

    async def test_unknown_and_already_revoked_credentials_are_indistinguishable(
        self,
    ) -> None:
        responses: list[httpx.Response] = []
        for case, service_result in (("unknown", False), ("revoked", True)):
            with self.subTest(case=case):
                self.session = _FakeAsyncSession()
                with patch(
                    "app.api.v1.auth.revoke_refresh_session",
                    new_callable=AsyncMock,
                    return_value=service_result,
                ):
                    response = await self._post_with_cookie(
                        f"{case}-logout-api-test-credential"
                    )
                responses.append(response)
                self.assertTrue(self.session.transaction_committed)
                self._assert_deletion_cookie(response)

        self.assertEqual(
            {(response.status_code, response.content) for response in responses},
            {(204, b"")},
        )

    async def test_repeated_logout_is_idempotent(self) -> None:
        results = iter((True, True))

        async def revoke(session, raw_token):
            return next(results)

        with patch(
            "app.api.v1.auth.revoke_refresh_session",
            side_effect=revoke,
        ) as revoke_mock:
            first = await self._post_with_cookie(
                "repeat-logout-api-test-credential"
            )
            self.session = _FakeAsyncSession()
            second = await self._post_with_cookie(
                "repeat-logout-api-test-credential"
            )

        self.assertEqual(first.status_code, 204)
        self.assertEqual(second.status_code, 204)
        self.assertEqual(first.content, second.content)
        self.assertEqual(revoke_mock.await_count, 2)
        self._assert_deletion_cookie(first)
        self._assert_deletion_cookie(second)

    async def test_bearer_is_not_required_and_expired_access_is_ignored(
        self,
    ) -> None:
        expired_access_token = _expired_access_token()

        with patch(
            "app.api.v1.auth.revoke_refresh_session",
            new_callable=AsyncMock,
            return_value=False,
        ):
            without_bearer = await self._post_with_cookie(
                "no-bearer-logout-api-test-credential"
            )
            self.session = _FakeAsyncSession()
            with_expired_bearer = await self._post_with_cookie(
                "expired-bearer-logout-api-test-credential",
                authorization=f"Bearer {expired_access_token}",
            )

        self.assertEqual(without_bearer.status_code, 204)
        self.assertEqual(with_expired_bearer.status_code, 204)
        self.assertEqual(without_bearer.content, b"")
        self.assertEqual(with_expired_bearer.content, b"")

    async def test_persistence_failure_returns_safe_503_and_clears_cookie(
        self,
    ) -> None:
        with patch(
            "app.api.v1.auth.revoke_refresh_session",
            new_callable=AsyncMock,
            side_effect=RefreshSessionPersistenceError(
                "private persistence detail"
            ),
        ):
            response = await self._post_with_cookie(
                "persistence-failure-logout-api-test-credential"
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Logout is temporarily unavailable."},
        )
        self.assertTrue(self.session.transaction_rolled_back)
        self.assertFalse(self.session.transaction_committed)
        self.assertNotIn("private", response.text)
        self.assertNotIn("session", response.text.lower())
        self._assert_deletion_cookie(response)
        self._assert_cache_headers(response)

    async def test_commit_failure_returns_safe_503_and_clears_cookie(self) -> None:
        self.session.commit_error = SQLAlchemyError("private commit detail")

        with patch(
            "app.api.v1.auth.revoke_refresh_session",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await self._post_with_cookie(
                "commit-failure-logout-api-test-credential"
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Logout is temporarily unavailable."},
        )
        self.assertFalse(self.session.transaction_committed)
        self.assertTrue(self.session.transaction_rolled_back)
        self.assertNotIn("private", response.text)
        self._assert_deletion_cookie(response)
        self._assert_cache_headers(response)

    async def test_success_exposes_no_authentication_internals(self) -> None:
        with patch(
            "app.api.v1.auth.revoke_refresh_session",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await self._post_with_cookie(
                "privacy-logout-api-test-credential"
            )

        self.assertEqual(response.content, b"")
        self.assertNotIn("content-type", response.headers)

    async def _post_with_cookie(
        self,
        raw_token: str,
        *,
        authorization: str | None = None,
    ) -> httpx.Response:
        headers = {
            "Cookie": f"{settings.auth_refresh_cookie_name}={raw_token}"
        }
        if authorization is not None:
            headers["Authorization"] = authorization
        return await self.client.post("/api/v1/auth/logout", headers=headers)

    def _assert_deletion_cookie(self, response: httpx.Response) -> None:
        header = response.headers["set-cookie"].lower()
        self.assertIn(f"{settings.auth_refresh_cookie_name}=", header)
        self.assertIn("max-age=0", header)
        self.assertIn("httponly", header)
        self.assertIn("path=/api/v1/auth", header)
        self.assertIn("samesite=lax", header)

    def _assert_cache_headers(self, response: httpx.Response) -> None:
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")


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
    def __init__(
        self,
        *,
        auth_sessions: list[AuthSession] | None = None,
    ) -> None:
        self.begin_calls = 0
        self.transaction_active = False
        self.transaction_committed = False
        self.transaction_rolled_back = False
        self.commit_error: SQLAlchemyError | None = None
        self.auth_sessions = list(auth_sessions or [])
        self.lock_statements: list[object] = []
        self.flush_calls = 0

    def begin(self) -> _FakeTransaction:
        self.begin_calls += 1
        return _FakeTransaction(self)

    def begin_nested(self) -> _FakeNestedTransaction:
        return _FakeNestedTransaction()

    async def scalar(self, statement: object) -> AuthSession | None:
        self.lock_statements.append(statement)
        parameters = statement.compile().params
        token_hash = next(
            value
            for name, value in parameters.items()
            if name == "token_hash" or name.startswith("token_hash_")
        )
        return next(
            (
                auth_session
                for auth_session in self.auth_sessions
                if auth_session.token_hash == token_hash
            ),
            None,
        )

    async def flush(self) -> None:
        self.flush_calls += 1


def _make_auth_session(raw_token: str) -> AuthSession:
    now = datetime.now(UTC)
    auth_session = AuthSession(
        id=uuid4(),
        user_id=uuid4(),
        token_hash=hash_refresh_token(raw_token),
        family_id=uuid4(),
        expires_at=now + timedelta(days=30),
        last_used_at=None,
        revoked_at=None,
        replaced_by_session_id=None,
    )
    auth_session.created_at = now
    auth_session.updated_at = now
    return auth_session


def _expired_access_token() -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(uuid4()),
            "jti": str(uuid4()),
            "iat": now - timedelta(minutes=20),
            "nbf": now - timedelta(minutes=20),
            "exp": now - timedelta(minutes=5),
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
            "type": "access",
        },
        settings.auth_secret_key.get_secret_value(),
        algorithm=settings.auth_algorithm,
    )
