from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.billing import ENTITLEMENTS, add_billing_period, require_entitlement, utc_month_period, validate_entitlement_value
from app.models.ai_agent_execution import AIAgentExecution
from app.models.automation import AutomationWorkflow, AutomationWorkflowRun
from app.models.billing import (
    BillingAuditEvent,
    BillingPlan,
    BillingPlanEntitlement,
    BillingPlanVersion,
    BillingSubscriptionEvent,
    BusinessEntitlementOverride,
    BusinessSubscription,
)
from app.models.business_membership import BusinessMembership
from app.models.chatbot import ChatbotSession
from app.models.integration import IntegrationConnection


class BillingError(RuntimeError):
    code = "billing_error"


class BillingConfigurationError(BillingError):
    code = "billing_configuration_unavailable"


class BillingNotFoundError(BillingError):
    code = "billing_resource_not_found"


class BillingConflictError(BillingError):
    code = "billing_conflict"


class BillingTestModeDisabledError(BillingError):
    code = "billing_test_mode_disabled"


class BillingEntitlementError(BillingError):
    def __init__(self, code: str, entitlement_key: str, *, current: int | None = None, limit: int | None = None):
        super().__init__(code)
        self.code = code
        self.entitlement_key = entitlement_key
        self.current = current
        self.limit = limit


@dataclass(frozen=True, slots=True)
class EntitlementSnapshot:
    business_id: UUID
    subscription_id: UUID | None
    plan_id: UUID
    plan_version_id: UUID
    plan_code: str
    plan_name: str
    plan_version: int
    subscription_status: str
    access_reason: str
    billing_interval: str
    current_period_start: datetime
    current_period_end: datetime
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    cancel_at_period_end: bool
    entitlements: dict[str, bool | int]


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    period_start: datetime
    period_end: datetime
    usage: dict[str, int]
    limits: dict[str, int]
    remaining: dict[str, int]
    informational: dict[str, int]


@dataclass(frozen=True, slots=True)
class PlanCatalogItem:
    id: UUID
    version_id: UUID
    code: str
    display_name: str
    description: str
    version: int
    currency: str
    monthly_price_minor: int | None
    yearly_price_minor: int | None
    trial_days: int
    active: bool
    public: bool
    entitlements: dict[str, bool | int]


def _value(row: BillingPlanEntitlement | BusinessEntitlementOverride) -> bool | int:
    if row.boolean_value is not None:
        return row.boolean_value
    if row.integer_value is None:
        raise BillingConfigurationError("entitlement_value_missing")
    return row.integer_value


async def _load_plan_version(
    session: AsyncSession, *, code: str | None = None, version_id: UUID | None = None,
) -> tuple[BillingPlan, BillingPlanVersion]:
    statement = select(BillingPlan, BillingPlanVersion).join(
        BillingPlanVersion, BillingPlanVersion.plan_id == BillingPlan.id,
    )
    if code is not None:
        statement = statement.where(
            BillingPlan.code == code,
            BillingPlan.active.is_(True),
            BillingPlanVersion.active.is_(True),
        ).order_by(BillingPlanVersion.version.desc())
    elif version_id is not None:
        statement = statement.where(BillingPlanVersion.id == version_id)
    else:
        raise ValueError("plan_selector_required")
    row = (await session.execute(statement.limit(1))).one_or_none()
    if row is None:
        raise BillingConfigurationError("plan_version_unavailable")
    plan, version = row
    return plan, version


async def _load_entitlements(session: AsyncSession, version_id: UUID) -> dict[str, bool | int]:
    rows = list((await session.scalars(select(BillingPlanEntitlement).where(
        BillingPlanEntitlement.plan_version_id == version_id,
    ))).all())
    values: dict[str, bool | int] = {
        key: (False if definition.value_type == "boolean" else 0)
        for key, definition in ENTITLEMENTS.items()
    }
    for row in rows:
        definition = require_entitlement(row.entitlement_key)
        value = _value(row)
        validate_entitlement_value(definition.key, value)
        values[definition.key] = value
    return values


