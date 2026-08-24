from __future__ import annotations

import os
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import ARRAY, CheckConstraint, ForeignKeyConstraint, Numeric
from sqlalchemy.dialects.postgresql import JSONB

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.domain.operations import LEAD_STAGE_TRANSITIONS, ORDER_STATUS_TRANSITIONS, OPPORTUNITY_STATUS_TRANSITIONS  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.conversation import Conversation, ConversationMessage  # noqa: E402
from app.models.crm_lead import CRMLead  # noqa: E402
from app.models.customer import Customer  # noqa: E402
from app.models.notification import Notification  # noqa: E402
from app.models.opportunity import Opportunity  # noqa: E402
from app.models.order import Order, OrderLineItem  # noqa: E402
from app.models.report import BusinessReport  # noqa: E402
from app.schemas.operations import (  # noqa: E402
    ConversationCreate,
    CustomerCreate,
    CustomerUpdate,
    LeadCreate,
    MessageCreate,
    OpportunityCreate,
    OrderCreate,
    OrderLineCreate,
    ReportGenerateRequest,
)


class OperationsModelTests(unittest.TestCase):
    def test_expected_tables_are_registered(self) -> None:
        expected = {CRMLead: "crm_leads", Order: "orders", OrderLineItem: "order_line_items", Conversation: "conversations", ConversationMessage: "conversation_messages", Notification: "notifications", Opportunity: "opportunities", AuditLog: "business_audit_log", BusinessReport: "business_reports"}
        for model, name in expected.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(model.__tablename__, name)
                self.assertIn("business_id", model.__table__.columns)

    def test_customer_is_canonical_and_bounded(self) -> None:
        self.assertIsInstance(Customer.__table__.c.tags.type, ARRAY)
        for column in ("display_name", "first_name", "last_name", "email", "phone", "status", "source", "tags", "company", "notes", "active"):
            self.assertIn(column, Customer.__table__.columns)

    def test_money_uses_fixed_precision_numeric(self) -> None:
        for column in (Order.__table__.c.subtotal, Order.__table__.c.adjustment_amount, Order.__table__.c.total, OrderLineItem.__table__.c.unit_price, CRMLead.__table__.c.estimated_value, Opportunity.__table__.c.estimated_value):
            with self.subTest(column=column.name):
                self.assertIsInstance(column.type, Numeric)
                self.assertEqual((column.type.precision, column.type.scale), (14, 2))

    def test_tenant_references_use_composite_foreign_keys(self) -> None:
        for model, minimum in ((CRMLead, 1), (Order, 1), (OrderLineItem, 2), (Conversation, 1), (ConversationMessage, 1), (Opportunity, 2)):
            composite = [item for item in model.__table__.constraints if isinstance(item, ForeignKeyConstraint) and len(item.column_keys) == 2]
            with self.subTest(model=model.__name__):
                self.assertGreaterEqual(len(composite), minimum)

    def test_reports_are_only_typed_safe_json_container(self) -> None:
        self.assertIsInstance(BusinessReport.__table__.c.metrics.type, JSONB)
        for model in (Customer, CRMLead, Order, Conversation, ConversationMessage, Notification, AuditLog):
            with self.subTest(model=model.__name__):
                self.assertFalse(any(isinstance(column.type, JSONB) for column in model.__table__.columns))
        self.assertIsInstance(Opportunity.__table__.c.provenance.type, JSONB)

    def test_models_exclude_secret_clinical_and_payment_fields(self) -> None:
        forbidden = {"password", "access_token", "refresh_token", "api_key", "authorization", "diagnosis", "prescription", "clinical_notes", "medical_history", "card_number", "payment_token"}
        for model in (Customer, CRMLead, Order, OrderLineItem, Conversation, ConversationMessage, Notification, Opportunity, AuditLog, BusinessReport):
            with self.subTest(model=model.__name__):
                self.assertTrue(forbidden.isdisjoint(model.__table__.columns.keys()))

    def test_core_tables_have_database_check_constraints(self) -> None:
        for model in (Customer, CRMLead, Order, OrderLineItem, Conversation, ConversationMessage, Notification, Opportunity, AuditLog, BusinessReport):
            with self.subTest(model=model.__name__):
                self.assertTrue(any(isinstance(item, CheckConstraint) for item in model.__table__.constraints))

    def test_order_lifecycle_is_terminal_after_completion_or_cancel(self) -> None:
        self.assertEqual(ORDER_STATUS_TRANSITIONS["completed"], frozenset())
        self.assertEqual(ORDER_STATUS_TRANSITIONS["canceled"], frozenset())
        self.assertEqual(ORDER_STATUS_TRANSITIONS["draft"], frozenset({"confirmed", "canceled"}))

    def test_opportunity_lifecycle_is_terminal_after_outcome(self) -> None:
        for state in ("won", "lost", "dismissed"):
            self.assertEqual(OPPORTUNITY_STATUS_TRANSITIONS[state], frozenset())
        self.assertIn("in_progress", OPPORTUNITY_STATUS_TRANSITIONS["open"])

    def test_won_and_lost_leads_are_terminal(self) -> None:
        self.assertEqual(LEAD_STAGE_TRANSITIONS["won"], frozenset())
        self.assertEqual(LEAD_STAGE_TRANSITIONS["lost"], frozenset())
        self.assertIn("qualified", LEAD_STAGE_TRANSITIONS["new"])


