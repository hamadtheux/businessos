from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx
from fastapi import HTTPException

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.api.dependencies.business import BusinessAccessContext, get_business_access  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.main import app  # noqa: E402


BUSINESS_ID = UUID("f1000000-0000-4000-8000-000000000001")
OTHER_BUSINESS_ID = UUID("f2000000-0000-4000-8000-000000000002")
USER_ID = UUID("f3000000-0000-4000-8000-000000000003")


class _Session:
    async def commit(self):
        return None

    async def rollback(self):
        return None


class CustomerSupportApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original = app.dependency_overrides.copy()

        async def override_session():
            yield _Session()

        async def override_access(business_id: UUID):
            if business_id != BUSINESS_ID:
                raise HTTPException(404, "Business not found.")
            return BusinessAccessContext(
                user=SimpleNamespace(id=USER_ID),
                business=SimpleNamespace(id=business_id, status="active"),
                membership=SimpleNamespace(
                    business_id=business_id,
                    user_id=USER_ID,
                    status="active",
                    role="owner",
                ),
            )

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_business_access] = override_access
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original)

    def test_openapi_exposes_authenticated_support_case_and_metrics_endpoints(self) -> None:
        root = "/api/v1/businesses/{business_id}/support"
        schema = app.openapi()
        for path in (f"{root}/cases", f"{root}/cases/{{case_id}}", f"{root}/metrics"):
            self.assertIn(path, schema["paths"])
            self.assertTrue(all(operation["security"] for operation in schema["paths"][path].values()))

    async def test_case_list_passes_tenant_and_operational_filters(self) -> None:
        with patch(
            "app.api.v1.support.service.list_support_cases",
            new=AsyncMock(return_value=([], 0)),
        ) as list_cases, patch(
            "app.api.v1.support.service.support_case_responses",
            new=AsyncMock(return_value=[]),
        ):
            response = await self.client.get(
                f"/api/v1/businesses/{BUSINESS_ID}/support/cases",
                params={
                    "status": "escalated",
                    "priority": "high",
                    "channel": "facebook",
                    "search": "SUP-100",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])
        self.assertEqual(list_cases.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(list_cases.await_args.kwargs["status"], "escalated")
        self.assertEqual(list_cases.await_args.kwargs["channel"], "facebook")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_metrics_are_tenant_scoped_and_cross_tenant_access_is_rejected(self) -> None:
        with patch(
            "app.api.v1.support.service.support_metrics",
            new=AsyncMock(return_value={
                "open_issues": 4,
                "ai_handling": 2,
                "escalated": 1,
                "waiting_for_customer": 1,
                "resolved_today": 3,
            }),
        ) as metrics:
            response = await self.client.get(
                f"/api/v1/businesses/{BUSINESS_ID}/support/metrics"
            )
            denied = await self.client.get(
                f"/api/v1/businesses/{OTHER_BUSINESS_ID}/support/metrics"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["escalated"], 1)
        self.assertEqual(metrics.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(metrics.await_count, 1)


if __name__ == "__main__":
    unittest.main()
