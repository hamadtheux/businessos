import os
import unittest
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError


TEST_DATABASE_URL = "postgresql+asyncpg://database.invalid/test"
TEST_AUTH_SECRET = "x" * 32
os.environ["AIBOS_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["AIBOS_AUTH_SECRET_KEY"] = TEST_AUTH_SECRET

from app.api.dependencies import business as business_dependencies  # noqa: E402
from app.api.dependencies.auth import get_current_user  # noqa: E402
from app.api.dependencies.business import (  # noqa: E402
    BusinessAccessContext,
    BusinessAccessDependency,
    get_business_access,
)
from app.db.session import get_db_session  # noqa: E402
from app.main import app as production_app  # noqa: E402
from app.models.business import Business  # noqa: E402
from app.models.business_membership import BusinessMembership  # noqa: E402
from app.models.user import User  # noqa: E402


class BusinessAccessDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user = self._user(email="member@example.com")
        self.other_user = self._user(email="other@example.com")
        self.business = self._business(name="Confidential Tenant A", slug="tenant-a")
        self.membership = self._membership(
            business=self.business,
            user=self.user,
        )
        self.session = _FakeAsyncSession(
            businesses=[self.business],
            memberships=[self.membership],
        )
        self.test_app = FastAPI()

        @self.test_app.get("/businesses/{business_id}/resource")
        async def protected_business_resource(
            access: BusinessAccessDependency,
        ) -> dict[str, str]:
            return {
                "user_id": str(access.user.id),
                "business_id": str(access.business.id),
                "membership_id": str(access.membership.id),
            }

        async def override_session():
            yield self.session

        async def override_current_user() -> User:
            return self.user

        self.test_app.dependency_overrides[get_db_session] = override_session
        self.test_app.dependency_overrides[
            get_current_user
        ] = override_current_user
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.test_app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_active_membership_and_business_return_exact_context(
        self,
    ) -> None:
        context = await get_business_access(
            self.business.id,
            self.user,
            self.session,
        )

        self.assertIsInstance(context, BusinessAccessContext)
        self.assertIs(context.user, self.user)
        self.assertIs(context.business, self.business)
        self.assertIs(context.membership, self.membership)
        with self.assertRaises(FrozenInstanceError):
            context.business = self._business(
                name="Replacement",
                slug="replacement",
            )

    async def test_query_is_scoped_by_user_and_business_ids(self) -> None:
        await get_business_access(
            self.business.id,
            self.user,
            self.session,
        )

        self.assertEqual(self.session.requested_user_id, self.user.id)
        self.assertEqual(self.session.requested_business_id, self.business.id)
        self.assertIn(
            "business_memberships.user_id",
            self.session.executed_sql,
        )
        self.assertIn(
            "business_memberships.business_id",
            self.session.executed_sql,
        )

    async def test_missing_membership_and_business_have_identical_404s(
        self,
    ) -> None:
        self.session.memberships = []
        existing_business_response = await self._request(self.business.id)
        nonexistent_business_response = await self._request(uuid4())

        self.assertEqual(existing_business_response.status_code, 404)
        self.assertEqual(nonexistent_business_response.status_code, 404)
        self.assertEqual(
            existing_business_response.json(),
            nonexistent_business_response.json(),
        )
        self.assertEqual(
            existing_business_response.json(),
            {"detail": "Business not found."},
        )

    async def test_different_users_membership_does_not_grant_access(self) -> None:
        self.session.memberships = [
            self._membership(
                business=self.business,
                user=self.other_user,
                role="owner",
            )
        ]

        response = await self._request(self.business.id)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Business not found."})

    async def test_membership_for_business_a_does_not_grant_business_b(
        self,
    ) -> None:
        business_b = self._business(
            name="Confidential Tenant B",
            slug="tenant-b",
        )
        self.session.businesses.append(business_b)

        response = await self._request(business_b.id)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Business not found."})

    async def test_invited_and_suspended_memberships_return_403(self) -> None:
        for membership_status in ("invited", "suspended"):
            with self.subTest(membership_status=membership_status):
                self.membership.status = membership_status

                response = await self._request(self.business.id)

                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    response.json(),
                    {"detail": "Business access is unavailable."},
                )

    async def test_inactive_and_suspended_businesses_return_403(self) -> None:
        for business_status in ("inactive", "suspended"):
            with self.subTest(business_status=business_status):
                self.business.status = business_status

                response = await self._request(self.business.id)

                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    response.json(),
                    {"detail": "Business access is unavailable."},
                )

    async def test_database_failure_returns_safe_503(self) -> None:
        self.session.execute_error = SQLAlchemyError("internal lookup failure")

        response = await self._request(self.business.id)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Business access is temporarily unavailable."},
        )

    async def test_authentication_dependency_handles_unauthenticated_user(
        self,
    ) -> None:
        del self.test_app.dependency_overrides[get_current_user]

        response = await self._request(self.business.id)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"detail": "Invalid or expired authentication token."},
        )
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(self.session.execute_calls, 0)

    async def test_tenant_dependency_does_not_decode_jwt(self) -> None:
        self.assertFalse(hasattr(business_dependencies, "decode_access_token"))

        with patch(
            "app.api.dependencies.auth.decode_access_token"
        ) as decode_token_mock:
            response = await self._request(self.business.id)

        self.assertEqual(response.status_code, 200)
        decode_token_mock.assert_not_called()

    async def test_dependency_performs_no_writes_or_active_business_changes(
        self,
    ) -> None:
        original_active_business_id = uuid4()
        self.session.active_business_id = original_active_business_id
        original_user_values = (
            self.user.email,
            self.user.status,
            self.user.is_email_verified,
        )

        response = await self._request(self.business.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.session.commit_calls, 0)
        self.assertEqual(self.session.flush_calls, 0)
        self.assertEqual(self.session.added, [])
        self.assertEqual(
            self.session.active_business_id,
            original_active_business_id,
        )
        self.assertEqual(
            (
                self.user.email,
                self.user.status,
                self.user.is_email_verified,
            ),
            original_user_values,
        )

    async def test_unauthorized_response_does_not_leak_tenant_data(self) -> None:
        protected_membership = self._membership(
            business=self.business,
            user=self.other_user,
            role="owner",
            status="suspended",
        )
        self.session.memberships = [protected_membership]

        response = await self._request(self.business.id)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Business not found."})
        response_text = response.text.lower()
        self.assertNotIn(self.business.name.lower(), response_text)
        self.assertNotIn(protected_membership.role.lower(), response_text)
        self.assertNotIn(protected_membership.status.lower(), response_text)

    async def test_existing_auth_and_status_routes_remain_available(self) -> None:
        paths = production_app.openapi()["paths"]

        self.assertIn("post", paths["/api/v1/auth/register"])
        self.assertIn("post", paths["/api/v1/auth/login"])
        self.assertIn("get", paths["/api/v1/auth/me"])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=production_app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/v1/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "available", "api_version": "v1"},
        )

    async def _request(self, business_id: UUID) -> httpx.Response:
        return await self.client.get(f"/businesses/{business_id}/resource")

    def _user(self, *, email: str) -> User:
        user = User(
            email=email,
            password_hash="stored-test-hash",
            first_name="Test",
            last_name="User",
            status="active",
            is_email_verified=True,
        )
        user.id = uuid4()
        user.created_at = datetime.now(UTC)
        user.updated_at = user.created_at
        return user

    def _business(self, *, name: str, slug: str) -> Business:
        business = Business(
            name=name,
            slug=slug,
            business_type="services",
            status="active",
            timezone="UTC",
            currency="USD",
            locale="en",
        )
        business.id = uuid4()
        business.created_at = datetime.now(UTC)
        business.updated_at = business.created_at
        return business

    def _membership(
        self,
        *,
        business: Business,
        user: User,
        role: str = "member",
        status: str = "active",
    ) -> BusinessMembership:
        membership = BusinessMembership(
            business_id=business.id,
            user_id=user.id,
            role=role,
            status=status,
        )
        membership.id = uuid4()
        membership.created_at = datetime.now(UTC)
        membership.updated_at = membership.created_at
        return membership