def _subscription_access(subscription: BusinessSubscription | None, now: datetime) -> tuple[bool, str]:
    if subscription is None:
        return False, "subscription_missing"
    if subscription.status == "trialing":
        if subscription.trial_ends_at is None or subscription.trial_ends_at <= now:
            return False, "trial_expired"
        if subscription.cancel_at_period_end and subscription.current_period_end <= now:
            return False, "subscription_canceled"
        return True, "trial_active"
    if subscription.status == "active":
        if subscription.cancel_at_period_end and subscription.current_period_end <= now:
            return False, "subscription_canceled"
        return True, "subscription_active"
    return False, f"subscription_{subscription.status}"


def _logical_period(subscription: BusinessSubscription, now: datetime) -> tuple[datetime, datetime]:
    start, end = subscription.current_period_start, subscription.current_period_end
    if subscription.status in {"active", "trialing"} and not subscription.cancel_at_period_end:
        while end <= now:
            start, end = end, add_billing_period(end, subscription.billing_interval)  # type: ignore[arg-type]
    return start, end


async def resolve_entitlements(
    session: AsyncSession, *, business_id: UUID, now: datetime | None = None,
) -> EntitlementSnapshot:
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    subscription = await session.scalar(select(BusinessSubscription).where(
        BusinessSubscription.business_id == business_id,
    ))
    permitted, access_reason = _subscription_access(subscription, instant)
    if permitted and subscription is not None:
        plan, version = await _load_plan_version(session, version_id=subscription.plan_version_id)
        period_start, period_end = _logical_period(subscription, instant)
        status = subscription.status
        interval = subscription.billing_interval
        trial_start = subscription.trial_started_at
        trial_end = subscription.trial_ends_at
        cancel_at_end = subscription.cancel_at_period_end
    else:
        plan, version = await _load_plan_version(session, code="free")
        period_start, period_end = utc_month_period(instant)
        status = subscription.status if subscription is not None else "none"
        interval = "month"
        trial_start = subscription.trial_started_at if subscription is not None else None
        trial_end = subscription.trial_ends_at if subscription is not None else None
        cancel_at_end = subscription.cancel_at_period_end if subscription is not None else False
    values = await _load_entitlements(session, version.id)
    if permitted:
        overrides = list((await session.scalars(select(BusinessEntitlementOverride).where(
            BusinessEntitlementOverride.business_id == business_id,
        ).order_by(
            BusinessEntitlementOverride.entitlement_key,
            BusinessEntitlementOverride.created_at.desc(),
            BusinessEntitlementOverride.id.desc(),
        ))).all())
        seen: set[str] = set()
        for override in overrides:
            if override.entitlement_key in seen:
                continue
            seen.add(override.entitlement_key)
            if not override.active or (override.expires_at is not None and override.expires_at <= instant):
                continue
            value = _value(override)
            validate_entitlement_value(override.entitlement_key, value)
            values[override.entitlement_key] = value
    return EntitlementSnapshot(
        business_id=business_id,
        subscription_id=subscription.id if subscription is not None else None,
        plan_id=plan.id,
        plan_version_id=version.id,
        plan_code=plan.code,
        plan_name=plan.display_name,
        plan_version=version.version,
        subscription_status=status,
        access_reason=access_reason,
        billing_interval=interval,
        current_period_start=period_start,
        current_period_end=period_end,
        trial_started_at=trial_start,
        trial_ends_at=trial_end,
        cancel_at_period_end=cancel_at_end,
        entitlements=values,
    )


