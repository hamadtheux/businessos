from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic import ValidationError


os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "billing-test-secret-with-at-least-thirty-two-bytes")

from app.billing.provider import BillingProviderUnavailableError, DisabledBillingProvider  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.domain.background_jobs import JOB_POLICIES  # noqa: E402
from app.domain.billing import (  # noqa: E402
    ENTITLEMENTS,
    FEATURE_ENTITLEMENTS,
    LEGACY_INTEGER_ENTITLEMENT_KEYS,
    RESOURCE_ENTITLEMENTS,
    USAGE_ENTITLEMENTS,
    add_billing_period,
    require_entitlement,
    utc_month_period,
    validate_entitlement_value,
)
from app.main import app  # noqa: E402
from app.models.billing import (  # noqa: E402
    BillingAuditEvent,
    BillingPlan,
    BillingPlanEntitlement,
    BillingPlanVersion,
    BillingSubscriptionEvent,
    BillingWebhookEvent,
    BusinessEntitlementOverride,
    BusinessSubscription,
)
from app.schemas.billing import (  # noqa: E402
    AdminEntitlementOverrideRequest,
    AdminPlanVersionRequest,
    PlanChangeIntentRequest,
)
from app.services.billing import (  # noqa: E402
    BillingEntitlementError,
    _logical_period,
    _subscription_access,
    validate_plan_change,
)


