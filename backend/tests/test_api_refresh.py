import os
import unittest
from datetime import UTC, datetime, timedelta
from types import TracebackType
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from sqlalchemy.exc import SQLAlchemyError


os.environ["AIBOS_DATABASE_URL"] = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = "x" * 32

from app.core.config import settings  # noqa: E402
from app.core.refresh_tokens import hash_refresh_token  # noqa: E402
from app.core.security import (  # noqa: E402
    create_access_token as issue_access_token,
    decode_access_token,
)
from app.db.session import get_db_session  # noqa: E402
from app.exceptions.auth import (  # noqa: E402
    InvalidRefreshTokenError,
    RefreshSessionPersistenceError,
    RefreshTokenReuseDetectedError,
    UserAccountUnavailableError,
)
from app.main import app  # noqa: E402
from app.models.auth_session import AuthSession  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_session import RotatedRefreshSession  # noqa: E402


class RefreshApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user = _make_user()
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

    def test_refresh_route_has_typed_bodyless_openapi_contract(self) -> None:
        paths = app.openapi()["paths"]
        operation = paths["/api/v1/auth/refresh"]["post"]
        response_schema = operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"]

        self.assertNotIn("requestBody", operation)
        self.assertNotIn("security", operation)
        self.assertEqual(len(operation["parameters"]), 1)
        cookie_parameter = operation["parameters"][0]
        self.assertEqual(
            cookie_parameter["name"],
            settings.auth_refresh_cookie_name,
        )
        self.assertEqual(cookie_parameter["in"], "cookie")
        self.assertFalse(cookie_parameter["required"])
        self.assertEqual(
            response_schema["$ref"],
            "#/components/schemas/UserLoginResponse",
        )

    async def test_missing_cookie_returns_generic_uncacheable_401(self) -> None:
        with patch(
            "app.api.v1.auth.rotate_refresh_session",
            new_callable=AsyncMock,
        ) as rotate_mock:
            response = await self.client.post("/api/v1/auth/refresh")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"detail": "Invalid or expired session."},
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(self.session.begin_calls, 0)
        rotate_mock.assert_not_awaited()

    async def test_valid_cookie_rotates_after_commit_and_returns_access_token(
        self,
    ) -> None:
        old_token = "old-valid-api-test-token"
        new_token = "new-valid-api-test-token"
        rotated = _rotation(self.user, new_token)

        def create_token_after_commit(user_id):
            self.assertTrue(self.session.transaction_committed)
            self.assertFalse(self.session.transaction_active)
            return issue_access_token(user_id)

        with (
            patch(
                "app.api.v1.auth.rotate_refresh_session",
                new_callable=AsyncMock,
                return_value=rotated,
            ) as rotate_mock,
            patch(
                "app.api.v1.auth.create_access_token",
                side_effect=create_token_after_commit,
            ) as access_mock,
        ):
            response = await self._post_with_cookie(old_token)

        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertEqual(
            set(response_data),
            {"access_token", "token_type", "expires_in", "user"},
        )
        self.assertEqual(response_data["token_type"], "bearer")
        self.assertEqual(
            response_data["expires_in"],
            settings.auth_access_token_expire_minutes * 60,
        )
        self.assertEqual(
            decode_access_token(response_data["access_token"]).sub,
            self.user.id,
        )
        self.assertNotIn("refresh_token", response_data)
        self.assertNotIn(new_token, response.text)
        self.assertNotEqual(old_token, new_token)
        self.assertEqual(
            response.cookies.get(settings.auth_refresh_cookie_name),
            new_token,
        )
        set_cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", set_cookie)
        self.assertIn("path=/api/v1/auth", set_cookie)
        self.assertIn("samesite=lax", set_cookie)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertTrue(self.session.transaction_committed)
        self.assertFalse(self.session.transaction_rolled_back)
        rotate_mock.assert_awaited_once_with(self.session, old_token)
        access_mock.assert_called_once_with(self.user.id)

    async def test_rotation_commit_failure_issues_no_token_or_cookie(self) -> None:
        self.session.commit_error = SQLAlchemyError("private commit failure")
        rotated = _rotation(self.user, "unused-new-api-test-token")

        with (
            patch(
                "app.api.v1.auth.rotate_refresh_session",
                new_callable=AsyncMock,
                return_value=rotated,
            ),
            patch("app.api.v1.auth.create_access_token") as access_mock,
        ):
            response = await self._post_with_cookie(
                "commit-failure-api-test-token"
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Session refresh is temporarily unavailable."},
        )
        self.assertTrue(self.session.transaction_rolled_back)
        self.assertFalse(self.session.transaction_committed)
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotIn("access_token", response.json())
        access_mock.assert_not_called()

    async def test_persistence_failure_is_safe_and_rolls_back(self) -> None:
        with (
            patch(
                "app.api.v1.auth.rotate_refresh_session",
                new_callable=AsyncMock,
                side_effect=RefreshSessionPersistenceError(
                    "private persistence failure"
                ),
            ),
            patch("app.api.v1.auth.create_access_token") as access_mock,
        ):
            response = await self._post_with_cookie(
                "persistence-failure-api-test-token"
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Session refresh is temporarily unavailable."},
        )
        self.assertTrue(self.session.transaction_rolled_back)
        self.assertFalse(self.session.transaction_committed)
        self.assertNotIn("set-cookie", response.headers)
        access_mock.assert_not_called()

    async def test_reuse_commits_family_revocation_and_clears_cookie(self) -> None:
        family_id = uuid4()
        descendants = [
            _auth_session(self.user.id, family_id=family_id),
            _auth_session(self.user.id, family_id=family_id),
        ]

        async def revoke_family_then_raise(session, raw_token):
            revoked_at = datetime.now(UTC)
            for auth_session in descendants:
                auth_session.revoked_at = revoked_at
            raise RefreshTokenReuseDetectedError("private replay detail")

        with (
            patch(
                "app.api.v1.auth.rotate_refresh_session",
                side_effect=revoke_family_then_raise,
            ),
            patch("app.api.v1.auth.create_access_token") as access_mock,
        ):
            response = await self._post_with_cookie(
                "replayed-old-api-test-token"
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"detail": "Invalid or expired session."},
        )
        self.assertTrue(self.session.transaction_committed)
        self.assertFalse(self.session.transaction_rolled_back)
        self.assertTrue(all(item.revoked_at is not None for item in descendants))
        self.assertNotIn("replay", response.text.lower())
        self.assertNotIn("reuse", response.text.lower())
        self.assertNotIn("access_token", response.json())
        deletion = response.headers["set-cookie"].lower()
        self.assertIn("max-age=0", deletion)
        self.assertIn("httponly", deletion)
        self.assertIn("path=/api/v1/auth", deletion)
        self.assertIn("samesite=lax", deletion)
        access_mock.assert_not_called()

    async def test_reuse_revocation_commit_failure_returns_safe_503(self) -> None:
        self.session.commit_error = SQLAlchemyError(
            "private security commit failure"
        )

        async def revoke_then_raise(session, raw_token):
            raise RefreshTokenReuseDetectedError("private replay detail")

        with (
            patch(
                "app.api.v1.auth.rotate_refresh_session",
                side_effect=revoke_then_raise,
            ),
            patch("app.api.v1.auth.create_access_token") as access_mock,
        ):
            response = await self._post_with_cookie(
                "replay-commit-failure-api-test-token"
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Session refresh is temporarily unavailable."},
        )
        self.assertFalse(self.session.transaction_committed)
        self.assertTrue(self.session.transaction_rolled_back)
        self.assertNotIn("set-cookie", response.headers)
        access_mock.assert_not_called()

    async def test_unavailable_accounts_commit_security_revocation(self) -> None:
        for account_status in ("inactive", "suspended"):
            with self.subTest(account_status=account_status):
                self.session = _FakeAsyncSession()
                descendant = _auth_session(self.user.id)

                async def revoke_then_raise(session, raw_token):
                    descendant.revoked_at = datetime.now(UTC)
                    raise UserAccountUnavailableError(
                        "private account-state detail"
                    )

                with (
                    patch(
                        "app.api.v1.auth.rotate_refresh_session",
                        side_effect=revoke_then_raise,
                    ),
                    patch(
                        "app.api.v1.auth.create_access_token"
                    ) as access_mock,
                ):
                    response = await self._post_with_cookie(
                        f"{account_status}-account-api-test-token"
                    )

                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    response.json(),
                    {"detail": "This account is unavailable."},
                )
                self.assertTrue(self.session.transaction_committed)
                self.assertFalse(self.session.transaction_rolled_back)
                self.assertIsNotNone(descendant.revoked_at)
                self.assertNotIn("access_token", response.json())
                self.assertIn(
                    "max-age=0",
                    response.headers["set-cookie"].lower(),
                )
                access_mock.assert_not_called()

    async def test_invalid_session_causes_are_indistinguishable(self) -> None:
        responses: list[httpx.Response] = []
        for cause in ("unknown", "expired", "revoked"):
            with self.subTest(cause=cause):
                self.session = _FakeAsyncSession()
                with (
                    patch(
                        "app.api.v1.auth.rotate_refresh_session",
                        new_callable=AsyncMock,
                        side_effect=InvalidRefreshTokenError(
                            f"private {cause} detail"
                        ),
                    ),
                    patch(
                        "app.api.v1.auth.create_access_token"
                    ) as access_mock,
                ):
                    response = await self._post_with_cookie(
                        f"{cause}-invalid-api-test-token"
                    )
                responses.append(response)
                self.assertTrue(self.session.transaction_rolled_back)
                self.assertFalse(self.session.transaction_committed)
                self.assertIn(
                    "max-age=0",
                    response.headers["set-cookie"].lower(),
                )
                access_mock.assert_not_called()

        self.assertTrue(all(response.status_code == 401 for response in responses))
        self.assertEqual(
            {response.text for response in responses},
            {'{"detail":"Invalid or expired session."}'},
        )

    async def test_cors_remains_explicit_for_cookie_refresh(self) -> None:
        allowed_origin = settings.cors_origins[0]
        allowed_response = await self.client.options(
            "/api/v1/auth/refresh",
            headers={
                "Origin": allowed_origin,
                "Access-Control-Request-Method": "POST",
            },
        )
        rejected_response = await self.client.options(
            "/api/v1/auth/refresh",
            headers={
                "Origin": "https://cross-site.invalid",
                "Access-Control-Request-Method": "POST",
            },
        )

        self.assertNotIn("*", settings.cors_origins)
        self.assertEqual(
            allowed_response.headers["Access-Control-Allow-Origin"],
            allowed_origin,
        )
        self.assertNotIn(
            "Access-Control-Allow-Origin",
            rejected_response.headers,
        )

    async def _post_with_cookie(self, raw_token: str) -> httpx.Response:
        return await self.client.post(
            "/api/v1/auth/refresh",
            headers={
                "Cookie": f"{settings.auth_refresh_cookie_name}={raw_token}"
            },
        )


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


