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
from app.exceptions.automation import AutomationValidationError  # noqa: E402
from app.main import app  # noqa: E402


BUSINESS_ID = UUID("91000000-0000-0000-0000-000000000001")
OTHER_BUSINESS_ID = UUID("92000000-0000-0000-0000-000000000002")
USER_ID = UUID("93000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


class AutomationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = _FakeSession()
        self.original = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        async def override_access(business_id: UUID):
            if business_id != BUSINESS_ID:
                raise HTTPException(404, "Business not found.")
            return BusinessAccessContext(
                user=SimpleNamespace(id=USER_ID),
                business=SimpleNamespace(id=business_id, status="active", timezone="UTC"),
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

    def test_openapi_exposes_authenticated_workflow_surface(self) -> None:
        paths = {path: operations for path, operations in app.openapi()["paths"].items() if "/automations/" in path}
        self.assertGreaterEqual(len(paths), 19)
        for route, operations in paths.items():
            with self.subTest(route=route):
                self.assertTrue(all(operation["security"] for operation in operations.values()))

    async def test_authentication_and_cross_tenant_access_fail_closed(self) -> None:
        del app.dependency_overrides[get_business_access]
        response = await self.client.get(self._url("workflows"))
        self.assertEqual(response.status_code, 401)
        app.dependency_overrides[get_business_access] = self.override_access
        response = await self.client.get(self._url("workflows", OTHER_BUSINESS_ID))
        self.assertEqual(response.status_code, 404)

    async def test_workflow_list_passes_bounded_pagination_and_tenant(self) -> None:
        with patch("app.api.v1.automations.list_workflows", new=AsyncMock(return_value=([_workflow()], 1))) as service:
            response = await self.client.get(self._url("workflows?page=2&page_size=10"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(service.await_args.kwargs["page"], 2)
        self.assertEqual(service.await_args.kwargs["page_size"], 10)

    async def test_creation_uses_authenticated_actor_and_commits(self) -> None:
        with patch("app.api.v1.automations.create_workflow", new=AsyncMock(return_value=_workflow())) as service:
            response = await self.client.post(self._url("workflows"), json={"name": "Lead triage", "trigger_type": "lead_created", "timezone": "UTC"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(service.await_args.kwargs["actor_user_id"], USER_ID)
        self.assertEqual(self.session.commit_calls, 1)

    async def test_invalid_activation_is_safe_and_rolls_back(self) -> None:
        with patch("app.api.v1.automations.transition_workflow", new=AsyncMock(side_effect=AutomationValidationError("private graph detail"))):
            response = await self.client.post(self._url(f"workflows/{uuid4()}/status"), json={"status": "active"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private graph detail", response.text)
        self.assertEqual(self.session.rollback_calls, 1)

    async def test_simulation_returns_backend_trace_and_no_commit(self) -> None:
        result = {"valid": True, "completed": True, "trace": [{"node_key": uuid4(), "node_type": "end", "name": "Done", "status": "succeeded", "branch_outcome": None, "summary": "Complete"}], "approvals": [], "delays": [], "planned_actions": [], "errors": []}
        with patch("app.api.v1.automations.simulate_workflow", new=AsyncMock(return_value=result)) as service:
            response = await self.client.post(self._url(f"workflows/{uuid4()}/simulate"), json={"payload": {"lead": {"estimated_value": 10}}})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["completed"])
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(self.session.commit_calls, 0)

    @staticmethod
    def _url(path: str, business_id: UUID = BUSINESS_ID) -> str:
        return f"/api/v1/businesses/{business_id}/automations/{path}"


class _FakeSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


def _workflow() -> dict:
    return {
        "id": uuid4(), "business_id": BUSINESS_ID, "name": "Lead triage", "description": None,
        "status": "draft", "current_version": 1, "trigger_type": "lead_created", "enabled": False,
        "timezone": "UTC", "schedule_definition": {}, "next_run_at": None,
        "created_by_user_id": USER_ID, "created_at": NOW, "updated_at": NOW,
        "last_run_status": None, "last_run_at": None,
    }
