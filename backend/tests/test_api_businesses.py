import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from sqlalchemy.exc import SQLAlchemyError


TEST_DATABASE_URL = "postgresql+asyncpg://database.invalid/test"
TEST_AUTH_SECRET = "x" * 32
os.environ["AIBOS_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["AIBOS_AUTH_SECRET_KEY"] = TEST_AUTH_SECRET

from app.api.dependencies.auth import get_current_user  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.exceptions.business import BusinessListingPersistenceError  # noqa: E402
from app.main import app  # noqa: E402
from app.models.business import Business  # noqa: E402
from app.models.business_membership import BusinessMembership  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.business import (  # noqa: E402
    AccessibleBusiness,
    list_accessible_businesses,
)


class BusinessListingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_accessible_businesses_returns_empty_list(self) -> None:
        user = _make_user("member@example.com")
        session = _FakeAsyncSession()

        businesses = await list_accessible_businesses(session, user.id)

        self.assertEqual(businesses, [])

    async def test_result_is_typed_and_contains_current_membership_role(
        self,
    ) -> None:
        user = _make_user("member@example.com")
        business = _make_business("Tenant A", "tenant-a")
        membership = _make_membership(business, user, role="viewer")
        session = _FakeAsyncSession(
            businesses=[business],
            memberships=[membership],
        )

        businesses = await list_accessible_businesses(session, user.id)

        self.assertEqual(len(businesses), 1)
        self.assertIsInstance(businesses[0], AccessibleBusiness)
        self.assertIs(businesses[0].business, business)
        self.assertEqual(businesses[0].membership_role, "viewer")

    async def test_query_scopes_filters_and_orders_in_database(self) -> None:
        user = _make_user("member@example.com")
        session = _FakeAsyncSession()

        await list_accessible_businesses(session, user.id)

        self.assertEqual(session.requested_user_id, user.id)
        self.assertIn("JOIN business_memberships", session.executed_sql)
        self.assertIn(
            "business_memberships.user_id",
            session.executed_sql,
        )
        self.assertIn(
            "business_memberships.status",
            session.executed_sql,
        )
        self.assertIn("businesses.status", session.executed_sql)
        self.assertEqual(session.bound_values.count("active"), 2)
        self.assertIn(
            "ORDER BY businesses.created_at ASC, businesses.id ASC",
            session.executed_sql,
        )

    async def test_tenant_isolation_and_status_filters_are_enforced(self) -> None:
        user_a = _make_user("user-a@example.com")
        user_b = _make_user("user-b@example.com")
        created_at = datetime(2026, 1, 1, tzinfo=UTC)

        first = _make_business(
            "First",
            "first",
            created_at=created_at,
            business_id=UUID(int=2),
        )
        second = _make_business(
            "Second",
            "second",
            created_at=created_at + timedelta(minutes=1),
            business_id=UUID(int=1),
        )
        third = _make_business(
            "Third",
            "third",
            created_at=created_at + timedelta(minutes=1),
            business_id=UUID(int=3),
        )
        other_users_business = _make_business("Other", "other")
        no_membership_business = _make_business("No Membership", "no-membership")
        invited_business = _make_business("Invited", "invited")
        suspended_membership_business = _make_business(
            "Suspended Membership",
            "suspended-membership",
        )
        inactive_business = _make_business(
            "Inactive",
            "inactive",
            status="inactive",
        )
        suspended_business = _make_business(
            "Suspended",
            "suspended",
            status="suspended",
        )
        businesses = [
            third,
            other_users_business,
            invited_business,
            first,
            inactive_business,
            no_membership_business,
            second,
            suspended_membership_business,
            suspended_business,
        ]
        memberships = [
            _make_membership(first, user_a, role="owner"),
            _make_membership(second, user_a, role="member"),
            _make_membership(third, user_a, role="viewer"),
            _make_membership(other_users_business, user_b, role="owner"),
            _make_membership(invited_business, user_a, status="invited"),
            _make_membership(
                suspended_membership_business,
                user_a,
                status="suspended",
            ),
            _make_membership(inactive_business, user_a),
            _make_membership(suspended_business, user_a),
        ]
        session = _FakeAsyncSession(
            businesses=businesses,
            memberships=memberships,
        )

        accessible = await list_accessible_businesses(session, user_a.id)

        self.assertEqual(
            [item.business for item in accessible],
            [first, second, third],
        )
        self.assertEqual(
            [item.membership_role for item in accessible],
            ["owner", "member", "viewer"],
        )
        self.assertEqual(
            len({item.business.id for item in accessible}),
            len(accessible),
        )

    async def test_database_failure_raises_domain_exception(self) -> None:
        user = _make_user("member@example.com")
        session = _FakeAsyncSession(
            execute_error=SQLAlchemyError("internal listing failure")
        )

        with self.assertRaises(BusinessListingPersistenceError) as raised:
            await list_accessible_businesses(session, user.id)

        self.assertNotIsInstance(raised.exception, SQLAlchemyError)

    async def test_service_performs_no_writes_or_commits(self) -> None:
        user = _make_user("member@example.com")
        business = _make_business("Tenant A", "tenant-a")
        session = _FakeAsyncSession(
            businesses=[business],
            memberships=[_make_membership(business, user)],
        )

        await list_accessible_businesses(session, user.id)

        self.assertEqual(session.commit_calls, 0)
        self.assertEqual(session.flush_calls, 0)
        self.assertEqual(session.added, [])


class BusinessListingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user = _make_user("member@example.com")
        self.session = _FakeAsyncSession()
        self.original_dependency_overrides = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        async def override_current_user() -> User:
            return self.user

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_user] = override_current_user
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_dependency_overrides)

    def test_endpoint_has_authenticated_list_openapi_contract(self) -> None:
        openapi = app.openapi()
        operation = openapi["paths"]["/api/v1/businesses"]["get"]
        response_schema = operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"]

        self.assertEqual(response_schema["type"], "array")
        self.assertEqual(
            response_schema["items"]["$ref"],
            "#/components/schemas/BusinessSummary",
        )
        self.assertTrue(operation["security"])

    async def test_authentication_is_required(self) -> None:
        del app.dependency_overrides[get_current_user]

        response = await self.client.get("/api/v1/businesses")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(self.session.execute_calls, 0)

    async def test_zero_businesses_returns_safe_empty_response(self) -> None:
        response = await self.client.get("/api/v1/businesses")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")

    async def test_accessible_business_returns_safe_summary(self) -> None:
        business = _make_business("Tenant A", "tenant-a")
        membership = _make_membership(business, self.user, role="manager")
        self.session.businesses = [business]
        self.session.memberships = [membership]

        with patch("app.core.security.create_access_token") as token_mock:
            response = await self.client.get("/api/v1/businesses")

        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertEqual(len(response_data), 1)
        self.assertEqual(
            set(response_data[0]),
            {
                "id",
                "name",
                "slug",
                "business_type",
                "status",
                "timezone",
                "currency",
                "locale",
                "website_url",
                "location",
                "description",
                "brand_voice",
                "avoid_keywords",
                "membership_role",
                "created_at",
            },
        )
        self.assertEqual(response_data[0]["id"], str(business.id))
        self.assertEqual(response_data[0]["membership_role"], "manager")
        self.assertNotIn("membership_id", response_data[0])
        self.assertNotIn("password", response_data[0])
        self.assertNotIn("password_hash", response_data[0])
        self.assertNotIn("access_token", response_data[0])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(self.session.commit_calls, 0)
        self.assertEqual(self.session.flush_calls, 0)
        self.assertEqual(self.session.added, [])
        token_mock.assert_not_called()

    async def test_multiple_businesses_are_isolated_filtered_and_ordered(
        self,
    ) -> None:
        other_user = _make_user("other@example.com")
        created_at = datetime(2026, 1, 1, tzinfo=UTC)
        first = _make_business(
            "First",
            "first",
            created_at=created_at,
            business_id=UUID(int=1),
        )
        second = _make_business(
            "Second",
            "second",
            created_at=created_at + timedelta(minutes=1),
            business_id=UUID(int=2),
        )
        hidden = _make_business("Hidden", "hidden")
        invited = _make_business("Invited", "invited")
        suspended_membership = _make_business(
            "Suspended Membership",
            "suspended-membership",
        )
        inactive = _make_business("Inactive", "inactive", status="inactive")
        suspended = _make_business(
            "Suspended",
            "suspended",
            status="suspended",
        )
        self.session.businesses = [
            suspended,
            second,
            hidden,
            inactive,
            invited,
            first,
            suspended_membership,
        ]
        self.session.memberships = [
            _make_membership(first, self.user, role="owner"),
            _make_membership(second, self.user, role="viewer"),
            _make_membership(hidden, other_user, role="owner"),
            _make_membership(invited, self.user, status="invited"),
            _make_membership(
                suspended_membership,
                self.user,
                status="suspended",
            ),
            _make_membership(inactive, self.user),
            _make_membership(suspended, self.user),
        ]

        response = await self.client.get("/api/v1/businesses")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in response.json()],
            [str(first.id), str(second.id)],
        )
        self.assertEqual(
            [item["membership_role"] for item in response.json()],
            ["owner", "viewer"],
        )

    async def test_database_failure_maps_to_safe_503(self) -> None:
        self.session.execute_error = SQLAlchemyError("internal listing failure")

        response = await self.client.get("/api/v1/businesses")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "detail": {
                    "code": "temporary_failure",
                    "message": (
                        "Business data could not be loaded because the API "
                        "could not read the workspace records. Please try "
                        "again."
                    ),
                }
            },
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_existing_routes_remain_available(self) -> None:
        paths = app.openapi()["paths"]

        self.assertIn("post", paths["/api/v1/auth/register"])
        self.assertIn("post", paths["/api/v1/auth/login"])
        self.assertIn("get", paths["/api/v1/auth/me"])

        response = await self.client.get("/api/v1/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "available", "api_version": "v1"},
        )


