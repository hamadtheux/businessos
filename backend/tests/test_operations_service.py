from __future__ import annotations

import os
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.operations import OperationsStateError, OperationsValidationError  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.customer import Customer  # noqa: E402
from app.models.opportunity import Opportunity  # noqa: E402
from app.models.order import Order, OrderLineItem  # noqa: E402
from app.schemas.operations import OrderCreate  # noqa: E402
from app.services.operations import (  # noqa: E402
    _page,
    _search_term,
    change_opportunity_status,
    change_order_status,
    create_order,
    core_analytics,
    record_audit,
)


BUSINESS_ID = uuid4()
USER_ID = uuid4()
NOW = datetime(2026, 8, 23, tzinfo=UTC)


class OperationsServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_page_bounds_and_offset_are_deterministic(self) -> None:
        self.assertEqual(_page(1, 25), (0, 25))
        self.assertEqual(_page(3, 10), (20, 10))
        for page, size in ((0, 10), (1, 0), (1, 101)):
            with self.subTest(page=page, size=size), self.assertRaises(OperationsValidationError):
                _page(page, size)

    def test_search_is_trimmed_empty_aware_and_bounded(self) -> None:
        self.assertIsNone(_search_term(None))
        self.assertIsNone(_search_term("   "))
        self.assertEqual(_search_term(" Acme "), "Acme")
        with self.assertRaises(OperationsValidationError):
            _search_term("x" * 101)

    def test_audit_record_contains_only_explicit_bounded_metadata(self) -> None:
        session = _ObjectSession()
        entry = record_audit(session, business_id=BUSINESS_ID, actor_user_id=USER_ID, event_type="order.updated", entity_type="order", entity_id=uuid4(), summary="s" * 1200, before_value="b" * 700, after_value="a" * 700)
        self.assertIsInstance(entry, AuditLog)
        self.assertEqual(len(entry.summary), 1000)
        self.assertEqual(len(entry.before_value or ""), 500)
        self.assertEqual(len(entry.after_value or ""), 500)
        self.assertIs(session.added[-1], entry)

    async def test_create_order_calculates_decimal_totals_server_side(self) -> None:
        customer_id = uuid4()
        session = _OrderSession(customer_id)
        payload = OrderCreate(customer_id=customer_id, currency="USD", adjustment_amount=Decimal("1.50"), lines=[{"description": "Widget", "quantity": 2, "unit_price": Decimal("10.25")}, {"description": "Setup", "quantity": 1, "unit_price": Decimal("0.10")}])
        order = await create_order(session, business_id=BUSINESS_ID, actor_user_id=USER_ID, data=payload)
        self.assertEqual(order.subtotal, Decimal("20.60"))
        self.assertEqual(order.total, Decimal("22.10"))
        self.assertEqual(order.adjustment_amount, Decimal("1.50"))
        self.assertTrue(order.order_number.startswith("ORD-"))
        self.assertEqual(len([item for item in session.added if isinstance(item, OrderLineItem)]), 2)
        self.assertTrue(any(isinstance(item, AuditLog) for item in session.added))

    async def test_create_order_rejects_foreign_or_archived_customer(self) -> None:
        session = _OrderSession(None)
        payload = OrderCreate(customer_id=uuid4(), currency="USD", lines=[{"description": "Widget", "quantity": 1, "unit_price": "1"}])
        with self.assertRaises(OperationsValidationError):
            await create_order(session, business_id=BUSINESS_ID, actor_user_id=USER_ID, data=payload)

    async def test_order_transitions_flush_and_audit(self) -> None:
        order = _order("draft")
        session = _ScalarSession(order)
        changed = await change_order_status(session, business_id=BUSINESS_ID, order_id=order.id, actor_user_id=USER_ID, status="confirmed")
        self.assertEqual(changed.status, "confirmed")
        self.assertEqual(session.flush_calls, 1)
        self.assertTrue(any(isinstance(item, AuditLog) for item in session.added))

    async def test_order_transition_cannot_skip_lifecycle(self) -> None:
        order = _order("draft")
        with self.assertRaises(OperationsStateError):
            await change_order_status(_ScalarSession(order), business_id=BUSINESS_ID, order_id=order.id, actor_user_id=USER_ID, status="completed")

    async def test_completed_order_is_terminal(self) -> None:
        order = _order("completed")
        with self.assertRaises(OperationsStateError):
            await change_order_status(_ScalarSession(order), business_id=BUSINESS_ID, order_id=order.id, actor_user_id=USER_ID, status="canceled")

    async def test_opportunity_transition_is_explicit_and_audited(self) -> None:
        opportunity = Opportunity(id=uuid4(), business_id=BUSINESS_ID, title="Grow", description="Follow demand", category="sales", source="manual", priority="high", estimated_value=Decimal("20"), currency="USD", status="open", customer_id=None, lead_id=None, created_at=NOW, updated_at=NOW)
        session = _ScalarSession(opportunity)
        changed = await change_opportunity_status(session, business_id=BUSINESS_ID, opportunity_id=opportunity.id, actor_user_id=USER_ID, status="in_progress")
        self.assertEqual(changed.status, "in_progress")
        self.assertTrue(any(isinstance(item, AuditLog) for item in session.added))

    async def test_won_opportunity_is_terminal(self) -> None:
        opportunity = Opportunity(id=uuid4(), business_id=BUSINESS_ID, title="Grow", description="Follow demand", category="sales", source="manual", priority="high", estimated_value=None, currency=None, status="won", customer_id=None, lead_id=None, created_at=NOW, updated_at=NOW)
        with self.assertRaises(OperationsStateError):
            await change_opportunity_status(_ScalarSession(opportunity), business_id=BUSINESS_ID, opportunity_id=opportunity.id, actor_user_id=USER_ID, status="lost")

    async def test_core_analytics_maps_database_aggregates_without_fabrication(self) -> None:
        session = _AnalyticsSession()
        result = await core_analytics(session, business_id=BUSINESS_ID, period_start=date(2026, 8, 1), period_end=date(2026, 8, 7))
        self.assertEqual(result.customers, 3)
        self.assertEqual(result.leads, 4)
        self.assertEqual(result.orders, 2)
        self.assertEqual(result.order_revenue, Decimal("30.00"))
        self.assertEqual(result.average_order_value, Decimal("15.00"))
        self.assertEqual(result.crm_stage_counts, {"new": 3, "won": 1})
        self.assertEqual(result.revenue_series[0].revenue, Decimal("30.00"))
        tenant_queries = [query for query in session.statements if "businesses.timezone" not in query]
        self.assertTrue(tenant_queries)
        self.assertTrue(all("business_id" in query for query in tenant_queries))

    async def test_core_analytics_rejects_unbounded_period_before_querying(self) -> None:
        session = _AnalyticsSession()
        with self.assertRaises(OperationsValidationError):
            await core_analytics(session, business_id=BUSINESS_ID, period_start=date(2025, 1, 1), period_end=date(2026, 8, 1))
        self.assertEqual(session.statements, [])


