from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.commerce import CommerceValidationError  # noqa: E402
from app.integrations.commerce_registry import CommerceConnectorRegistry  # noqa: E402
from app.main import app  # noqa: E402
from app.models.commerce import (  # noqa: E402
    AudienceSegment,
    AudienceSegmentMember,
    CatalogMedia,
    CatalogSource,
    CatalogVariant,
    CommerceConnection,
    CommerceEvent,
    CommerceFeedDestination,
    CommerceFeedProductStatus,
    CommerceSyncIssue,
    CommerceSyncRun,
    CommerceWebhookReceipt,
    ExternalCustomerMapping,
    ExternalOrderMapping,
    ExternalProductMapping,
)
from app.models.order import OrderAddress, OrderFulfillment, OrderRefund, OrderRefundLine  # noqa: E402
from app.schemas.commerce import (  # noqa: E402
    AudienceRule,
    AudienceRuleCondition,
    AudienceSegmentCompileRequest,
    AudienceSegmentCreate,
    CommerceEventCreate,
    NormalizedProduct,
)
from app.services.automation_copilot import (  # noqa: E402
    _condition,
    _missing_information,
    _proposed_actions,
    _refinement_context,
    _required_integrations,
    _stop_conditions,
    _trigger_type,
    _wait_seconds,
)
from app.services.commerce import (  # noqa: E402
    _matching_customers,
    _validate_no_sensitive_targeting,
    compile_segment,
    create_segment,
    ingest_event,
)


class _AsyncContext:
    def __init__(self) -> None:
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, _type, _value, _traceback):
        return False


class _ScalarRows:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return self.values


class _ConcurrentEventSession:
    def __init__(self, existing: CommerceEvent) -> None:
        self.existing = existing
        self.scalar_calls = 0
        self.savepoint = _AsyncContext()

    async def scalar(self, _statement):
        self.scalar_calls += 1
        return None if self.scalar_calls == 1 else self.existing

    def begin_nested(self):
        return self.savepoint

    def add(self, _value) -> None:
        pass

    async def flush(self) -> None:
        raise IntegrityError("insert", {}, Exception("duplicate"))


class _AudienceMatchSession:
    def __init__(self, rows, customer_ids) -> None:
        self.rows = rows
        self.customer_ids = customer_ids

    async def execute(self, _statement):
        return _ScalarRows(self.rows)

    async def scalars(self, _statement):
        return _ScalarRows(self.customer_ids)


class CommerceModelTests(unittest.TestCase):
    def test_all_commerce_records_are_tenant_owned(self) -> None:
        models = (
            CommerceConnection, CatalogSource, CommerceSyncRun,
            ExternalProductMapping, CatalogVariant, CatalogMedia,
            CommerceSyncIssue, CommerceEvent, AudienceSegment,
            AudienceSegmentMember, CommerceFeedDestination,
            CommerceFeedProductStatus,
            CommerceWebhookReceipt, ExternalCustomerMapping,
            ExternalOrderMapping, OrderAddress, OrderFulfillment,
            OrderRefund, OrderRefundLine,
        )
        for model in models:
            with self.subTest(model=model.__name__):
                self.assertIn("business_id", model.__table__.columns)

    def test_cross_record_references_bind_parent_and_business(self) -> None:
        models = (
            CatalogSource, CommerceSyncRun, ExternalProductMapping,
            CatalogVariant, CatalogMedia, CommerceSyncIssue, CommerceEvent,
            AudienceSegmentMember, CommerceFeedProductStatus,
            CommerceWebhookReceipt, ExternalCustomerMapping,
            ExternalOrderMapping, OrderAddress, OrderFulfillment,
            OrderRefund, OrderRefundLine,
        )
        for model in models:
            with self.subTest(model=model.__name__):
                self.assertTrue(any(
                    isinstance(constraint, ForeignKeyConstraint)
                    and len(constraint.column_keys) == 2
                    and "business_id" in constraint.column_keys
                    for constraint in model.__table__.constraints
                ))

    def test_external_product_identity_and_events_are_idempotent_per_tenant(self) -> None:
        mapping_uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in ExternalProductMapping.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        event_uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in CommerceEvent.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertIn(
            ("business_id", "provider", "external_account_id", "external_object_id"),
            mapping_uniques,
        )
        self.assertIn(("business_id", "source", "external_event_id"), event_uniques)

    def test_domain_tables_never_store_credentials_or_raw_provider_responses(self) -> None:
        forbidden = {
            "api_key", "secret", "access_token", "refresh_token", "oauth_token",
            "authorization", "credential", "raw_response",
        }
        for model in (
            CommerceConnection, CatalogSource, CommerceSyncRun,
            ExternalProductMapping, CommerceEvent, CommerceFeedDestination,
        ):
            with self.subTest(model=model.__name__):
                self.assertTrue(forbidden.isdisjoint(model.__table__.columns.keys()))