async def current_usage(
    session: AsyncSession, *, business_id: UUID, snapshot: EntitlementSnapshot | None = None,
    now: datetime | None = None,
) -> UsageSnapshot:
    resolved = snapshot or await resolve_entitlements(session, business_id=business_id, now=now)
    start, end = resolved.current_period_start, resolved.current_period_end
    ai_row = (await session.execute(select(
        func.count(AIAgentExecution.id),
        func.coalesce(func.sum(AIAgentExecution.input_tokens), 0),
        func.coalesce(func.sum(AIAgentExecution.output_tokens), 0),
    ).where(
        AIAgentExecution.business_id == business_id,
        AIAgentExecution.created_at >= start,
        AIAgentExecution.created_at < end,
    ))).one()
    chatbot_row = (await session.execute(select(
        func.count(ChatbotSession.id),
        func.coalesce(func.sum(ChatbotSession.message_count), 0),
        func.coalesce(func.sum(ChatbotSession.ai_response_count), 0),
    ).where(
        ChatbotSession.business_id == business_id,
        ChatbotSession.started_at >= start,
        ChatbotSession.started_at < end,
    ))).one()
    automation_runs = await session.scalar(select(func.count(AutomationWorkflowRun.id)).where(
        AutomationWorkflowRun.business_id == business_id,
        AutomationWorkflowRun.created_at >= start,
        AutomationWorkflowRun.created_at < end,
    )) or 0
    members = await session.scalar(select(func.count(BusinessMembership.id)).where(
        BusinessMembership.business_id == business_id,
        BusinessMembership.status == "active",
    )) or 0
    active_workflows = await session.scalar(select(func.count(AutomationWorkflow.id)).where(
        AutomationWorkflow.business_id == business_id,
        AutomationWorkflow.status == "active",
        AutomationWorkflow.enabled.is_(True),
    )) or 0
    integrations = await session.scalar(select(func.count(IntegrationConnection.id)).where(
        IntegrationConnection.business_id == business_id,
        IntegrationConnection.status.in_(("connected", "degraded")),
        IntegrationConnection.authentication_state == "authorized",
    )) or 0
    usage = {
        "max_ai_executions_month": int(ai_row[0]),
        "max_ai_input_tokens_month": int(ai_row[1]),
        "max_ai_output_tokens_month": int(ai_row[2]),
        "max_chatbot_sessions_month": int(chatbot_row[0]),
        "max_chatbot_messages_month": int(chatbot_row[1]),
        "max_automation_runs_month": int(automation_runs),
        "max_members": int(members),
        "max_active_workflows": int(active_workflows),
        "max_integrations": int(integrations),
    }
    limits = {
        key: int(resolved.entitlements[key]) for key in usage
    }
    total_chatbot_messages = int(chatbot_row[1])
    chatbot_ai_responses = int(chatbot_row[2])
    return UsageSnapshot(
        period_start=start,
        period_end=end,
        usage=usage,
        limits=limits,
        remaining={key: max(0, limits[key] - value) for key, value in usage.items()},
        informational={
            "chatbot_customer_messages_month": max(0, total_chatbot_messages - chatbot_ai_responses),
            "chatbot_ai_responses_month": chatbot_ai_responses,
        },
    )


async def _quota_lock(session: AsyncSession, business_id: UUID, key: str) -> None:
    # Callers keep this transaction open through the protected source write.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"), {
        "lock_key": f"billing:{business_id}-{key}",
    })


async def require_feature(session: AsyncSession, *, business_id: UUID, key: str) -> EntitlementSnapshot:
    definition = require_entitlement(key)
    if definition.kind != "feature":
        raise ValueError("feature_entitlement_required")
    snapshot = await resolve_entitlements(session, business_id=business_id)
    if snapshot.entitlements[key] is not True:
        raise BillingEntitlementError("feature_not_in_plan", key)
    return snapshot


async def require_capacity(
    session: AsyncSession, *, business_id: UUID, key: str, increment: int = 1,
) -> tuple[EntitlementSnapshot, UsageSnapshot]:
    definition = require_entitlement(key)
    if definition.kind not in {"usage", "resource"} or increment < 1:
        raise ValueError("capacity_entitlement_required")
    await _quota_lock(session, business_id, key)
    snapshot = await resolve_entitlements(session, business_id=business_id)
    usage = await current_usage(session, business_id=business_id, snapshot=snapshot)
    current, limit = usage.usage[key], usage.limits[key]
    if current + increment > limit:
        raise BillingEntitlementError("usage_limit_reached", key, current=current, limit=limit)
    return snapshot, usage