class _FakeResult:
    def __init__(self, rows: list[tuple[Business, str]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[Business, str]]:
        return self.rows


class _FakeAsyncSession:
    def __init__(
        self,
        *,
        businesses: list[Business] | None = None,
        memberships: list[BusinessMembership] | None = None,
        execute_error: SQLAlchemyError | None = None,
    ) -> None:
        self.businesses = businesses or []
        self.memberships = memberships or []
        self.execute_error = execute_error
        self.execute_calls = 0
        self.commit_calls = 0
        self.flush_calls = 0
        self.added: list[object] = []
        self.executed_sql = ""
        self.bound_values: list[object] = []
        self.requested_user_id: UUID | None = None

    async def execute(self, statement: object) -> _FakeResult:
        self.execute_calls += 1
        if self.execute_error is not None:
            raise self.execute_error

        self.executed_sql = str(statement)
        parameters = statement.compile().params
        self.bound_values = list(parameters.values())
        self.requested_user_id = next(
            value
            for name, value in parameters.items()
            if name.startswith("user_id")
        )

        rows: list[tuple[Business, str]] = []
        for membership in self.memberships:
            if (
                membership.user_id != self.requested_user_id
                or membership.status != "active"
            ):
                continue
            business = next(
                (
                    candidate
                    for candidate in self.businesses
                    if candidate.id == membership.business_id
                    and candidate.status == "active"
                ),
                None,
            )
            if business is not None:
                rows.append((business, membership.role))

        rows.sort(key=lambda row: (row[0].created_at, row[0].id))
        return _FakeResult(rows)

    async def commit(self) -> None:
        self.commit_calls += 1

    async def flush(self) -> None:
        self.flush_calls += 1

    def add(self, instance: object) -> None:
        self.added.append(instance)


def _make_user(email: str) -> User:
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


def _make_business(
    name: str,
    slug: str,
    *,
    status: str = "active",
    created_at: datetime | None = None,
    business_id: UUID | None = None,
) -> Business:
    business = Business(
        name=name,
        slug=slug,
        business_type="services",
        status=status,
        timezone="UTC",
        currency="USD",
        locale="en",
    )
    business.id = business_id or uuid4()
    business.created_at = created_at or datetime.now(UTC)
    business.updated_at = business.created_at
    return business


def _make_membership(
    business: Business,
    user: User,
    *,
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
