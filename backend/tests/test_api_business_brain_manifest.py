import os
import unittest
from types import MappingProxyType
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.business import (
    BusinessAccessContext,
    get_business_access,
)
from app.db.session import get_db_session
from app.exceptions.business_brain import BusinessBrainAssemblyError
from app.main import app
from app.models.business import Business
from app.models.business_membership import BusinessMembership
from app.models.user import User
from app.services.business_brain_assembly import BusinessBrainManifest

BUSINESS_A_ID = UUID("61000000-0000-0000-0000-000000000001")
BUSINESS_B_ID = UUID("62000000-0000-0000-0000-000000000002")
REVISION = "a" * 64


class BusinessBrainManifestApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user = _user()
        self.business = _business(BUSINESS_A_ID)
        self.membership = _membership(self.business, self.user)
        self.session = _ReadOnlySession()
        self.original_dependency_overrides = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        async def override_user() -> User:
            return self.user

        async def override_access(business_id: UUID) -> BusinessAccessContext:
            if business_id != BUSINESS_A_ID:
                raise HTTPException(status_code=404, detail="Business not found.")
            return BusinessAccessContext(
                user=self.user,
                business=self.business,
                membership=self.membership,
            )

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[get_business_access] = override_access
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_dependency_overrides)

    def test_openapi_exposes_only_lightweight_manifest_get(self) -> None:
        schema = app.openapi()
        path = "/api/v1/businesses/{business_id}/brain/manifest"
        self.assertEqual(set(schema["paths"][path]), {"get"})
        response_ref = schema["paths"][path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        self.assertTrue(response_ref.endswith("/BusinessBrainManifestResponse"))

    async def test_authentication_is_required_and_not_cached(self) -> None:
        del app.dependency_overrides[get_business_access]
        del app.dependency_overrides[get_current_user]

        response = await self.client.get(self._url())

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        self._assert_private(response)
        self.assertEqual(self.session.write_calls, 0)

    async def test_business_membership_is_required_with_safe_404(self) -> None:
        del app.dependency_overrides[get_business_access]
        self.session.access_row = None

        response = await self.client.get(self._url())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Business not found."})
        self._assert_private(response)
        self.assertEqual(self.session.write_calls, 0)

    async def test_manifest_is_tenant_scoped_typed_and_contains_no_sources(
        self,
    ) -> None:
        manifest_builder = AsyncMock(return_value=_manifest())
        with patch(
            "app.api.v1.business_brain_manifest.build_business_brain_manifest",
            manifest_builder,
        ):
            response = await self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "business_id": str(BUSINESS_A_ID),
                "source_count": 4,
                "source_counts_by_type": {
                    "business_profile": 1,
                    "branding": 1,
                    "catalog_item": 1,
                    "knowledge_entry": 1,
                },
                "revision": REVISION,
            },
        )
        self.assertNotIn("content", response.json())
        self.assertNotIn("sources", response.json())
        self.assertNotIn("logo_storage_key", response.text)
        self.assertNotIn("secret", response.text.lower())
        manifest_builder.assert_awaited_once_with(self.session, BUSINESS_A_ID)
        self._assert_private(response)

    async def test_cross_tenant_manifest_is_safe_404(self) -> None:
        manifest_builder = AsyncMock(return_value=_manifest())
        with patch(
            "app.api.v1.business_brain_manifest.build_business_brain_manifest",
            manifest_builder,
        ):
            response = await self.client.get(self._url(BUSINESS_B_ID))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Business not found."})
        manifest_builder.assert_not_awaited()
        self._assert_private(response)

    async def test_database_failure_returns_safe_private_503(self) -> None:
        manifest_builder = AsyncMock(
            side_effect=BusinessBrainAssemblyError("private database detail")
        )
        with patch(
            "app.api.v1.business_brain_manifest.build_business_brain_manifest",
            manifest_builder,
        ):
            response = await self.client.get(self._url())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Business Brain manifest is temporarily unavailable."},
        )
        self.assertNotIn("private database detail", response.text)
        self._assert_private(response)
        self.assertEqual(self.session.write_calls, 0)

    async def test_manifest_request_performs_zero_database_writes(self) -> None:
        with patch(
            "app.api.v1.business_brain_manifest.build_business_brain_manifest",
            AsyncMock(return_value=_manifest()),
        ):
            response = await self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.session.write_calls, 0)

    def _url(self, business_id: UUID = BUSINESS_A_ID) -> str:
        return f"/api/v1/businesses/{business_id}/brain/manifest"

    def _assert_private(self, response: httpx.Response) -> None:
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")


class _AccessResult:
    def __init__(self, row) -> None:
        self.row = row

    def one_or_none(self):
        return self.row


class _ReadOnlySession:
    def __init__(self) -> None:
        self.access_row = None
        self.write_calls = 0

    async def execute(self, _statement) -> _AccessResult:
        return _AccessResult(self.access_row)

    def add(self, _record) -> None:
        self.write_calls += 1

    async def flush(self) -> None:
        self.write_calls += 1

    async def commit(self) -> None:
        self.write_calls += 1

    async def rollback(self) -> None:
        self.write_calls += 1


def _manifest() -> BusinessBrainManifest:
    return BusinessBrainManifest(
        business_id=BUSINESS_A_ID,
        source_count=4,
        source_counts_by_type=MappingProxyType(
            {
                "business_profile": 1,
                "branding": 1,
                "catalog_item": 1,
                "knowledge_entry": 1,
            }
        ),
        revision=REVISION,
    )


def _user() -> User:
    return User(
        id=uuid4(),
        email="manifest-owner@example.com",
        password_hash="hash",
        first_name="Owner",
        status="active",
        is_email_verified=True,
    )


def _business(business_id: UUID) -> Business:
    return Business(
        id=business_id,
        name="Manifest Business",
        slug="manifest-business",
        business_type="services",
        status="active",
        timezone="UTC",
        currency="USD",
        locale="en",
    )


def _membership(business: Business, user: User) -> BusinessMembership:
    return BusinessMembership(
        id=uuid4(),
        business_id=business.id,
        user_id=user.id,
        role="owner",
        status="active",
    )