class _ScalarSession:
    def __init__(self, scalar): self.value = scalar; self.added = []; self.flush_calls = 0
    async def scalar(self, _statement): return self.value
    def add(self, value): self.added.append(value)
    async def flush(self): self.flush_calls += 1


class _ObjectSession:
    def __init__(self): self.added = []
    def add(self, value): self.added.append(value)


class _OrderSession:
    def __init__(self, customer_id): self.customer_id = customer_id; self.added = []; self.flush_calls = 0
    async def scalar(self, _statement): return self.customer_id
    def add(self, value): self.added.append(value)
    def add_all(self, values): self.added.extend(values)
    async def flush(self):
        self.flush_calls += 1
        for value in self.added:
            if getattr(value, "id", None) is None: value.id = uuid4()


class _Rows:
    def __init__(self, values): self.values = values
    def all(self): return self.values


class _AnalyticsSession:
    def __init__(self): self.statements = []
    async def scalar(self, statement):
        sql = str(statement); self.statements.append(sql)
        if "businesses.timezone" in sql: return "UTC"
        if "sum(orders.total)" in sql: return Decimal("30")
        counts = {"customers.id": 3, "crm_leads.id": 4, "orders.id": 2, "appointments.id": 5, "service_providers.id": 2, "opportunities.id": 1, "ai_agent_executions.id": 6, "ai_actions.id": 7}
        return next((value for key, value in counts.items() if f"count({key})" in sql), 0)
    async def execute(self, statement):
        sql = str(statement); self.statements.append(sql)
        if "date(timezone" in sql: return _Rows([(date(2026, 8, 2), Decimal("30"), 2)])
        if "crm_leads.stage" in sql: return _Rows([("new", 3), ("won", 1)])
        if "crm_leads.source" in sql: return _Rows([("manual", 4)])
        if "appointments.status" in sql: return _Rows([("confirmed", 5)])
        if "opportunities.status" in sql: return _Rows([("open", 1)])
        return _Rows([])


def _order(status: str) -> Order:
    return Order(id=uuid4(), business_id=BUSINESS_ID, customer_id=uuid4(), order_number="ORD-TEST", status=status, source="manual", currency="USD", subtotal=Decimal("10"), adjustment_amount=Decimal("0"), total=Decimal("10"), notes=None, created_at=NOW, updated_at=NOW)