class _FakeAsyncSession:
    def __init__(self) -> None:
        self.begin_calls = 0
        self.transaction_active = False
        self.transaction_committed = False
        self.transaction_rolled_back = False
        self.commit_error: SQLAlchemyError | None = None

    def begin(self) -> _FakeTransaction:
        self.begin_calls += 1
        return _FakeTransaction(self)


def _make_user() -> User:
    now = datetime.now(UTC)
    user = User(
        id=uuid4(),
        email="refresh-api-user@example.com",
        password_hash="stored-test-hash",
        first_name="Refresh",
        last_name="API",
        status="active",
        is_email_verified=True,
    )
    user.created_at = now
    user.updated_at = now
    return user


def _auth_session(
    user_id: UUID,
    *,
    family_id: UUID | None = None,
    raw_token: str = "stored-api-test-token",
) -> AuthSession:
    now = datetime.now(UTC)
    auth_session = AuthSession(
        id=uuid4(),
        user_id=user_id,
        token_hash=hash_refresh_token(raw_token),
        family_id=family_id or uuid4(),
        expires_at=now + timedelta(days=30),
        last_used_at=None,
        revoked_at=None,
        replaced_by_session_id=None,
    )
    auth_session.created_at = now
    auth_session.updated_at = now
    return auth_session


def _rotation(user: User, raw_token: str) -> RotatedRefreshSession:
    return RotatedRefreshSession(
        user=user,
        session=_auth_session(user.id, raw_token=raw_token),
        refresh_token=raw_token,
    )
