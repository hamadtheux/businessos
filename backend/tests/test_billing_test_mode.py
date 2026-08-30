from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

from fastapi import HTTPException, Response


os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "billing-test-secret-with-at-least-thirty-two-bytes")

from app.api.dependencies.business import BusinessAccessContext, get_business_access  # noqa: E402
from app.api.v1.billing import create_plan_change_intent  # noqa: E402
from app.billing.provider import BillingProviderUnavailableError  # noqa: E402
from app.core.config import Settings, settings  # noqa: E402
from app.models.billing import (  # noqa: E402
    BillingAuditEvent,
    BillingPlan,
    BillingPlanVersion,
    BillingSubscriptionEvent,
    BillingWebhookEvent,
    BusinessSubscription,
)
from app.models.business import Business  # noqa: E402
from app.models.business_membership import BusinessMembership  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.billing import PlanChangeIntentRequest  # noqa: E402
from app.services.billing import (  # noqa: E402
    BillingConflictError,
    BillingTestModeDisabledError,
    PlanCatalogItem,
    activate_test_subscription,
    resolve_entitlements,
)


NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


class BillingTestModeConfigurationTests(unittest.TestCase):
    def test_billing_test_mode_defaults_off_and_requires_explicit_opt_in(self) -> None:
        default = Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://database.invalid/test",
            auth_secret_key="x" * 32,
        )
        enabled = Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://database.invalid/test",
            auth_secret_key="x" * 32,
            billing_test_mode=True,
        )

        self.assertFalse(default.billing_test_mode)
        self.assertTrue(enabled.billing_test_mode)


class BillingTestActivationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.owner = _user("owner@example.test")
        self.business_a = _business("Business A", "business-a")
        self.business_b = _business("Business B", "business-b")
        self.free_plan, self.free_version, self.free_catalog = _plan("free", "Free", 0)
        self.starter_plan, self.starter_version, self.starter_catalog = _plan(
            "starter", "Starter", 2_900
        )
        self.pro_plan, self.pro_version, self.pro_catalog = _plan("pro", "Pro", 14_900)
        self.pro_entitlements = {
            "advanced_analytics": True,
            "ai_agents": True,
            "max_ai_executions_month": 15_000,
        }
        self.free_entitlements = {
            "advanced_analytics": False,
            "ai_agents": True,
            "max_ai_executions_month": 20,
        }
        self.starter_entitlements = {
            "advanced_analytics": False,
            "ai_agents": True,
            "max_ai_executions_month": 1_000,
        }
        self.subscription_a = _subscription(
            self.business_a.id, self.free_plan.id, self.free_version.id,
        )
        self.subscription_b = _subscription(
            self.business_b.id, self.free_plan.id, self.free_version.id,
        )
        self.store = {
            self.business_a.id: self.subscription_a,
            self.business_b.id: self.subscription_b,
        }
        self.session = _DurableBillingSession(self.store)

    async def test_activation_is_rejected_by_the_service_when_mode_is_off(self) -> None:
        with patch.object(settings, "billing_test_mode", False):
            with self.assertRaises(BillingTestModeDisabledError):
                await activate_test_subscription(
                    self.session,
                    business_id=self.business_a.id,
                    target_plan=self.pro_catalog,
                    billing_interval="month",
                    actor_user_id=self.owner.id,
                    now=NOW,
                )

        self.assertEqual(self.subscription_a.plan_version_id, self.free_version.id)
        self.assertEqual(self.session.flush_calls, 0)
        self.assertEqual(self.session.added, [])

    async def test_activation_persists_pro_entitlements_for_only_one_tenant(self) -> None:
        with self._billing_service_patches(), patch.object(settings, "billing_test_mode", True):
            activated = await activate_test_subscription(
                self.session,
                business_id=self.business_a.id,
                target_plan=self.pro_catalog,
                billing_interval="month",
                actor_user_id=self.owner.id,
                now=NOW,
            )
            await self.session.commit()

            # A new session reading the shared durable store represents a later
            # request after refresh, re-login, or browser restart.
            later_session = _DurableBillingSession(self.store)
            first_overview = await resolve_entitlements(
                later_session, business_id=self.business_a.id, now=NOW,
            )
            second_overview = await resolve_entitlements(
                later_session, business_id=self.business_a.id, now=NOW,
            )
            other_overview = await resolve_entitlements(
                later_session, business_id=self.business_b.id, now=NOW,
            )

        self.assertIs(activated, self.subscription_a)
        self.assertEqual(self.session.flush_calls, 1)
        self.assertEqual(self.session.commit_calls, 1)
        self.assertEqual(self.subscription_a.plan_id, self.pro_plan.id)
        self.assertEqual(self.subscription_a.plan_version_id, self.pro_version.id)
        self.assertEqual(self.subscription_a.source, "billing_test_mode")
        self.assertEqual(self.subscription_a.status, "active")
        self.assertEqual(self.subscription_a.provider, "disabled")
        self.assertIsNone(self.subscription_a.provider_customer_reference)
        self.assertIsNone(self.subscription_a.provider_subscription_reference)
        self.assertEqual(first_overview.plan_code, "pro")
        self.assertEqual(second_overview.plan_code, "pro")
        self.assertIs(first_overview.entitlements["advanced_analytics"], True)
        self.assertEqual(first_overview.entitlements["max_ai_executions_month"], 15_000)
        self.assertEqual(other_overview.plan_code, "free")
        self.assertEqual(self.subscription_b.plan_version_id, self.free_version.id)
        self.assertEqual(self.subscription_b.source, "free_default")

        events = [item for item in self.session.added if isinstance(item, BillingSubscriptionEvent)]
        audits = [item for item in self.session.added if isinstance(item, BillingAuditEvent)]
        webhooks = [item for item in self.session.added if isinstance(item, BillingWebhookEvent)]
        self.assertEqual([item.event_type for item in events], ["test_plan_activated"])
        self.assertEqual([item.event_type for item in audits], ["subscription.test_plan_activated"])
        self.assertEqual(webhooks, [])
        commercial_fields = (
            self.subscription_a.provider_customer_reference,
            self.subscription_a.provider_subscription_reference,
        )
        self.assertEqual(commercial_fields, (None, None))

    async def test_provider_managed_subscription_cannot_be_overwritten(self) -> None:
        self.subscription_a.source = "provider"
        with self._billing_service_patches(), patch.object(settings, "billing_test_mode", True):
            with self.assertRaises(BillingConflictError):
                await activate_test_subscription(
                    self.session,
                    business_id=self.business_a.id,
                    target_plan=self.pro_catalog,
                    billing_interval="month",
                    actor_user_id=self.owner.id,
                    now=NOW,
                )

        self.assertEqual(self.subscription_a.plan_version_id, self.free_version.id)
        self.assertEqual(self.session.flush_calls, 0)

    async def test_two_businesses_activate_and_persist_independent_plans(
        self,
    ) -> None:
        with self._billing_service_patches(), patch.object(
            settings, "billing_test_mode", True
        ):
            await activate_test_subscription(
                self.session,
                business_id=self.business_a.id,
                target_plan=self.pro_catalog,
                billing_interval="month",
                actor_user_id=self.owner.id,
                now=NOW,
            )
            await self.session.commit()
            await activate_test_subscription(
                self.session,
                business_id=self.business_b.id,
                target_plan=self.starter_catalog,
                billing_interval="month",
                actor_user_id=self.owner.id,
                now=NOW,
            )
            await self.session.commit()

            # A later durable session represents refresh, business switching,
            # logout/login, or a browser restart.
            later_session = _DurableBillingSession(self.store)
            business_a = await resolve_entitlements(
                later_session, business_id=self.business_a.id, now=NOW
            )
            business_b = await resolve_entitlements(
                later_session, business_id=self.business_b.id, now=NOW
            )
            business_a_again = await resolve_entitlements(
                later_session, business_id=self.business_a.id, now=NOW
            )

        self.assertEqual(business_a.plan_code, "pro")
        self.assertEqual(business_b.plan_code, "starter")
        self.assertEqual(business_a_again.plan_code, "pro")
        self.assertEqual(self.subscription_a.plan_version_id, self.pro_version.id)
        self.assertEqual(
            self.subscription_b.plan_version_id, self.starter_version.id
        )
        self.assertEqual(self.subscription_a.source, "billing_test_mode")
        self.assertEqual(self.subscription_b.source, "billing_test_mode")

    def _billing_service_patches(self):
        async def load_plan(*_args, code=None, version_id=None, **_kwargs):
            if code == "free" or version_id == self.free_version.id:
                return self.free_plan, self.free_version
            if code == "starter" or version_id == self.starter_version.id:
                return self.starter_plan, self.starter_version
            if code == "pro" or version_id == self.pro_version.id:
                return self.pro_plan, self.pro_version
            raise AssertionError("unexpected plan lookup")

        async def load_entitlements(_session, version_id):
            if version_id == self.pro_version.id:
                return dict(self.pro_entitlements)
            if version_id == self.starter_version.id:
                return dict(self.starter_entitlements)
            if version_id == self.free_version.id:
                return dict(self.free_entitlements)
            raise AssertionError("unexpected entitlement lookup")

        return _PatchGroup(
            patch("app.services.billing._load_plan_version", side_effect=load_plan),
            patch("app.services.billing._load_entitlements", side_effect=load_entitlements),
        )