class _FakeResult:
    def __init__(
        self,
        access_row: tuple[Business, BusinessMembership] | None,
    ) -> None:
        self.access_row = access_row

    def one_or_none(self) -> tuple[Business, BusinessMembership] | None:
        return self.access_row


class _FakeAsyncSession:
    def __init__(
        self,
        *,
        businesses: list[Business],
        memberships: list[BusinessMembership],
    ) -> None:
        self.businesses = businesses
        self.memberships = memberships
        self.execute_calls = 0
        self.commit_calls = 0
        self.flush_calls = 0
        self.added: list[object] = []
        self.execute_error: SQLAlchemyError | None = None
        self.executed_sql = ""
        self.requested_user_id: UUID | None = None
        self.requested_business_id: UUID | None = None
        self.active_business_id: UUID | None = None

    async def execute(self, statement: object) -> _FakeResult:
        self.execute_calls += 1
        if self.execute_error is not None:
            raise self.execute_error

        self.executed_sql = str(statement)
        parameters = statement.compile().params
        self.requested_user_id = next(
            value
            for name, value in parameters.items()
            if name.startswith("user_id")
        )
        self.requested_business_id = next(
            value
            for name, value in parameters.items()
            if name.startswith("business_id")
        )

        membership = next(
            (
                candidate
                for candidate in self.memberships
                if candidate.user_id == self.requested_user_id
                and candidate.business_id == self.requested_business_id
            ),
            None,
        )
        business = next(
            (
                candidate
                for candidate in self.businesses
                if candidate.id == self.requested_business_id
            ),
            None,
        )
        access_row = (
            (business, membership)
            if business is not None and membership is not None
            else None
        )
        return _FakeResult(access_row)

    async def commit(self) -> None:
        self.commit_calls += 1

    async def flush(self) -> None:
        self.flush_calls += 1

    def add(self, instance: object) -> None:
        self.added.append(instance)
