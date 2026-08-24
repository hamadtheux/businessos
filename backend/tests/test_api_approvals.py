from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.api.dependencies.business import (  # noqa: E402
    BusinessAccessContext,
    get_business_access,
)
from app.db.session import get_db_session  # noqa: E402
from app.exceptions.approval import (  # noqa: E402
    ApprovalNotFoundError,
    ApprovalStateError,
)
from app.main import app  # noqa: E402
from app.models.approval_request import ApprovalRequest  # noqa: E402


BUSINESS_ID = UUID("e1000000-0000-0000-0000-000000000001")
OTHER_BUSINESS_ID = UUID("e2000000-0000-0000-0000-000000000002")
USER_ID = UUID("e3000000-0000-0000-0000-000000000003")


class ApprovalApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = _FakeSession()
        self.original_overrides = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        async def override_access(business_id: UUID) -> BusinessAccessContext:
            if business_id != BUSINESS_ID:
                raise HTTPException(status_code=404, detail="Business not found.")
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
        app.dependency_overrides.update(self.original_overrides)

    def test_openapi_exposes_authenticated_approval_routes(self) -> None:
        schema = app.openapi()
        root = f"/api/v1/businesses/{{business_id}}/approvals"
        self.assertIn(root, schema["paths"])
        self.assertIn(f"{root}/{{approval_id}}/approve", schema["paths"])
        self.assertTrue(schema["paths"][root]["get"]["security"])

    async def test_authentication_is_required(self) -> None:
        del app.dependency_overrides[get_business_access]
        response = await self.client.get(self._url())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        self._assert_private(response)

    async def test_business_membership_is_required(self) -> None:
        response = await self.client.get(self._url(business_id=OTHER_BUSINESS_ID))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Business not found."})
        self._assert_private(response)

    async def test_list_defaults_to_pending_and_is_private(self) -> None:
        approval = _approval()
        with patch(
            "app.api.v1.approvals.list_approval_requests",
            new=AsyncMock(return_value=[approval]),
        ) as service:
            response = await self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["status"], "pending")
        self._assert_private(response)
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(service.await_args.kwargs["approval_status"], "pending")

    async def test_cross_tenant_approval_is_safe_404(self) -> None:
        with patch(
            "app.api.v1.approvals.get_approval_request",
            new=AsyncMock(side_effect=ApprovalNotFoundError("private")),
        ):
            response = await self.client.get(f"{self._url()}/{uuid4()}")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("private", response.text)
        self._assert_private(response)

    async def test_approve_uses_authenticated_actor_and_commits(self) -> None:
        approval = _approval(status="approved", actor_id=USER_ID)
        with patch(
            "app.api.v1.approvals.approve_approval_request",
            new=AsyncMock(return_value=approval),
        ) as service:
            response = await self.client.post(
                f"{self._url()}/{approval.id}/approve",
                json={"decision_note": "Approved."},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "approved")
        self.assertEqual(service.await_args.kwargs["decided_by_user_id"], USER_ID)
        self.assertEqual(self.session.commit_calls, 1)
        self._assert_private(response)

    async def test_actor_identity_cannot_be_spoofed(self) -> None:
        with patch(
            "app.api.v1.approvals.approve_approval_request",
            new=AsyncMock(),
        ) as service:
            response = await self.client.post(
                f"{self._url()}/{uuid4()}/approve",
                json={
                    "decision_note": "Approved.",
                    "decided_by_user_id": str(uuid4()),
                    "business_id": str(OTHER_BUSINESS_ID),
                },
            )
        self.assertEqual(response.status_code, 422)
        service.assert_not_awaited()
        self._assert_private(response)

    async def test_reject_invalid_state_returns_safe_409(self) -> None:
        with patch(
            "app.api.v1.approvals.reject_approval_request",
            new=AsyncMock(side_effect=ApprovalStateError("private state")),
        ):
            response = await self.client.post(
                f"{self._url()}/{uuid4()}/reject",
                json={},
            )
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("private state", response.text)
        self.assertEqual(self.session.rollback_calls, 1)
        self._assert_private(response)

    @staticmethod
    def _url(*, business_id: UUID = BUSINESS_ID) -> str:
        return f"/api/v1/businesses/{business_id}/approvals"

    def _assert_private(self, response: httpx.Response) -> None:
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")


class _FakeSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


def _approval(
    *,
    status: str = "pending",
    actor_id: UUID | None = None,
) -> ApprovalRequest:
    now = datetime.now(UTC)
    decided = now if status != "pending" else None
    return ApprovalRequest(
        id=uuid4(),
        business_id=BUSINESS_ID,
        action_id=uuid4(),
        requested_by_user_id=None,
        status=status,
        reason_code="external_communication",
        requested_at=now,
        expires_at=None,
        decided_at=decided,
        decided_by_user_id=actor_id,
        decision_actor_id=actor_id,
        decision_note="Approved." if status == "approved" else None,
        created_at=now,
        updated_at=now,
    )
