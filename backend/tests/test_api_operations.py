from __future__ import annotations

import os
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.api.dependencies.business import BusinessAccessContext, get_business_access  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.exceptions.operations import OperationsNotFoundError, OperationsStateError, OperationsValidationError  # noqa: E402
from app.main import app  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.crm_lead import CRMLead  # noqa: E402
from app.models.customer import Customer  # noqa: E402
from app.models.notification import Notification  # noqa: E402
from app.models.opportunity import Opportunity  # noqa: E402
from app.models.order import Order  # noqa: E402


BUSINESS_ID = UUID("51000000-0000-0000-0000-000000000001")
OTHER_BUSINESS_ID = UUID("52000000-0000-0000-0000-000000000002")
USER_ID = UUID("53000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


class OperationsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = _FakeSession()
        self.original = app.dependency_overrides.copy()
        async def override_session(): yield self.session
        async def override_access(business_id: UUID):
            if business_id != BUSINESS_ID:
                raise HTTPException(404, "Business not found.")
            return BusinessAccessContext(user=SimpleNamespace(id=USER_ID), business=SimpleNamespace(id=business_id, status="active"), membership=SimpleNamespace(business_id=business_id, user_id=USER_ID, status="active"))
        self.override_access = override_access
        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_business_access] = override_access
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear(); app.dependency_overrides.update(self.original)

    def test_openapi_exposes_tenant_scoped_domains_with_security(self) -> None:
        root = "/api/v1/businesses/{business_id}"
        required = ("/customers", "/crm/leads", "/orders", "/conversations", "/notifications", "/opportunities", "/audit", "/reports", "/analytics/core")
        schema = app.openapi()
        for tail in required:
            with self.subTest(tail=tail):
                operations = schema["paths"][root + tail]
                self.assertTrue(all(value["security"] for value in operations.values()))

    async def test_authentication_and_cross_tenant_access_are_denied(self) -> None:
        del app.dependency_overrides[get_business_access]
        response = await self.client.get(self._url("customers"))
        self.assertEqual(response.status_code, 401)
        app.dependency_overrides[get_business_access] = self.override_access
        response = await self.client.get(self._url("customers", OTHER_BUSINESS_ID))
        self.assertEqual(response.status_code, 404)
        self._private(response)

    async def test_customer_list_passes_bounded_pagination_search_and_tenant(self) -> None:
        with patch("app.api.v1.operations.service.list_customers", new=AsyncMock(return_value=([_customer()], 1))) as service:
            response = await self.client.get(self._url("customers?page=2&page_size=10&search=Acme&status=active"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["page"], 2)
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(service.await_args.kwargs["search"], "Acme")
        self._private(response)

    async def test_customer_create_cannot_spoof_tenant_and_commits(self) -> None:
        with patch("app.api.v1.operations.service.create_customer", new=AsyncMock(return_value=_customer())) as service:
            response = await self.client.post(self._url("customers"), json={"display_name": "Acme", "business_id": str(OTHER_BUSINESS_ID)})
        self.assertEqual(response.status_code, 422)
        service.assert_not_awaited()
        with patch("app.api.v1.operations.service.create_customer", new=AsyncMock(return_value=_customer())) as service:
            response = await self.client.post(self._url("customers"), json={"display_name": "Acme"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(service.await_args.kwargs["actor_user_id"], USER_ID)
        self.assertEqual(self.session.commit_calls, 1)

    async def test_lead_stage_uses_explicit_transition_endpoint(self) -> None:
        lead = _lead(stage="qualified")
        with patch("app.api.v1.operations.service.change_lead_state", new=AsyncMock(return_value=lead)) as service:
            response = await self.client.post(self._url(f"crm/leads/{lead.id}/stage"), json={"stage": "qualified"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.await_args.kwargs["field"], "stage")
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)

    async def test_order_create_rejects_client_total_before_service(self) -> None:
        payload = {"customer_id": str(uuid4()), "currency": "USD", "total": "0.01", "lines": [{"description": "Item", "quantity": 2, "unit_price": "5.00"}]}
        with patch("app.api.v1.operations.service.create_order", new=AsyncMock()) as service:
            response = await self.client.post(self._url("orders"), json=payload)
        self.assertEqual(response.status_code, 422)
        service.assert_not_awaited()

    async def test_order_invalid_state_is_safe_conflict_and_rolls_back(self) -> None:
        with patch("app.api.v1.operations.service.change_order_status", new=AsyncMock(side_effect=OperationsStateError("private"))):
            response = await self.client.post(self._url(f"orders/{uuid4()}/status"), json={"status": "completed"})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("private", response.text)
        self.assertEqual(self.session.rollback_calls, 1)

    async def test_message_endpoint_records_internal_content_without_external_contract(self) -> None:
        message = SimpleNamespace(id=uuid4(), business_id=BUSINESS_ID, conversation_id=uuid4(), direction="internal", sender_type="user", sender_user_id=USER_ID, content="Internal note", sent_at=NOW, external_reference=None, delivery_status="recorded", created_at=NOW, updated_at=NOW)
        with patch("app.api.v1.operations.service.add_message", new=AsyncMock(return_value=message)) as service:
            response = await self.client.post(self._url(f"conversations/{message.conversation_id}/messages"), json={"direction": "internal", "content": "Internal note"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["delivery_status"], "recorded")
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)

    async def test_notifications_are_scoped_to_authenticated_user(self) -> None:
        notice = _notification()
        with patch("app.api.v1.operations.service.list_notifications", new=AsyncMock(return_value=([notice], 1))) as service:
            response = await self.client.get(self._url("notifications?unread_only=true"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.await_args.kwargs["user_id"], USER_ID)
        self.assertTrue(service.await_args.kwargs["unread_only"])

    async def test_audit_api_is_read_only(self) -> None:
        entry = AuditLog(id=uuid4(), business_id=BUSINESS_ID, actor_user_id=USER_ID, actor_type="user", event_type="customer.created", entity_type="customer", entity_id=uuid4(), summary="Created customer.", before_value=None, after_value=None, status="completed", created_at=NOW, updated_at=NOW)
        with patch("app.api.v1.operations.service.list_audit_logs", new=AsyncMock(return_value=([entry], 1))):
            response = await self.client.get(self._url("audit"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("post", app.openapi()["paths"]["/api/v1/businesses/{business_id}/audit"])

    async def test_analytics_period_is_passed_as_dates(self) -> None:
        response_value = {"period_start": date(2026, 8, 1), "period_end": date(2026, 8, 23), "customers": 0, "leads": 0, "crm_stage_counts": {}, "orders": 0, "order_revenue": Decimal("0"), "average_order_value": Decimal("0"), "appointments": 0, "appointment_status_counts": {}, "providers": 0, "opportunities": 0, "opportunity_status_counts": {}, "ai_executions": 0, "ai_actions": 0, "revenue_series": [], "lead_source_counts": {}}
        with patch("app.api.v1.operations.service.core_analytics", new=AsyncMock(return_value=response_value)) as service:
            response = await self.client.get(self._url("analytics/core?period_start=2026-08-01&period_end=2026-08-23"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.await_args.kwargs["period_start"], date(2026, 8, 1))

    async def test_validation_and_not_found_errors_do_not_leak_details(self) -> None:
        with patch("app.api.v1.operations.service.get_customer", new=AsyncMock(side_effect=OperationsNotFoundError("tenant secret"))):
            response = await self.client.get(self._url(f"customers/{uuid4()}"))
        self.assertEqual(response.status_code, 404); self.assertNotIn("tenant secret", response.text)
        with patch("app.api.v1.operations.service.list_customers", new=AsyncMock(side_effect=OperationsValidationError("query detail"))):
            response = await self.client.get(self._url("customers"))
        self.assertEqual(response.status_code, 422); self.assertNotIn("query detail", response.text)

    @staticmethod
    def _url(path: str, business_id: UUID = BUSINESS_ID) -> str:
        return f"/api/v1/businesses/{business_id}/{path}"

    def _private(self, response: httpx.Response) -> None:
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")


class _FakeSession:
    def __init__(self): self.commit_calls = 0; self.rollback_calls = 0
    async def commit(self): self.commit_calls += 1
    async def rollback(self): self.rollback_calls += 1


def _customer() -> Customer:
    return Customer(id=uuid4(), business_id=BUSINESS_ID, display_name="Acme", first_name=None, last_name=None, email=None, phone=None, status="active", source="manual", tags=[], company=None, notes=None, active=True, created_at=NOW, updated_at=NOW)


def _lead(*, stage: str = "new") -> CRMLead:
    return CRMLead(id=uuid4(), business_id=BUSINESS_ID, customer_id=None, owner_user_id=None, display_name="Lead", company=None, email=None, phone=None, stage=stage, source="manual", priority="medium", qualification_state="unqualified", estimated_value=None, currency="USD", expected_close_date=None, next_follow_up_at=None, notes=None, created_at=NOW, updated_at=NOW)


def _notification() -> Notification:
    return Notification(id=uuid4(), business_id=BUSINESS_ID, recipient_user_id=USER_ID, category="system", title="Alert", message="Review this.", priority="medium", read=False, related_entity_type=None, related_entity_id=None, created_at=NOW, updated_at=NOW)