class BillingTestActivationApiTests(unittest.IsolatedAsyncioTestCase):
    _billing_service_patches = BillingTestActivationServiceTests._billing_service_patches

    async def asyncSetUp(self) -> None:
        await BillingTestActivationServiceTests.asyncSetUp(self)
        self.access = BusinessAccessContext(
            user=self.owner,
            business=self.business_a,
            membership=_membership(self.business_a.id, self.owner.id, role="owner"),
        )

    async def test_test_mode_activates_via_real_endpoint_without_provider_call(self) -> None:
        provider_factory = Mock()
        with (
            self._billing_service_patches(),
            patch.object(settings, "billing_test_mode", True),
            patch("app.api.v1.billing.list_public_plans", AsyncMock(return_value=[self.pro_catalog])),
            patch("app.api.v1.billing.validate_plan_change", AsyncMock(return_value=[])),
            patch("app.api.v1.billing.get_billing_provider", provider_factory),
        ):
            result = await create_plan_change_intent(
                PlanChangeIntentRequest(plan_code="pro", billing_interval="month"),
                self.access,
                Response(),
                self.session,
            )

        self.assertEqual(result.status, "test_activated")
        self.assertEqual(result.message, "Pro activated for testing.")
        self.assertIsNotNone(result.billing)
        self.assertEqual(result.billing.business_id, self.business_a.id)  # type: ignore[union-attr]
        self.assertEqual(result.billing.plan_code, "pro")  # type: ignore[union-attr]
        self.assertTrue(result.billing.test_plan_activation_enabled)  # type: ignore[union-attr]
        self.assertEqual(self.subscription_a.source, "billing_test_mode")
        self.assertEqual(self.session.commit_calls, 1)
        provider_factory.assert_not_called()

    async def test_mode_off_restores_truthful_provider_required_behavior(self) -> None:
        provider = Mock()
        provider.create_checkout = AsyncMock(
            side_effect=BillingProviderUnavailableError("billing_provider_unavailable"),
        )
        with (
            patch.object(settings, "billing_test_mode", False),
            patch("app.api.v1.billing.list_public_plans", AsyncMock(return_value=[self.pro_catalog])),
            patch("app.api.v1.billing.validate_plan_change", AsyncMock(return_value=[])),
            patch("app.api.v1.billing.get_billing_provider", return_value=provider),
        ):
            result = await create_plan_change_intent(
                PlanChangeIntentRequest(plan_code="pro", billing_interval="month"),
                self.access,
                Response(),
                self.session,
            )

        self.assertEqual(result.status, "provider_unavailable")
        self.assertIn("not configured", result.message)
        self.assertEqual(self.subscription_a.plan_version_id, self.free_version.id)
        self.assertEqual(self.subscription_a.source, "free_default")
        provider.create_checkout.assert_awaited_once_with(
            business_id=self.business_a.id,
            plan_code="pro",
            interval="month",
        )

    async def test_arbitrary_plan_id_is_rejected_before_mutation_or_provider(self) -> None:
        provider_factory = Mock()
        with (
            patch.object(settings, "billing_test_mode", True),
            patch("app.api.v1.billing.list_public_plans", AsyncMock(return_value=[self.pro_catalog])),
            patch("app.api.v1.billing.get_billing_provider", provider_factory),
        ):
            with self.assertRaises(HTTPException) as raised:
                await create_plan_change_intent(
                    PlanChangeIntentRequest(plan_code="secret_unlimited", billing_interval="month"),
                    self.access,
                    Response(),
                    self.session,
                )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(self.subscription_a.plan_version_id, self.free_version.id)
        self.assertEqual(self.session.commit_calls, 0)
        provider_factory.assert_not_called()

    async def test_non_owner_is_rejected_before_catalog_or_subscription_access(self) -> None:
        member_access = BusinessAccessContext(
            user=self.owner,
            business=self.business_a,
            membership=_membership(self.business_a.id, self.owner.id, role="member"),
        )
        catalog = AsyncMock(return_value=[self.pro_catalog])
        with patch("app.api.v1.billing.list_public_plans", catalog):
            with self.assertRaises(HTTPException) as raised:
                await create_plan_change_intent(
                    PlanChangeIntentRequest(plan_code="pro", billing_interval="month"),
                    member_access,
                    Response(),
                    self.session,
                )

        self.assertEqual(raised.exception.status_code, 403)
        catalog.assert_not_awaited()
        self.assertEqual(self.session.commit_calls, 0)

    async def test_changing_business_id_without_membership_is_rejected(self) -> None:
        denied_session = _DeniedAccessSession()
        with self.assertRaises(HTTPException) as raised:
            await get_business_access(self.business_b.id, self.owner, denied_session)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(self.subscription_b.plan_version_id, self.free_version.id)
        self.assertEqual(self.session.commit_calls, 0)


