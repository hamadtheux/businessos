import os
import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from sqlalchemy.exc import SQLAlchemyError


os.environ["AIBOS_DATABASE_URL"] = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = "x" * 32

from app.api.dependencies.auth import get_current_user  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.business import Business  # noqa: E402
from app.models.business_branding import BusinessBranding  # noqa: E402
from app.models.business_membership import BusinessMembership  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.business import (  # noqa: E402
    BusinessBrandingResponse,
    BusinessBrandingUpdate,
)
from app.services.business_branding import (  # noqa: E402
    get_business_branding,
    update_business_branding,
)


class BusinessBrandingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_returns_existing_or_missing_branding(self) -> None:
        business = _make_business("Tenant A", "tenant-a")
        branding = _make_branding(business.id, primary_color="#176B45")
        session = _FakeAsyncSession(brandings=[branding])

        self.assertIs(
            await get_business_branding(session, business.id),
            branding,
        )
        self.assertIsNone(await get_business_branding(session, uuid4()))
        self.assertEqual(session.commit_calls, 0)

    async def test_create_and_update_flush_without_committing(self) -> None:
        business_id = uuid4()
        session = _FakeAsyncSession()

        created = await update_business_branding(
            session,
            business_id,
            BusinessBrandingUpdate(primary_color="#176b45"),
        )

        self.assertIsNotNone(created)
        assert created is not None
        self.assertEqual(created.primary_color, "#176B45")
        self.assertEqual(created.business_id, business_id)
        self.assertEqual(session.flush_calls, 1)
        self.assertEqual(session.commit_calls, 0)

        created.logo_url = "https://storage.example.test/read-only.png"
        updated = await update_business_branding(
            session,
            business_id,
            BusinessBrandingUpdate(
                primary_color="#123456",
                secondary_color="#abcdef",
            ),
        )

        self.assertIs(updated, created)
        self.assertEqual(updated.secondary_color, "#ABCDEF")
        self.assertEqual(
            updated.logo_url,
            "https://storage.example.test/read-only.png",
        )
        self.assertEqual(session.commit_calls, 0)

    async def test_reset_deletes_empty_row_but_preserves_read_only_logo(self) -> None:
        empty_logo_business_id = uuid4()
        existing_logo_business_id = uuid4()
        empty_logo = _make_branding(
            empty_logo_business_id,
            primary_color="#176B45",
        )
        existing_logo = _make_branding(
            existing_logo_business_id,
            logo_url="https://storage.example.test/read-only.png",
            primary_color="#123456",
        )
        session = _FakeAsyncSession(brandings=[empty_logo, existing_logo])

        deleted = await update_business_branding(
            session,
            empty_logo_business_id,
            BusinessBrandingUpdate(),
        )
        retained = await update_business_branding(
            session,
            existing_logo_business_id,
            BusinessBrandingUpdate(),
        )

        self.assertIsNone(deleted)
        self.assertNotIn(empty_logo, session.brandings)
        self.assertIs(retained, existing_logo)
        self.assertIsNone(existing_logo.primary_color)
        self.assertEqual(
            existing_logo.logo_url,
            "https://storage.example.test/read-only.png",
        )

    async def test_persistence_errors_are_domain_safe(self) -> None:
        session = _FakeAsyncSession(
            scalar_error=SQLAlchemyError("private database details")
        )

        with self.assertRaises(Exception) as raised:
            await get_business_branding(session, uuid4())

        self.assertNotIsInstance(raised.exception, SQLAlchemyError)


class BusinessBrandingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user = _make_user("member@example.test")
        self.other_user = _make_user("other@example.test")
        self.business = _make_business("Tenant A", "tenant-a")
        self.other_business = _make_business("Tenant B", "tenant-b")
        self.membership = _make_membership(self.business, self.user)
        self.session = _FakeAsyncSession(
            businesses=[self.business, self.other_business],
            memberships=[self.membership],
        )
        self.original_overrides = app.dependency_overrides.copy()

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
        app.dependency_overrides.update(self.original_overrides)

    def test_openapi_contract_has_get_and_put_without_logo_input(self) -> None:
        operation = app.openapi()["paths"][
            "/api/v1/businesses/{business_id}/branding"
        ]

        self.assertIn("get", operation)
        self.assertIn("put", operation)
        self.assertTrue(operation["get"]["security"])
        self.assertTrue(operation["put"]["security"])
        self.assertEqual(
            operation["put"]["requestBody"]["content"]["application/json"][
                "schema"
            ]["$ref"],
            "#/components/schemas/BusinessBrandingUpdate",
        )
        update_schema = app.openapi()["components"]["schemas"][
            "BusinessBrandingUpdate"
        ]
        self.assertNotIn("logo_url", update_schema["properties"])

    async def test_authentication_is_required(self) -> None:
        del app.dependency_overrides[get_current_user]

        response = await self.client.get(self._path(self.business.id))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.session.execute_calls, 0)

    async def test_existing_branding_is_returned_without_a_commit(self) -> None:
        branding = _make_branding(
            self.business.id,
            logo_url="https://storage.example.test/read-only.png",
            primary_color="#176B45",
            secondary_color="#45695A",
            accent_color="#D36F32",
        )
        self.session.brandings.append(branding)

        response = await self.client.get(self._path(self.business.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "primary_color": "#176B45",
                "secondary_color": "#45695A",
                "accent_color": "#D36F32",
                "logo_url": "https://storage.example.test/read-only.png",
            },
        )
        self.assertEqual(self.session.commit_calls, 0)
        self._assert_private_cache_headers(response)

    async def test_missing_branding_returns_clean_null_representation(self) -> None:
        response = await self.client.get(self._path(self.business.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "primary_color": None,
                "secondary_color": None,
                "accent_color": None,
                "logo_url": None,
            },
        )
        self._assert_private_cache_headers(response)

    async def test_put_creates_normalizes_and_commits_before_response(self) -> None:
        def build_after_commit(branding: object) -> BusinessBrandingResponse:
            self.assertEqual(self.session.commit_calls, 1)
            return BusinessBrandingResponse.model_validate(branding)

        with patch(
            "app.api.v1.businesses._build_branding_response",
            side_effect=build_after_commit,
        ):
            response = await self.client.put(
                self._path(self.business.id),
                json={
                    "primary_color": "#176b45",
                    "secondary_color": "#abcdef",
                    "accent_color": None,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["primary_color"], "#176B45")
        self.assertEqual(response.json()["secondary_color"], "#ABCDEF")
        self.assertEqual(len(self.session.brandings), 1)
        self.assertEqual(self.session.flush_calls, 1)
        self._assert_private_cache_headers(response)

    async def test_put_updates_existing_without_changing_logo_url(self) -> None:
        branding = _make_branding(
            self.business.id,
            logo_url="https://storage.example.test/read-only.png",
            primary_color="#111111",
        )
        self.session.brandings.append(branding)

        response = await self.client.put(
            self._path(self.business.id),
            json={"primary_color": "#222222"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(branding.primary_color, "#222222")
        self.assertEqual(
            branding.logo_url,
            "https://storage.example.test/read-only.png",
        )
        self.assertEqual(response.json()["logo_url"], branding.logo_url)

    async def test_reset_removes_color_only_row_and_is_idempotent(self) -> None:
        self.session.brandings.append(
            _make_branding(self.business.id, primary_color="#176B45")
        )

        first = await self.client.put(self._path(self.business.id), json={})
        second = await self.client.put(
            self._path(self.business.id),
            json={
                "primary_color": None,
                "secondary_color": None,
                "accent_color": None,
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.session.brandings, [])
        self.assertTrue(all(value is None for value in second.json().values()))
        self.assertEqual(self.session.commit_calls, 2)

    async def test_invalid_hex_unknown_fields_and_logo_are_rejected(self) -> None:
        invalid_payloads = [
            {"primary_color": "#FFF"},
            {"primary_color": "red"},
            {"primary_color": "FFFFFF"},
            {"primary_color": "#GGGGGG"},
            {"sidebar_color": "#123456"},
            {"logo_url": "https://untrusted.example.test/logo.png"},
            {"logo_url": "data:image/png;base64,untrusted"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = await self.client.put(
                    self._path(self.business.id),
                    json=payload,
                )
                self.assertEqual(response.status_code, 422)

        self.assertEqual(self.session.commit_calls, 0)
        self.assertEqual(self.session.brandings, [])

    async def test_inactive_membership_is_rejected(self) -> None:
        self.membership.status = "suspended"

        response = await self.client.get(self._path(self.business.id))

        self.assertEqual(response.status_code, 403)

    async def test_cross_tenant_read_and_update_share_safe_404(self) -> None:
        hidden = _make_branding(
            self.other_business.id,
            primary_color="#654321",
        )
        self.session.brandings.append(hidden)

        read_response = await self.client.get(
            self._path(self.other_business.id)
        )
        update_response = await self.client.put(
            self._path(self.other_business.id),
            json={"primary_color": "#123456"},
        )
        missing_response = await self.client.get(self._path(uuid4()))

        self.assertEqual(read_response.status_code, 404)
        self.assertEqual(update_response.status_code, 404)
        self.assertEqual(read_response.json(), missing_response.json())
        self.assertEqual(hidden.primary_color, "#654321")
        self.assertEqual(self.session.commit_calls, 0)

    async def test_read_persistence_failure_is_safe_and_private(self) -> None:
        self.session.scalar_error = SQLAlchemyError("private database details")

        response = await self.client.get(self._path(self.business.id))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Business branding is temporarily unavailable."},
        )
        self.assertNotIn("private database details", response.text)
        self._assert_private_cache_headers(response)

    async def test_flush_and_commit_failures_rollback_to_safe_503(self) -> None:
        for failure in ("flush", "commit"):
            with self.subTest(failure=failure):
                self.session = _FakeAsyncSession(
                    businesses=[self.business],
                    memberships=[self.membership],
                    flush_error=(
                        SQLAlchemyError("private flush details")
                        if failure == "flush"
                        else None
                    ),
                    commit_error=(
                        SQLAlchemyError("private commit details")
                        if failure == "commit"
                        else None
                    ),
                )

                response = await self.client.put(
                    self._path(self.business.id),
                    json={"primary_color": "#123456"},
                )

                self.assertEqual(response.status_code, 503)
                self.assertEqual(self.session.rollback_calls, 1)
                self.assertNotIn("private", response.text)
                self._assert_private_cache_headers(response)

    @staticmethod
    def _path(business_id: UUID) -> str:
        return f"/api/v1/businesses/{business_id}/branding"

    def _assert_private_cache_headers(self, response: httpx.Response) -> None:
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")


class _FakeAccessResult:
    def __init__(
        self,
        row: tuple[Business, BusinessMembership] | None,
    ) -> None:
        self.row = row

    def one_or_none(self) -> tuple[Business, BusinessMembership] | None:
        return self.row


class _FakeAsyncSession:
    def __init__(
        self,
        *,
        businesses: list[Business] | None = None,
        memberships: list[BusinessMembership] | None = None,
        brandings: list[BusinessBranding] | None = None,
        scalar_error: SQLAlchemyError | None = None,
        flush_error: SQLAlchemyError | None = None,
        commit_error: SQLAlchemyError | None = None,
    ) -> None:
        self.businesses = businesses or []
        self.memberships = memberships or []
        self.brandings = brandings or []
        self.scalar_error = scalar_error
        self.flush_error = flush_error
        self.commit_error = commit_error
        self.execute_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0

    async def execute(self, statement: object) -> _FakeAccessResult:
        self.execute_calls += 1
        parameters = statement.compile().params
        user_id = _parameter(parameters, "user_id")
        business_id = _parameter(parameters, "business_id")
        membership = next(
            (
                item
                for item in self.memberships
                if item.user_id == user_id
                and item.business_id == business_id
            ),
            None,
        )
        business = next(
            (item for item in self.businesses if item.id == business_id),
            None,
        )
        return _FakeAccessResult(
            (business, membership)
            if business is not None and membership is not None
            else None
        )

    async def scalar(self, statement: object) -> BusinessBranding | None:
        if self.scalar_error is not None:
            raise self.scalar_error
        business_id = _parameter(statement.compile().params, "business_id")
        return next(
            (
                branding
                for branding in self.brandings
                if branding.business_id == business_id
            ),
            None,
        )

    def add(self, instance: object) -> None:
        if isinstance(instance, BusinessBranding):
            self.brandings.append(instance)

    async def delete(self, instance: object) -> None:
        if isinstance(instance, BusinessBranding):
            self.brandings.remove(instance)

    async def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_error is not None:
            raise self.flush_error

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollback_calls += 1


def _parameter(parameters: dict[str, object], prefix: str) -> object:
    return next(value for name, value in parameters.items() if name.startswith(prefix))


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


def _make_business(name: str, slug: str) -> Business:
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


def _make_membership(
    business: Business,
    user: User,
) -> BusinessMembership:
    membership = BusinessMembership(
        business_id=business.id,
        user_id=user.id,
        role="owner",
        status="active",
    )
    membership.id = uuid4()
    membership.created_at = datetime.now(UTC)
    membership.updated_at = membership.created_at
    return membership


def _make_branding(
    business_id: UUID,
    *,
    logo_url: str | None = None,
    primary_color: str | None = None,
    secondary_color: str | None = None,
    accent_color: str | None = None,
) -> BusinessBranding:
    branding = BusinessBranding(
        business_id=business_id,
        logo_url=logo_url,
        primary_color=primary_color,
        secondary_color=secondary_color,
        accent_color=accent_color,
    )
    branding.created_at = datetime.now(UTC)
    branding.updated_at = branding.created_at
    return branding
