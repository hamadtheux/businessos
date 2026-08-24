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
from app.exceptions.background_jobs import BackgroundJobStateError  # noqa: E402
from app.main import app  # noqa: E402


BUSINESS_ID = UUID("e1000000-0000-4000-8000-000000000001")
OTHER_BUSINESS_ID = UUID("e2000000-0000-4000-8000-000000000002")
USER_ID = UUID("e3000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


class ProcessingApiTests(unittest.IsolatedAsyncioTestCase):
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
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original)

    def test_openapi_is_authenticated_and_has_no_generic_enqueue(self) -> None:
        root = "/api/v1/businesses/{business_id}/processing"
        schema = app.openapi()["paths"]
        self.assertNotIn(root + "/jobs", {
            path for path, operations in schema.items() if "post" in operations and path == root + "/jobs"
        })
        for path, operations in schema.items():
            if path.startswith(root):
                self.assertTrue(all(operation["security"] for operation in operations.values()))

    async def test_list_is_tenant_scoped_and_bounded(self) -> None:
        with patch("app.api.v1.processing.list_jobs", new=AsyncMock(return_value=([_job()], 1))) as service:
            response = await self.client.get(self._url("jobs?status=failed&page=2&page_size=10"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(service.await_args.kwargs["status"], "failed")
        self.assertEqual(service.await_args.kwargs["page_size"], 10)
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_cross_tenant_access_fails_before_job_lookup(self) -> None:
        with patch("app.api.v1.processing.get_job", new=AsyncMock()) as service:
            response = await self.client.get(self._url(f"jobs/{uuid4()}", OTHER_BUSINESS_ID))
        self.assertEqual(response.status_code, 404)
        service.assert_not_awaited()

    async def test_safe_manual_retry_uses_authenticated_actor_and_commits(self) -> None:
        with patch("app.api.v1.processing.retry_job", new=AsyncMock(return_value=_job())) as service:
            response = await self.client.post(self._url(f"jobs/{uuid4()}/retry"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.await_args.kwargs["actor_user_id"], USER_ID)
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(self.session.commit_calls, 1)

    async def test_processing_cancel_conflict_is_safe_and_rolls_back(self) -> None:
        with patch(
            "app.api.v1.processing.cancel_job",
            new=AsyncMock(side_effect=BackgroundJobStateError("private state")),
        ):
            response = await self.client.post(self._url(f"jobs/{uuid4()}/cancel"))
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("private state", response.text)
        self.assertEqual(self.session.rollback_calls, 1)

    async def test_health_is_separate_from_api_health_and_has_safe_heartbeat_summary(self) -> None:
        data = {
            "counts": {"queued": 2, "processing": 1, "succeeded": 5, "failed": 0, "dead_letter": 1, "canceled": 0},
            "automation_event_backlog": 3,
            "oldest_queued_job_age_seconds": 12.0,
            "average_processing_latency_seconds": 1.5,
            "worker_last_heartbeat_at": NOW,
            "scheduler_last_heartbeat_at": NOW,
        }
        with patch("app.api.v1.processing.processing_health", new=AsyncMock(return_value=data)):
            response = await self.client.get(self._url("health"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(response.json()["status"], {"healthy", "degraded", "unavailable"})
        self.assertNotIn("worker_id", response.text)
        api_health = await self.client.get("/health")
        self.assertEqual(api_health.status_code, 200)

    @staticmethod
    def _url(path: str, business_id: UUID = BUSINESS_ID) -> str:
        return f"/api/v1/businesses/{business_id}/processing/{path}"


class _Session:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


def _job() -> dict[str, object]:
    return {
        "id": uuid4(), "business_id": BUSINESS_ID,
        "job_type": "process_automation_event", "status": "failed", "priority": 80,
        "idempotency_key": f"automation-event:{uuid4()}", "attempt_count": 1,
        "max_attempts": 4, "available_at": NOW, "claimed_at": NOW,
        "lease_expires_at": NOW, "completed_at": NOW, "failure_code": "invalid_job_state",
        "automation_event_id": uuid4(), "workflow_id": None, "workflow_run_id": None,
        "node_run_id": None, "integration_event_id": None,
        "action_execution_attempt_id": None, "social_schedule_id": None,
        "scheduled_occurrence_at": None, "created_at": NOW, "updated_at": NOW,
    }