class _PatchGroup:
    def __init__(self, *patchers) -> None:
        self.patchers = patchers

    def __enter__(self):
        for patcher in self.patchers:
            patcher.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()


class _ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class _DurableBillingSession:
    def __init__(self, subscriptions: dict[UUID, BusinessSubscription]) -> None:
        self.subscriptions = subscriptions
        self.added: list[object] = []
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0

    async def scalar(self, statement):
        sql = str(statement).casefold()
        if "count(" in sql:
            return 0
        parameters = statement.compile().params.values()
        for value in parameters:
            if isinstance(value, UUID) and value in self.subscriptions:
                return self.subscriptions[value]
        return None

    async def scalars(self, _statement) -> _ScalarRows:
        return _ScalarRows([])

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


class _DeniedAccessResult:
    def one_or_none(self):
        return None


class _DeniedAccessSession:
    async def execute(self, _statement) -> _DeniedAccessResult:
        return _DeniedAccessResult()


def _user(email: str) -> User:
    item = User(
        email=email,
        password_hash="stored-test-hash",
        first_name="Owner",
        last_name=None,
        status="active",
        is_email_verified=True,
    )
    item.id = uuid4()
    item.created_at = NOW
    item.updated_at = NOW
    return item


def _business(name: str, slug: str) -> Business:
    item = Business(
        name=name,
        slug=slug,
        business_type="services",
        status="active",
        timezone="UTC",
        currency="USD",
        locale="en",
    )
    item.id = uuid4()
    item.created_at = NOW
    item.updated_at = NOW
    return item


def _membership(business_id: UUID, user_id: UUID, *, role: str) -> BusinessMembership:
    item = BusinessMembership(
        business_id=business_id,
        user_id=user_id,
        role=role,
        status="active",
    )
    item.id = uuid4()
    item.created_at = NOW
    item.updated_at = NOW
    return item


def _plan(code: str, display_name: str, monthly_price_minor: int):
    plan = BillingPlan(
        code=code,
        display_name=display_name,
        description=f"{display_name} plan",
        active=True,
        public=True,
        sort_order=1,
        trial_days=0,
    )
    plan.id = uuid4()
    plan.created_at = NOW
    plan.updated_at = NOW
    version = BillingPlanVersion(
        plan_id=plan.id,
        version=1,
        currency="USD",
        monthly_price_minor=monthly_price_minor,
        yearly_price_minor=monthly_price_minor * 10,
        active=True,
        effective_at=NOW,
        retired_at=None,
    )
    version.id = uuid4()
    version.created_at = NOW
    version.updated_at = NOW
    catalog = PlanCatalogItem(
        id=plan.id,
        version_id=version.id,
        code=code,
        display_name=display_name,
        description=plan.description,
        version=version.version,
        currency=version.currency,
        monthly_price_minor=version.monthly_price_minor,
        yearly_price_minor=version.yearly_price_minor,
        trial_days=0,
        active=True,
        public=True,
        entitlements={},
    )
    return plan, version, catalog


def _subscription(business_id: UUID, plan_id: UUID, version_id: UUID) -> BusinessSubscription:
    item = BusinessSubscription(
        business_id=business_id,
        plan_id=plan_id,
        plan_version_id=version_id,
        status="active",
        source="free_default",
        billing_interval="month",
        provider="disabled",
        provider_customer_reference=None,
        provider_subscription_reference=None,
        current_period_start=datetime(2026, 8, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 9, 1, tzinfo=UTC),
        trial_started_at=None,
        trial_ends_at=None,
        cancel_at_period_end=False,
        canceled_at=None,
        ended_at=None,
    )
    item.id = uuid4()
    item.created_at = NOW
    item.updated_at = NOW
    return item


if __name__ == "__main__":
    unittest.main()