class OperationsSchemaTests(unittest.TestCase):
    def test_customer_normalizes_email_tags_and_whitespace(self) -> None:
        value = CustomerCreate(display_name=" Acme Buyer ", email="BUYER@EXAMPLE.COM", tags=["VIP", "vip", " Wholesale "])
        self.assertEqual(value.display_name, "Acme Buyer")
        self.assertEqual(value.email, "buyer@example.com")
        self.assertEqual(value.tags, ["VIP", "Wholesale"])

    def test_customer_rejects_unbounded_or_unsafe_values(self) -> None:
        for payload in ({"display_name": ""}, {"display_name": "A", "source": "Not Safe"}, {"display_name": "A", "tags": ["x" * 41]}, {"display_name": "A", "notes": "x" * 4001}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                CustomerCreate.model_validate(payload)

    def test_customer_required_update_fields_cannot_be_null(self) -> None:
        for field in ("display_name", "status", "source", "tags"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                CustomerUpdate.model_validate({field: None})

    def test_lead_money_currency_and_source_are_bounded(self) -> None:
        base = {"display_name": "Lead", "currency": "USD"}
        LeadCreate.model_validate({**base, "estimated_value": "25.50"})
        for changes in ({"currency": "usd"}, {"estimated_value": "1000000000000"}, {"source": "x" * 33}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                LeadCreate.model_validate({**base, **changes})

    def test_order_requires_lines_and_ignores_no_client_total(self) -> None:
        customer_id = uuid4()
        with self.assertRaises(ValidationError):
            OrderCreate.model_validate({"customer_id": customer_id, "currency": "USD", "lines": []})
        with self.assertRaises(ValidationError):
            OrderCreate.model_validate({"customer_id": customer_id, "currency": "USD", "lines": [{"description": "Item", "quantity": 1, "unit_price": "2"}], "total": "1"})

    def test_order_line_quantity_and_price_are_bounded(self) -> None:
        OrderLineCreate(description="Item", quantity=1, unit_price=Decimal("0"))
        for payload in ({"description": "", "quantity": 1, "unit_price": "1"}, {"description": "Item", "quantity": 0, "unit_price": "1"}, {"description": "Item", "quantity": 1, "unit_price": "-1"}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                OrderLineCreate.model_validate(payload)

    def test_conversation_channel_and_external_reference_are_typed(self) -> None:
        ConversationCreate(channel="manual", external_reference="ticket/123")
        with self.assertRaises(ValidationError):
            ConversationCreate(channel="telegram")
        with self.assertRaises(ValidationError):
            ConversationCreate(channel="email", external_reference="bad ref with spaces")

    def test_message_is_internal_or_outbound_but_never_claims_delivery(self) -> None:
        self.assertEqual(MessageCreate(content=" Internal note ").content, "Internal note")
        with self.assertRaises(ValidationError):
            MessageCreate(direction="inbound", content="Spoof")
        with self.assertRaises(ValidationError):
            MessageCreate(content="x" * 10001)

    def test_opportunity_value_requires_safe_currency(self) -> None:
        base = {"title": "Grow", "description": "Follow demand", "category": "sales", "source": "manual"}
        OpportunityCreate.model_validate(base)
        with self.assertRaises(ValidationError):
            OpportunityCreate.model_validate({**base, "currency": "usd"})
        with self.assertRaises(ValidationError):
            OpportunityCreate.model_validate({**base, "category": "Unsafe category"})

    def test_report_period_is_bounded(self) -> None:
        ReportGenerateRequest(report_type="sales", period_start=date(2026, 1, 1), period_end=date(2026, 12, 31))
        for start, end in ((date(2026, 2, 1), date(2026, 1, 1)), (date(2025, 1, 1), date(2026, 2, 1))):
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                ReportGenerateRequest(report_type="sales", period_start=start, period_end=end)

    def test_aware_follow_up_is_required(self) -> None:
        with self.assertRaises(ValidationError):
            LeadCreate(display_name="Lead", currency="USD", next_follow_up_at=datetime(2026, 1, 1))
        LeadCreate(display_name="Lead", currency="USD", next_follow_up_at=datetime(2026, 1, 1, tzinfo=UTC))