class CommerceContractTests(unittest.TestCase):
    def test_new_http_contracts_are_tenant_scoped_and_additive(self) -> None:
        paths = set(app.openapi()["paths"])
        prefix = "/api/v1/businesses/{business_id}"
        required = {
            f"{prefix}/commerce/connections",
            f"{prefix}/commerce/events",
            f"{prefix}/commerce/audience-segments/compile",
            f"{prefix}/commerce/feed-destinations/{{destination_id}}/products",
            f"{prefix}/automations/copilot/compile",
            f"{prefix}/automations/workflows/{{workflow_id}}/nodes",
            f"{prefix}/catalog",
            f"{prefix}/marketing/campaigns/generate",
        }
        self.assertTrue(required.issubset(paths))

    def test_unassembled_registry_never_claims_external_configuration(self) -> None:
        registry = CommerceConnectorRegistry()
        configured = {
            definition.provider: definition.configured
            for definition in registry.provider_definitions()
        }
        for provider in configured:
            self.assertFalse(configured[provider])

    def test_event_requires_deterministic_or_anonymous_identity(self) -> None:
        base = {
            "event_type": "product_viewed",
            "source": "website",
            "external_event_id": "event-001",
            "occurred_at": datetime.now(UTC),
        }
        with self.assertRaises(ValidationError):
            CommerceEventCreate.model_validate(base)
        event = CommerceEventCreate.model_validate({**base, "anonymous_session_id": "session-0001"})
        self.assertEqual(event.source, "website")

    def test_store_url_rejects_embedded_credentials_and_private_targets(self) -> None:
        from app.schemas.commerce import CommerceConnectionCreate

        for url in ("https://user:secret@example.com", "http://127.0.0.1/store", "http://localhost/store"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                CommerceConnectionCreate(provider="custom_api", display_name="Store", store_url=url)
        public = CommerceConnectionCreate(
            provider="woocommerce", display_name="Store", store_url="https://shop.example.com",
        )
        self.assertEqual(public.store_url.host, "shop.example.com")

    def test_normalized_catalog_contract_is_bounded_and_strict(self) -> None:
        product = NormalizedProduct(
            external_object_id="provider-product-1",
            name="Premium Farm Eggs",
            price="12.50",
            currency="USD",
            inventory_quantity=40,
            availability="in_stock",
        )
        self.assertEqual(product.inventory_quantity, 40)
        with self.assertRaises(ValidationError):
            NormalizedProduct(
                external_object_id="p", name="Eggs", currency="usd",
            )

    def test_sensitive_audience_targeting_fails_closed(self) -> None:
        for text in ("customers inferred to be pregnant", "target a political belief", "religious buyers"):
            with self.subTest(text=text), self.assertRaises(CommerceValidationError):
                _validate_no_sensitive_targeting(text)
        rule = AudienceRule(all=[AudienceRuleCondition(
            field="order.count", operator="gte", value=2, lookback_days=60,
        )])
        self.assertEqual(rule.all[0].lookback_days, 60)

    def test_audience_rules_reject_incompatible_operators_and_unbounded_values(self) -> None:
        invalid = (
            {"field": "event.last_at", "operator": "gte", "value": 30},
            {"field": "order.count", "operator": "within_days", "value": 30},
            {"field": "customer.status", "operator": "gte", "value": 1},
            {"field": "customer.tags", "operator": "contains", "value": "x" * 256},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                AudienceRuleCondition.model_validate(value)
        valid = AudienceRuleCondition(
            field="event.last_at", operator="not_within_days", value=30,
            event_type="product_viewed",
        )
        self.assertEqual(valid.value, 30)

    def test_customer_event_metadata_is_bounded_before_persistence(self) -> None:
        with self.assertRaises(ValidationError):
            CommerceEventCreate(
                event_type="product_viewed", source="website",
                external_event_id="event-large", occurred_at=datetime.now(UTC),
                anonymous_session_id="session-0001",
                safe_metadata={"payload": "x" * 17_000},
            )

    def test_new_json_columns_have_database_size_constraints(self) -> None:
        sync_checks = {constraint.name for constraint in CommerceSyncRun.__table__.constraints}
        variant_checks = {constraint.name for constraint in CatalogVariant.__table__.constraints}
        self.assertIn("ck_commerce_sync_runs_valid_next_cursor", sync_checks)
        self.assertIn("ck_catalog_variants_valid_option_values", variant_checks)


class CommerceServiceSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_duplicate_event_uses_savepoint_and_returns_existing(self) -> None:
        business_id = uuid4()
        existing = CommerceEvent(
            business_id=business_id, event_type="product_viewed", source="website",
            external_event_id="event-001", occurred_at=datetime.now(UTC), safe_metadata={},
        )
        session = _ConcurrentEventSession(existing)
        event, duplicate = await ingest_event(
            session, business_id=business_id, actor_user_id=None,
            data=CommerceEventCreate(
                event_type="product_viewed", source="website",
                external_event_id="event-001", occurred_at=datetime.now(UTC),
                anonymous_session_id="session-0001",
            ),
        )
        self.assertTrue(session.savepoint.entered)
        self.assertTrue(duplicate)
        self.assertIs(event, existing)

    async def test_event_last_at_matches_stale_and_never_seen_customers(self) -> None:
        recent_id, stale_id, never_id = uuid4(), uuid4(), uuid4()
        now = datetime.now(UTC)
        session = _AudienceMatchSession(
            [(recent_id, now - timedelta(days=1)), (stale_id, now - timedelta(days=90))],
            [recent_id, stale_id, never_id],
        )
        matched = await _matching_customers(
            session, business_id=uuid4(), now=now,
            condition=AudienceRuleCondition(
                field="event.last_at", operator="not_within_days", value=30,
                event_type="product_viewed",
            ),
        )
        self.assertEqual(matched, {stale_id, never_id})

    async def test_zero_event_count_includes_customers_without_events(self) -> None:
        observed_id, never_id = uuid4(), uuid4()
        session = _AudienceMatchSession([(observed_id, 2)], [observed_id, never_id])
        matched = await _matching_customers(
            session, business_id=uuid4(), now=datetime.now(UTC),
            condition=AudienceRuleCondition(
                field="event.count", operator="equals", value=0,
                event_type="order_paid",
            ),
        )
        self.assertEqual(matched, {never_id})

    async def test_sensitive_rule_values_are_rejected_before_persistence(self) -> None:
        with self.assertRaises(CommerceValidationError):
            await create_segment(
                object(), business_id=uuid4(), actor_user_id=uuid4(),
                data=AudienceSegmentCreate(
                    name="Unsafe segment",
                    rule=AudienceRule(all=[AudienceRuleCondition(
                        field="customer.tags", operator="contains", value="pregnant",
                    )]),
                ),
            )

    async def test_natural_language_product_rule_fails_when_product_is_unknown(self) -> None:
        session = AsyncMock()
        session.scalar.return_value = None
        with self.assertRaisesRegex(CommerceValidationError, "segment_product_not_found"):
            await compile_segment(
                session, business_id=uuid4(), actor_user_id=uuid4(),
                data=AudienceSegmentCompileRequest(
                    definition="Customers who bought an unknown widget 2 times in the last 30 days",
                ),
            )


class AutomationCopilotCompilerTests(unittest.TestCase):
    def test_abandoned_checkout_prompt_compiles_safe_deterministic_requirements(self) -> None:
        prompt = "When someone abandons checkout, wait two hours, send a WhatsApp message and stop after purchase"
        self.assertEqual(_trigger_type(prompt.casefold()), "checkout_abandoned")
        self.assertEqual(_wait_seconds(prompt.casefold()), 7200)
        numeric = "when checkout is abandoned wait 2 hours and send whatsapp"
        self.assertEqual(_wait_seconds(numeric), 7200)
        required = _required_integrations(numeric)
        self.assertEqual(required, ["whatsapp_business"])
        self.assertIn("authoritative recipient identity", _missing_information(numeric, required))
        self.assertIn("stop immediately after purchase", _stop_conditions(numeric))

    def test_exact_recovery_prompt_returns_ordered_withheld_actions(self) -> None:
        prompt = (
            "when someone abandons checkout, wait 2 hours, send whatsapp if allowed, "
            "then email them if they have not purchased"
        )
        actions = _proposed_actions(prompt)
        self.assertEqual(
            [action["action_type"] for action in actions],
            ["send_whatsapp_message", "send_email"],
        )
        self.assertIn("no purchase has been recorded", actions[1]["condition"])
        self.assertTrue(all(action["execution_state"].startswith("withheld") for action in actions))

    def test_refinement_retains_original_requirements_and_replaces_channel(self) -> None:
        description = "Automation Copilot draft: send WhatsApp after checkout abandonment"
        delayed = _refinement_context(description, "wait 3 hours")
        self.assertIn("whatsapp", delayed)
        replaced = _refinement_context(description, "use email instead")
        self.assertNotIn("whatsapp", replaced)
        self.assertIn("email", replaced)

    def test_order_threshold_is_a_typed_condition(self) -> None:
        self.assertEqual(
            _condition("only for orders above $50", "order_created"),
            {"field": "order.total", "operator": "gt", "value": "50"},
        )