async def list_public_plans(session: AsyncSession, *, include_hidden: bool = False) -> list[PlanCatalogItem]:
    statement = select(BillingPlan, BillingPlanVersion).join(
        BillingPlanVersion, BillingPlanVersion.plan_id == BillingPlan.id,
    ).where(BillingPlanVersion.active.is_(True))
    if not include_hidden:
        statement = statement.where(BillingPlan.active.is_(True), BillingPlan.public.is_(True))
    rows = (await session.execute(statement.order_by(BillingPlan.sort_order, BillingPlanVersion.version.desc()))).all()
    result: list[PlanCatalogItem] = []
    seen: set[UUID] = set()
    for plan, version in rows:
        if plan.id in seen:
            continue
        seen.add(plan.id)
        result.append(PlanCatalogItem(
            plan.id, version.id, plan.code, plan.display_name, plan.description,
            version.version, version.currency, version.monthly_price_minor,
            version.yearly_price_minor, plan.trial_days, plan.active, plan.public,
            await _load_entitlements(session, version.id),
        ))
    return result


async def ensure_free_subscription(
    session: AsyncSession, *, business_id: UUID, now: datetime | None = None,
) -> BusinessSubscription:
    existing = await session.scalar(select(BusinessSubscription).where(BusinessSubscription.business_id == business_id))
    if existing is not None:
        return existing
    plan, version = await _load_plan_version(session, code="free")
    start, end = utc_month_period((now or datetime.now(UTC)).astimezone(UTC))
    subscription = BusinessSubscription(
        business_id=business_id, plan_id=plan.id, plan_version_id=version.id,
        status="active", source="free_default", billing_interval="month",
        provider="disabled", current_period_start=start, current_period_end=end,
        trial_started_at=None, trial_ends_at=None, cancel_at_period_end=False,
    )
    session.add(subscription)
    await session.flush()
    record_subscription_event(
        session,
        subscription=subscription,
        event_type="subscription_created",
        idempotency_key=f"subscription-created:{subscription.id}",
        from_status=None,
        reason="Tenant assigned the explicit Free baseline.",
        now=start,
    )
    return subscription


async def activate_test_subscription(
    session: AsyncSession, *, business_id: UUID, target_plan: PlanCatalogItem,
    billing_interval: str, actor_user_id: UUID, now: datetime | None = None,
) -> BusinessSubscription:
    """Durably activate one canonical paid plan without commercial evidence.

    The service repeats the server-side feature gate so it cannot be safely
    reused as an unrestricted entitlement mutation primitive.
    """
    if not settings.billing_test_mode:
        raise BillingTestModeDisabledError("billing_test_mode_disabled")
    if (
        target_plan.code == "free"
        or not target_plan.active
        or not target_plan.public
        or billing_interval not in {"month", "year"}
    ):
        raise BillingNotFoundError("test_plan_unavailable")

    plan, version = await _load_plan_version(session, version_id=target_plan.version_id)
    if plan.id != target_plan.id or plan.code != target_plan.code or not plan.public:
        raise BillingNotFoundError("test_plan_unavailable")

    subscription = await session.scalar(select(BusinessSubscription).where(
        BusinessSubscription.business_id == business_id,
    ).with_for_update())
    if subscription is None or subscription.business_id != business_id:
        raise BillingNotFoundError("subscription_missing")
    if (
        subscription.source == "provider"
        or subscription.provider != "disabled"
        or subscription.provider_customer_reference is not None
        or subscription.provider_subscription_reference is not None
    ):
        raise BillingConflictError("provider_managed_subscription")

    if (
        subscription.plan_version_id == version.id
        and subscription.status == "active"
        and not subscription.cancel_at_period_end
    ):
        return subscription

    instant = (now or datetime.now(UTC)).astimezone(UTC)
    previous_status = subscription.status
    previous_version_id = subscription.plan_version_id
    before = {
        "status": subscription.status,
        "plan_id": str(subscription.plan_id),
        "plan_version_id": str(subscription.plan_version_id),
        "source": subscription.source,
        "billing_interval": subscription.billing_interval,
    }
    subscription.plan_id = plan.id
    subscription.plan_version_id = version.id
    subscription.status = "active"
    subscription.source = "billing_test_mode"
    subscription.billing_interval = billing_interval
    subscription.provider = "disabled"
    subscription.provider_customer_reference = None
    subscription.provider_subscription_reference = None
    subscription.current_period_start = instant
    subscription.current_period_end = add_billing_period(instant, billing_interval)  # type: ignore[arg-type]
    subscription.trial_started_at = None
    subscription.trial_ends_at = None
    subscription.cancel_at_period_end = False
    subscription.canceled_at = None
    subscription.ended_at = None
    record_subscription_event(
        session,
        subscription=subscription,
        event_type="test_plan_activated",
        idempotency_key=f"test-plan-activated:{subscription.id}:{instant.isoformat()}",
        actor_user_id=actor_user_id,
        from_status=previous_status,
        from_plan_version_id=previous_version_id,
        reason=f"Owner activated canonical {plan.code} access in billing test mode.",
        now=instant,
    )
    record_billing_audit(
        session,
        event_type="subscription.test_plan_activated",
        target_type="business_subscription",
        reason="Owner activated a canonical plan in billing test mode.",
        actor_user_id=actor_user_id,
        business_id=business_id,
        target_id=subscription.id,
        before=before,
        after={
            "status": subscription.status,
            "plan_id": str(subscription.plan_id),
            "plan_version_id": str(subscription.plan_version_id),
            "source": subscription.source,
            "billing_interval": subscription.billing_interval,
            "provider": subscription.provider,
        },
        now=instant,
    )
    await session.flush()
    return subscription


