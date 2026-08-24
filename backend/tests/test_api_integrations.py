from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.api.dependencies.business import BusinessAccessContext, get_business_access  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.exceptions.integration import IntegrationProviderUnavailableError  # noqa: E402
from app.main import app  # noqa: E402
from app.models.integration import IntegrationConnection  # noqa: E402


BUSINESS_ID = UUID("a1000000-0000-4000-8000-000000000001")
OTHER_BUSINESS_ID = UUID("a2000000-0000-4000-8000-000000000002")
USER_ID = UUID("a3000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


class IntegrationsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = _Session()
        self.original = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        async def override_access(business_id: UUID):
            if business_id != BUSINESS_ID:
                raise HTTPException(404, "Business not found.")
            return BusinessAccessContext(
                user=SimpleNamespace(id=USER_ID),
                business=SimpleNamespace(id=business_id, status="active"),
                membership=SimpleNamespace(business_id=business_id, user_id=USER_ID, status="active"),
            )

        self.override_access = override_access
        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_business_access] = override_access
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original)

    def test_openapi_exposes_secure_business_lifecycle_and_public_callbacks(self) -> None:
        root = "/api/v1/businesses/{business_id}/integrations"
        schema = app.openapi()
        for tail in ("/registry", "/connections", "/{connector_type}/authorize"):
            operations = schema["paths"][root + tail]
            self.assertTrue(all(item["security"] for item in operations.values()))
        self.assertIn("/api/v1/integrations/oauth/{connector_type}/callback", schema["paths"])
        self.assertIn("/api/v1/integrations/webhooks/{connector_type}/{connection_id}", schema["paths"])

    async def test_registry_is_tenant_authorized_and_contains_no_provider_secrets(self) -> None:
        response = await self.client.get(self._url("registry"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 8)
        serialized = response.text.lower()
        self.assertNotIn("client_secret", serialized)
        self.assertNotIn("access_token", serialized)
        self.assertTrue(all(item["external_writes_enabled"] is False for item in response.json()))
        self._private(response)

    async def test_cross_tenant_access_fails_before_service(self) -> None:
        with patch("app.api.v1.integrations.service.list_connections", new=AsyncMock(return_value=[])) as operation:
            response = await self.client.get(self._url("connections", OTHER_BUSINESS_ID))
        self.assertEqual(response.status_code, 404)
        operation.assert_not_awaited()

    async def test_connection_response_excludes_opaque_credential_reference(self) -> None:
        connection = _connection()
        with patch("app.api.v1.integrations.service.list_connections", new=AsyncMock(return_value=[connection])) as operation:
            response = await self.client.get(self._url("connections"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(operation.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertNotIn("credential_reference", response.json()[0])
        self.assertNotIn("server-only-reference", response.text)
        self._private(response)

    async def test_authorization_fails_safely_when_provider_setup_is_unavailable(self) -> None:
        with patch(
            "app.api.v1.integrations.service.begin_authorization",
            new=AsyncMock(side_effect=IntegrationProviderUnavailableError("private-provider-detail")),
        ):
            response = await self.client.post(self._url("gmail/authorize"), json={"redirect_target": "/integrations"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private-provider-detail", response.text)
        self.assertEqual(self.session.rollback_calls, 1)
        self._private(response)

    async def test_authorization_rejects_open_redirects_and_unknown_connectors(self) -> None:
        response = await self.client.post(self._url("gmail/authorize"), json={"redirect_target": "https://attacker.invalid"})
        self.assertEqual(response.status_code, 422)
        response = await self.client.post(self._url("unknown/authorize"), json={"redirect_target": "/integrations"})
        self.assertEqual(response.status_code, 422)

    async def test_webhook_rejects_invalid_payload_before_processing(self) -> None:
        response = await self.client.post(
            f"/api/v1/integrations/webhooks/facebook/{uuid4()}",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 422)
        self._private(response)

    @staticmethod
    def _url(path: str, business_id: UUID = BUSINESS_ID) -> str:
        return f"/api/v1/businesses/{business_id}/integrations/{path}"

    def _private(self, response: httpx.Response) -> None:
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")


class _Session:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


def _connection() -> IntegrationConnection:
    return IntegrationConnection(
        id=uuid4(),
        business_id=BUSINESS_ID,
        connector_type="gmail",
        display_name="Gmail",
        status="connected",
        authentication_state="authorized",
        health="healthy",
        credential_reference="server-only-reference",
        external_account_reference="account-1",
        external_account_display_name="Business mailbox",
        selected_resources=[],
        scopes_granted=["openid"],
        connected_by_user_id=USER_ID,
        connected_at=NOW,
        last_health_check_at=NOW,
        last_successful_sync_at=None,
        failure_code=None,
        created_at=NOW,
        updated_at=NOW,
    )


if __name__ == "__main__":
    unittest.main()