class BillingDomainTests(unittest.TestCase):
    def test_registry_is_immutable_and_complete(self) -> None:
        self.assertIsInstance(ENTITLEMENTS, MappingProxyType)
        self.assertEqual(len(ENTITLEMENTS), 22)
        self.assertEqual(len(FEATURE_ENTITLEMENTS), 13)
        self.assertEqual(len(RESOURCE_ENTITLEMENTS), 3)
        self.assertEqual(len(USAGE_ENTITLEMENTS), 6)
        self.assertEqual(LEGACY_INTEGER_ENTITLEMENT_KEYS, {"max_businesses"})
        self.assertNotIn("max_businesses", ENTITLEMENTS)
        with self.assertRaises(TypeError):
            ENTITLEMENTS["new_feature"] = object()  # type: ignore[index,assignment]

    def test_registry_keys_are_disjoint(self) -> None:
        self.assertFalse(FEATURE_ENTITLEMENTS & RESOURCE_ENTITLEMENTS)
        self.assertFalse(FEATURE_ENTITLEMENTS & USAGE_ENTITLEMENTS)
        self.assertFalse(RESOURCE_ENTITLEMENTS & USAGE_ENTITLEMENTS)
        self.assertEqual(FEATURE_ENTITLEMENTS | RESOURCE_ENTITLEMENTS | USAGE_ENTITLEMENTS, set(ENTITLEMENTS))

    def test_feature_values_require_real_booleans(self) -> None:
        validate_entitlement_value("ai_agents", True)
        for invalid in (0, 1, "true", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_entitlement_value("ai_agents", invalid)  # type: ignore[arg-type]

    def test_limit_values_require_nonnegative_real_integers(self) -> None:
        validate_entitlement_value("max_members", 0)
        validate_entitlement_value("max_members", 10)
        for invalid in (True, False, -1, 1.5, "10"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_entitlement_value("max_members", invalid)  # type: ignore[arg-type]

    def test_unknown_entitlement_fails_closed(self) -> None:
        for key in ("unregistered", "max_businesses"):
            with self.subTest(key=key), self.assertRaisesRegex(
                ValueError, "entitlement_key_invalid"
            ):
                require_entitlement(key)

    def test_calendar_months_clamp_month_end(self) -> None:
        self.assertEqual(
            add_billing_period(datetime(2024, 1, 31, 9, tzinfo=UTC), "month"),
            datetime(2024, 2, 29, 9, tzinfo=UTC),
        )
        self.assertEqual(
            add_billing_period(datetime(2025, 1, 31, 9, tzinfo=UTC), "month"),
            datetime(2025, 2, 28, 9, tzinfo=UTC),
        )

    def test_calendar_years_are_not_365_day_approximations(self) -> None:
        self.assertEqual(
            add_billing_period(datetime(2024, 2, 29, 12, tzinfo=UTC), "year"),
            datetime(2025, 2, 28, 12, tzinfo=UTC),
        )

    def test_free_fallback_uses_utc_calendar_month(self) -> None:
        start, end = utc_month_period(datetime(2026, 8, 23, 23, 30, tzinfo=UTC))
        self.assertEqual(start, datetime(2026, 8, 1, tzinfo=UTC))
        self.assertEqual(end, datetime(2026, 9, 1, tzinfo=UTC))

    def test_structured_entitlement_error_has_safe_fields(self) -> None:
        error = BillingEntitlementError("usage_limit_reached", "max_ai_executions_month", current=20, limit=20)
        self.assertEqual(error.code, "usage_limit_reached")
        self.assertEqual(error.entitlement_key, "max_ai_executions_month")
        self.assertEqual((error.current, error.limit), (20, 20))

    def test_subscription_access_lifecycle_fails_to_baseline(self) -> None:
        now = datetime(2026, 8, 23, tzinfo=UTC)
        self.assertEqual(_subscription_access(None, now), (False, "subscription_missing"))
        active = BusinessSubscription(
            status="active", cancel_at_period_end=False,
            current_period_start=now - timedelta(days=30),
            current_period_end=now - timedelta(seconds=1),
        )
        self.assertEqual(_subscription_access(active, now), (True, "subscription_active"))
        active.cancel_at_period_end = True
        self.assertEqual(_subscription_access(active, now), (False, "subscription_canceled"))
        trial = BusinessSubscription(
            status="trialing", cancel_at_period_end=False,
            current_period_start=now - timedelta(days=7),
            current_period_end=now + timedelta(days=23),
            trial_started_at=now - timedelta(days=7),
            trial_ends_at=now + timedelta(days=7),
        )
        self.assertEqual(_subscription_access(trial, now), (True, "trial_active"))
        trial.trial_ends_at = now
        self.assertEqual(_subscription_access(trial, now), (False, "trial_expired"))

    def test_logical_period_rolls_forward_by_calendar_boundary(self) -> None:
        subscription = BusinessSubscription(
            status="active", cancel_at_period_end=False, billing_interval="month",
            current_period_start=datetime(2024, 1, 31, 9, tzinfo=UTC),
            current_period_end=datetime(2024, 2, 29, 9, tzinfo=UTC),
        )
        self.assertEqual(
            _logical_period(subscription, datetime(2024, 3, 1, tzinfo=UTC)),
            (datetime(2024, 2, 29, 9, tzinfo=UTC), datetime(2024, 3, 29, 9, tzinfo=UTC)),
        )


class BillingModelTests(unittest.TestCase):
    def test_expected_durable_tables_exist(self) -> None:
        self.assertEqual({
            BillingPlan.__tablename__, BillingPlanVersion.__tablename__, BillingPlanEntitlement.__tablename__,
            BusinessSubscription.__tablename__, BillingSubscriptionEvent.__tablename__,
            BusinessEntitlementOverride.__tablename__, BillingWebhookEvent.__tablename__, BillingAuditEvent.__tablename__,
        }, {
            "billing_plans", "billing_plan_versions", "billing_plan_entitlements",
            "business_subscriptions", "billing_subscription_events",
            "business_entitlement_overrides", "billing_webhook_events", "billing_audit_events",
        })

    def test_subscription_has_one_tenant_row_and_pinned_version(self) -> None:
        constraints = {item.name for item in BusinessSubscription.__table__.constraints}
        self.assertIn("uq_business_subscriptions_business", constraints)
        self.assertIn("ck_business_subscriptions_valid_status", constraints)
        self.assertIn("ck_business_subscriptions_valid_trial_period", constraints)
        self.assertIn("ck_business_subscriptions_trialing_requires_period", constraints)
        self.assertIn("fk_business_subscriptions_version_plan", constraints)
        self.assertIn("plan_version_id", BusinessSubscription.__table__.columns)
        self.assertIn("trial_started_at", BusinessSubscription.__table__.columns)

    def test_entitlements_are_typed_not_generic_json(self) -> None:
        columns = set(BillingPlanEntitlement.__table__.columns.keys())
        self.assertIn("boolean_value", columns)
        self.assertIn("integer_value", columns)
        self.assertNotIn("value", columns)
        self.assertNotIn("payload", columns)

    def test_subscription_model_contains_no_payment_instrument_fields(self) -> None:
        columns = " ".join(BusinessSubscription.__table__.columns.keys()).casefold()
        for forbidden in ("card", "bank", "cvv", "payment_method", "invoice"):
            self.assertNotIn(forbidden, columns)

    def test_event_and_override_history_are_append_only_shapes(self) -> None:
        self.assertNotIn("updated_at", BillingSubscriptionEvent.__table__.columns)
        self.assertNotIn("updated_at", BusinessEntitlementOverride.__table__.columns)
        self.assertIn("reason", BusinessEntitlementOverride.__table__.columns)

    def test_background_queue_registers_billing_maintenance(self) -> None:
        policy = JOB_POLICIES["maintain_subscription"]
        self.assertEqual(policy.reference_field, "subscription_id")
        self.assertTrue(policy.retryable)


class BillingSchemaTests(unittest.TestCase):
    def test_plan_change_accepts_only_calendar_intervals(self) -> None:
        self.assertEqual(PlanChangeIntentRequest(plan_code="growth", billing_interval="year").billing_interval, "year")
        with self.assertRaises(ValidationError):
            PlanChangeIntentRequest(plan_code="growth", billing_interval="quarter")  # type: ignore[arg-type]

    def test_override_requires_exactly_one_typed_value(self) -> None:
        valid = AdminEntitlementOverrideRequest(entitlement_key="ai_agents", boolean_value=True, reason="Support exception")
        self.assertTrue(valid.boolean_value)
        for values in ({}, {"boolean_value": True, "integer_value": 1}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                AdminEntitlementOverrideRequest(entitlement_key="ai_agents", reason="Support exception", **values)

    def test_plan_versions_require_bounded_prices(self) -> None:
        with self.assertRaises(ValidationError):
            AdminPlanVersionRequest(currency="USD", monthly_price_minor=-1, entitlements={}, reason="Commercial update")


class BillingConfigurationTests(unittest.TestCase):
    def test_platform_admin_identities_are_normalized_and_deduplicated(self) -> None:
        settings = Settings(
            database_url="postgresql+asyncpg://database.invalid/test",
            auth_secret_key="x" * 32,
            platform_admin_emails=[" Admin@Example.com ", "admin@example.com"],
        )
        self.assertEqual(settings.platform_admin_emails, ["admin@example.com"])
        self.assertEqual(settings.billing_provider, "disabled")

    def test_invalid_platform_admin_identity_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(database_url="postgresql+asyncpg://database.invalid/test", auth_secret_key="x" * 32, platform_admin_emails=["not-an-email"])


class DisabledProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkout_fails_truthfully(self) -> None:
        with self.assertRaisesRegex(BillingProviderUnavailableError, "billing_provider_unavailable"):
            await DisabledBillingProvider().create_checkout(business_id=uuid4(), plan_code="pro", interval="month")

    async def test_portal_and_mutations_fail_truthfully(self) -> None:
        provider = DisabledBillingProvider()
        for call in (
            provider.create_customer(business_id=uuid4(), owner_email="owner@example.com"),
            provider.create_portal(business_id=uuid4()),
            provider.cancel_subscription(provider_subscription_reference="opaque"),
            provider.change_subscription(provider_subscription_reference="opaque", plan_code="pro", interval="year"),
        ):
            with self.assertRaises(BillingProviderUnavailableError):
                await call

    async def test_unverified_webhooks_fail_closed(self) -> None:
        with self.assertRaises(BillingProviderUnavailableError):
            await DisabledBillingProvider().verify_and_normalize_webhook(body=b"{}", headers={})


class BillingPlanChangeValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_change_does_not_query_account_owned_business_count(
        self,
    ) -> None:
        business_id = uuid4()
        target_version_id = uuid4()
        session = AsyncMock()
        session.scalar.side_effect = AssertionError(
            "plan validation must not inspect account business ownership"
        )
        usage = {
            "max_members": 1,
            "max_active_workflows": 0,
            "max_integrations": 0,
        }
        target = {
            "max_members": 5,
            "max_active_workflows": 5,
            "max_integrations": 5,
        }

        with (
            patch(
                "app.services.billing._load_plan_version",
                AsyncMock(
                    return_value=(
                        SimpleNamespace(),
                        SimpleNamespace(id=target_version_id),
                    )
                ),
            ),
            patch(
                "app.services.billing._load_entitlements",
                AsyncMock(return_value=target),
            ),
            patch(
                "app.services.billing.current_usage",
                AsyncMock(return_value=SimpleNamespace(usage=usage)),
            ),
        ):
            blockers = await validate_plan_change(
                session,
                business_id=business_id,
                target_version_id=target_version_id,
            )

        self.assertEqual(blockers, [])
        session.scalar.assert_not_awaited()

    async def test_plan_change_still_blocks_target_business_resource_excess(
        self,
    ) -> None:
        business_id = uuid4()
        target_version_id = uuid4()
        usage = {
            "max_members": 6,
            "max_active_workflows": 2,
            "max_integrations": 1,
        }
        target = {
            "max_members": 5,
            "max_active_workflows": 5,
            "max_integrations": 5,
        }

        with (
            patch(
                "app.services.billing._load_plan_version",
                AsyncMock(
                    return_value=(
                        SimpleNamespace(),
                        SimpleNamespace(id=target_version_id),
                    )
                ),
            ),
            patch(
                "app.services.billing._load_entitlements",
                AsyncMock(return_value=target),
            ),
            patch(
                "app.services.billing.current_usage",
                AsyncMock(return_value=SimpleNamespace(usage=usage)),
            ),
        ):
            blockers = await validate_plan_change(
                AsyncMock(),
                business_id=business_id,
                target_version_id=target_version_id,
            )

        self.assertEqual(
            blockers,
            [
                {
                    "entitlement_key": "max_members",
                    "current": 6,
                    "target_limit": 5,
                }
            ],
        )


class BillingApiContractTests(unittest.TestCase):
    def test_business_and_platform_routes_are_exposed(self) -> None:
        paths = app.openapi()["paths"]
        for path in (
            "/api/v1/businesses/{business_id}/billing",
            "/api/v1/businesses/{business_id}/billing/usage",
            "/api/v1/businesses/{business_id}/billing/plans",
            "/api/v1/businesses/{business_id}/billing/change-intent",
            "/api/v1/businesses/{business_id}/billing/cancel",
            "/api/v1/businesses/{business_id}/billing/reactivate",
            "/api/v1/platform/billing/plans",
            "/api/v1/platform/billing/subscriptions",
            "/api/v1/platform/billing/subscriptions/{subscription_id}/events",
            "/api/v1/platform/billing/audit",
            "/api/v1/platform/billing/metrics",
        ):
            with self.subTest(path=path):
                self.assertIn(path, paths)

    def test_business_billing_routes_require_authentication(self) -> None:
        paths = app.openapi()["paths"]
        for path, methods in paths.items():
            if "/businesses/{business_id}/billing" not in path:
                continue
            for operation in methods.values():
                self.assertTrue(operation["security"], f"{path} must be authenticated")

    def test_platform_admin_assignment_is_providerless_and_audited(self) -> None:
        source = (Path(__file__).parents[1] / "app/api/v1/billing.py").read_text()
        assignment = source.split('@router.put("/platform/billing/businesses/{business_id}/subscription"', 1)[1].split('@router.post("/platform/billing/businesses/{business_id}/trial-extension"', 1)[0]
        self.assertIn('source="platform_admin"', assignment)
        self.assertIn('provider="disabled"', assignment)
        self.assertIn("subscription.plan_assigned", assignment)
        self.assertNotIn("create_checkout", assignment)
        self.assertNotIn("provider_customer_reference", assignment)

    def test_webhook_is_provider_specific_and_not_a_trusted_generic_json_route(self) -> None:
        paths = app.openapi()["paths"]
        self.assertIn("/api/v1/billing/webhooks/{provider_name}", paths)
        self.assertNotIn("/api/v1/billing/webhooks", paths)

    def test_migration_is_linear_and_seeds_explicit_free_and_legacy(self) -> None:
        source = (Path(__file__).parents[1] / "alembic/versions/b8e1f4a7c962_add_saas_billing_foundation.py").read_text()
        self.assertIn('down_revision: str | None = "a5d9e2f8b074"', source)
        self.assertIn('"free": {', source)
        self.assertIn('"legacy": {', source)
        self.assertIn("legacy_bootstrap", source)
        self.assertIn("subscription_created", source)
        self.assertIn("provision_free_business_subscription", source)
        self.assertIn("enforce_owner_business_entitlement", source)
        self.assertIn("billing_effective_boolean", source)
        self.assertIn("enforce_business_member_entitlement", source)
        self.assertIn("enforce_ai_execution_entitlement", source)
        self.assertIn("enforce_chatbot_message_entitlement", source)
        self.assertIn("enforce_automation_run_entitlement", source)
        self.assertIn("enforce_plan_version_immutability", source)
        self.assertIn("enforce_plan_entitlement_immutability", source)
        self.assertNotIn("unlimited", source.casefold())

    def test_new_migration_removes_only_owner_business_plan_enforcement(
        self,
    ) -> None:
        source = (
            Path(__file__).parents[1]
            / "alembic/versions/1c9d4e7f2a6b_remove_owner_business_plan_limit.py"
        ).read_text()
        self.assertIn('down_revision: str | None = "f0a7b6c5d4e3"', source)
        self.assertIn(
            "DROP TRIGGER IF EXISTS trg_business_memberships_owner_limit",
            source,
        )
        self.assertIn(
            "DROP FUNCTION IF EXISTS enforce_owner_business_entitlement()",
            source,
        )
        self.assertNotIn("provision_free_business_subscription", source)
        self.assertNotIn("business_subscriptions", source)
        self.assertNotIn("UPDATE ", source)
        self.assertNotIn("DELETE ", source)

    def test_subscription_scheduler_skips_already_queued_daily_maintenance(self) -> None:
        source = (Path(__file__).parents[1] / "app/services/job_scheduler.py").read_text()
        self.assertIn("maintenance_already_queued", source)
        self.assertIn("~maintenance_already_queued", source)

    def test_guarded_mutation_sources_use_central_billing_service(self) -> None:
        root = Path(__file__).parents[1] / "app"
        for relative in (
            "api/v1/ai_agents.py", "api/v1/ai_workforce.py", "api/v1/marketing.py",
            "api/v1/chatbot.py", "api/v1/scheduling.py", "api/v1/operations.py",
            "services/automation.py", "services/integrations.py", "services/chatbot.py",
        ):
            with self.subTest(relative=relative):
                self.assertIn("billing", (root / relative).read_text())


if __name__ == "__main__":
    unittest.main()