async def validate_plan_change(
    session: AsyncSession, *, business_id: UUID, target_version_id: UUID,
    owner_user_id: UUID | None = None,
) -> list[dict[str, int | str]]:
    _plan, version = await _load_plan_version(session, version_id=target_version_id)
    target = await _load_entitlements(session, version.id)
    current = await current_usage(session, business_id=business_id)
    blockers: list[dict[str, int | str]] = []
    for key in ("max_members", "max_active_workflows", "max_integrations"):
        if current.usage[key] > int(target[key]):
            blockers.append({"entitlement_key": key, "current": current.usage[key], "target_limit": int(target[key])})
    if owner_user_id is not None:
        owned_businesses = await session.scalar(select(func.count(BusinessMembership.id)).where(
            BusinessMembership.user_id == owner_user_id,
            BusinessMembership.role == "owner",
            BusinessMembership.status == "active",
        )) or 0
        if owned_businesses > int(target["max_businesses"]):
            blockers.append({
                "entitlement_key": "max_businesses",
                "current": int(owned_businesses),
                "target_limit": int(target["max_businesses"]),
            })
    return blockers


def record_subscription_event(
    session: AsyncSession, *, subscription: BusinessSubscription, event_type: str,
    idempotency_key: str, actor_user_id: UUID | None = None,
    from_status: str | None = None, from_plan_version_id: UUID | None = None,
    reason: str | None = None, now: datetime | None = None,
) -> BillingSubscriptionEvent:
    event = BillingSubscriptionEvent(
        subscription_id=subscription.id, business_id=subscription.business_id,
        event_type=event_type, idempotency_key=idempotency_key,
        actor_user_id=actor_user_id, from_status=from_status,
        to_status=subscription.status, from_plan_version_id=from_plan_version_id,
        to_plan_version_id=subscription.plan_version_id, reason=reason,
        created_at=(now or datetime.now(UTC)).astimezone(UTC),
    )
    session.add(event)
    return event


def record_billing_audit(
    session: AsyncSession, *, event_type: str, target_type: str, reason: str,
    actor_user_id: UUID | None, business_id: UUID | None, target_id: UUID | None,
    before: dict[str, object], after: dict[str, object], now: datetime | None = None,
) -> None:
    session.add(BillingAuditEvent(
        event_type=event_type, target_type=target_type, reason=reason.strip(),
        actor_user_id=actor_user_id, business_id=business_id, target_id=target_id,
        before_state=before, after_state=after,
        created_at=(now or datetime.now(UTC)).astimezone(UTC),
    ))
