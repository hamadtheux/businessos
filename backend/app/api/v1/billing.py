from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency
from app.api.dependencies.platform_admin import PlatformAdminDependency
from app.billing.provider import BillingProviderUnavailableError, get_billing_provider
from app.core.config import settings
from app.db.session import get_db_session
from app.domain.billing import add_billing_period, require_entitlement, utc_month_period, validate_entitlement_value
from app.models.billing import (
    BillingAuditEvent,
    BillingPlan,
    BillingPlanEntitlement,
    BillingPlanVersion,
    BillingSubscriptionEvent,
    BusinessEntitlementOverride,
    BusinessSubscription,
)
from app.models.business import Business
from app.models.notification import Notification
from app.schemas.billing import (
    AdminBillingAuditItem,
    AdminBillingAuditPage,
    AdminBillingMetrics,
    AdminEntitlementOverrideRequest,
    AdminPlanAvailabilityRequest,
    AdminPlanVersionRequest,
    AdminSubscriptionAssignRequest,
    AdminSubscriptionEventItem,
    AdminSubscriptionEventPage,
    AdminSubscriptionItem,
    AdminSubscriptionPage,
    AdminSubscriptionStatusRequest,
    AdminTrialExtensionRequest,
    BillingOverviewResponse,
    CancellationRequest,
    PlanChangeIntentRequest,
    PlanChangeIntentResponse,
    PlanResponse,
    SubscriptionMutationResponse,
    UsageResponse,
)
from app.services.billing import (
    BillingConflictError,
    BillingConfigurationError,
    BillingNotFoundError,
    BillingTestModeDisabledError,
    activate_test_subscription,
    current_usage,
    list_public_plans,
    record_billing_audit,
    record_subscription_event,
    resolve_entitlements,
    validate_plan_change,
)


router = APIRouter(tags=["Billing"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _owner(access: BusinessAccessDependency) -> None:
    if access.membership.role != "owner":
        raise HTTPException(status_code=403, detail="Business owner access is required.")


def _billing_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Billing information is temporarily unavailable.")


def _overview_response(snapshot: object) -> BillingOverviewResponse:
    fields = {
        field: getattr(snapshot, field)
        for field in snapshot.__dataclass_fields__  # type: ignore[attr-defined]
    }
    return BillingOverviewResponse(
        **fields,
        provider_configured=settings.billing_provider != "disabled",
        test_plan_activation_enabled=settings.billing_test_mode,
    )


async def _subscription_for_update(session: AsyncSession, business_id: UUID) -> BusinessSubscription:
    item = await session.scalar(select(BusinessSubscription).where(
        BusinessSubscription.business_id == business_id,
    ).with_for_update())
    if item is None:
        raise BillingNotFoundError("subscription_missing")
    return item


@router.get("/businesses/{business_id}/billing", response_model=BillingOverviewResponse)
async def billing_overview(access: BusinessAccessDependency, response: Response, session: SessionDependency):
    try:
        snapshot = await resolve_entitlements(session, business_id=access.business.id)
    except (BillingConfigurationError, SQLAlchemyError):
        raise _billing_unavailable() from None
    _private(response)
    return _overview_response(snapshot)


@router.get("/businesses/{business_id}/billing/usage", response_model=UsageResponse)
async def billing_usage(access: BusinessAccessDependency, response: Response, session: SessionDependency):
    try:
        usage = await current_usage(session, business_id=access.business.id)
    except (BillingConfigurationError, SQLAlchemyError):
        raise _billing_unavailable() from None
    _private(response)
    return UsageResponse(
        period_start=usage.period_start,
        period_end=usage.period_end,
        usage=usage.usage,
        limits=usage.limits,
        remaining=usage.remaining,
        informational=usage.informational,
    )


@router.get("/businesses/{business_id}/billing/plans", response_model=list[PlanResponse])
async def billing_plans(access: BusinessAccessDependency, response: Response, session: SessionDependency):
    try:
        plans = await list_public_plans(session)
    except (BillingConfigurationError, SQLAlchemyError):
        raise _billing_unavailable() from None
    _private(response)
    return [PlanResponse(**{field: getattr(item, field) for field in item.__dataclass_fields__}) for item in plans]


@router.post("/businesses/{business_id}/billing/change-intent", response_model=PlanChangeIntentResponse)
async def create_plan_change_intent(data: PlanChangeIntentRequest, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    _owner(access)
    try:
        plan = next((item for item in await list_public_plans(session) if item.code == data.plan_code), None)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found.")
        now = datetime.now(UTC)
        subscription = await session.scalar(select(BusinessSubscription).where(
            BusinessSubscription.business_id == access.business.id,
        ))
        if subscription is not None:
            recent = await session.scalar(select(func.count(BillingSubscriptionEvent.id)).where(
                BillingSubscriptionEvent.business_id == access.business.id,
                BillingSubscriptionEvent.event_type.in_((
                    "plan_change_intent", "plan_change_blocked", "test_plan_activated",
                )),
                BillingSubscriptionEvent.created_at >= now - timedelta(minutes=10),
            )) or 0
            if recent >= 5:
                raise HTTPException(status_code=429, detail="Too many plan-change attempts. Try again shortly.")
        blockers = await validate_plan_change(
            session,
            business_id=access.business.id,
            target_version_id=plan.version_id,
            owner_user_id=access.user.id,
        )
        if blockers:
            if subscription is not None:
                record_subscription_event(session, subscription=subscription, event_type="plan_change_blocked", idempotency_key=f"plan-change-blocked:{subscription.id}:{now.isoformat()}", actor_user_id=access.user.id, from_status=subscription.status, reason=f"Target plan {plan.code} is below current resource usage.", now=now)
                await session.commit()
            return PlanChangeIntentResponse(status="blocked", message="Current resources exceed the target plan. Reduce them before downgrading; nothing was deleted.", blockers=blockers)
        if settings.billing_test_mode and plan.code != "free":
            await activate_test_subscription(
                session,
                business_id=access.business.id,
                target_plan=plan,
                billing_interval=data.billing_interval,
                actor_user_id=access.user.id,
                now=now,
            )
            snapshot = await resolve_entitlements(
                session, business_id=access.business.id, now=now,
            )
            await session.commit()
            return PlanChangeIntentResponse(
                status="test_activated",
                message=f"{plan.display_name} activated for testing.",
                billing=_overview_response(snapshot),
            )
        if subscription is not None:
            record_subscription_event(session, subscription=subscription, event_type="plan_change_intent", idempotency_key=f"plan-change-intent:{subscription.id}:{now.isoformat()}", actor_user_id=access.user.id, from_status=subscription.status, reason=f"Requested {plan.code} on {data.billing_interval} interval.", now=now)
            await session.commit()
        try:
            url = await get_billing_provider().create_checkout(
                business_id=access.business.id, plan_code=data.plan_code, interval=data.billing_interval,
            )
        except BillingProviderUnavailableError:
            return PlanChangeIntentResponse(status="provider_unavailable", message="Online plan changes are not configured yet. Your current plan was not changed.")
        return PlanChangeIntentResponse(status="checkout_ready", message="Continue to secure checkout.", checkout_url=url)
    except HTTPException:
        await session.rollback()
        raise
    except BillingTestModeDisabledError:
        await session.rollback()
        raise HTTPException(status_code=403, detail="Billing test activation is disabled.") from None
    except BillingConflictError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="This subscription is managed by the billing provider.") from None
    except BillingNotFoundError:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Plan or subscription not found.") from None
    except (BillingConfigurationError, SQLAlchemyError):
        await session.rollback()
        raise _billing_unavailable() from None
    finally:
        _private(response)


@router.post("/businesses/{business_id}/billing/cancel", response_model=SubscriptionMutationResponse)
async def cancel_subscription(data: CancellationRequest, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    _owner(access)
    now = datetime.now(UTC)
    try:
        subscription = await _subscription_for_update(session, access.business.id)
        snapshot = await resolve_entitlements(session, business_id=access.business.id, now=now)
        if snapshot.plan_code == "free" or subscription.status not in {"active", "trialing"}:
            raise HTTPException(status_code=409, detail="This subscription cannot be canceled.")
        if not subscription.cancel_at_period_end:
            subscription.cancel_at_period_end = True
            subscription.canceled_at = now
            record_subscription_event(session, subscription=subscription, event_type="cancellation_scheduled", idempotency_key=f"cancel:{subscription.id}:{now.isoformat()}", actor_user_id=access.user.id, from_status=subscription.status, reason=data.reason, now=now)
            session.add(Notification(
                business_id=access.business.id, recipient_user_id=None,
                category="billing_subscription", title="Cancellation scheduled",
                message=f"The current plan remains available until {subscription.current_period_end.date().isoformat()} UTC.",
                priority="medium", read=False,
                related_entity_type="business_subscription", related_entity_id=subscription.id,
            ))
        await session.commit()
    except HTTPException:
        await session.rollback()
        raise
    except (BillingNotFoundError, BillingConfigurationError, SQLAlchemyError):
        await session.rollback()
        raise _billing_unavailable() from None
    _private(response)
    return SubscriptionMutationResponse(status=subscription.status, cancel_at_period_end=True, current_period_end=subscription.current_period_end)


@router.post("/businesses/{business_id}/billing/reactivate", response_model=SubscriptionMutationResponse)
async def reactivate_subscription(access: BusinessAccessDependency, response: Response, session: SessionDependency):
    _owner(access)
    now = datetime.now(UTC)
    try:
        subscription = await _subscription_for_update(session, access.business.id)
        if subscription.status not in {"active", "trialing"} or not subscription.cancel_at_period_end or subscription.current_period_end <= now:
            raise HTTPException(status_code=409, detail="This subscription cannot be reactivated.")
        subscription.cancel_at_period_end = False
        subscription.canceled_at = None
        record_subscription_event(session, subscription=subscription, event_type="cancellation_reversed", idempotency_key=f"reactivate:{subscription.id}:{now.isoformat()}", actor_user_id=access.user.id, from_status=subscription.status, now=now)
        session.add(Notification(
            business_id=access.business.id, recipient_user_id=None,
            category="billing_subscription", title="Cancellation reversed",
            message="The current plan will continue into its next billing period.",
            priority="medium", read=False,
            related_entity_type="business_subscription", related_entity_id=subscription.id,
        ))
        await session.commit()
    except HTTPException:
        await session.rollback()
        raise
    except (BillingNotFoundError, SQLAlchemyError):
        await session.rollback()
        raise _billing_unavailable() from None
    _private(response)
    return SubscriptionMutationResponse(status=subscription.status, cancel_at_period_end=False, current_period_end=subscription.current_period_end)


@router.post("/billing/webhooks/{provider_name}", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
async def billing_webhook(provider_name: str, request: Request):
    if provider_name != "disabled":
        raise HTTPException(status_code=404, detail="Billing provider not found.")
    try:
        await get_billing_provider().verify_and_normalize_webhook(body=await request.body(), headers=dict(request.headers))
    except BillingProviderUnavailableError:
        raise HTTPException(status_code=503, detail="Billing provider is not configured.") from None
    raise HTTPException(status_code=503, detail="Billing provider is not configured.")


@router.get("/platform/billing/plans", response_model=list[PlanResponse], tags=["Platform Billing"])
async def admin_plans(_admin: PlatformAdminDependency, response: Response, session: SessionDependency):
    plans = await list_public_plans(session, include_hidden=True)
    _private(response)
    return [PlanResponse(**{field: getattr(item, field) for field in item.__dataclass_fields__}) for item in plans]


@router.patch("/platform/billing/plans/{plan_id}/availability", tags=["Platform Billing"])
async def admin_plan_availability(plan_id: UUID, data: AdminPlanAvailabilityRequest, admin: PlatformAdminDependency, response: Response, session: SessionDependency):
    try:
        plan = await session.scalar(select(BillingPlan).where(BillingPlan.id == plan_id).with_for_update())
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found.")
        if plan.code == "free" and not data.active:
            raise HTTPException(status_code=409, detail="The Free fallback plan must remain active.")
        before = {"active": plan.active, "public": plan.public}
        plan.active, plan.public = data.active, data.public
        record_billing_audit(session, event_type="plan.availability_changed", target_type="billing_plan", reason=data.reason, actor_user_id=admin.id, business_id=None, target_id=plan.id, before=before, after={"active": plan.active, "public": plan.public})
        await session.commit()
    except HTTPException:
        await session.rollback(); raise
    except SQLAlchemyError:
        await session.rollback(); raise _billing_unavailable() from None
    _private(response)
    return {"id": plan.id, "active": plan.active, "public": plan.public}


@router.post("/platform/billing/plans/{plan_id}/versions", response_model=PlanResponse, status_code=201, tags=["Platform Billing"])
async def admin_publish_plan_version(plan_id: UUID, data: AdminPlanVersionRequest, admin: PlatformAdminDependency, response: Response, session: SessionDependency):
    from app.domain.billing import ENTITLEMENTS
    now = datetime.now(UTC)
    try:
        plan = await session.scalar(select(BillingPlan).where(BillingPlan.id == plan_id).with_for_update())
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found.")
        if set(data.entitlements) != set(ENTITLEMENTS):
            raise HTTPException(status_code=422, detail="A complete registered entitlement set is required.")
        for key, value in data.entitlements.items():
            validate_entitlement_value(key, value)
        previous = await session.scalar(select(BillingPlanVersion).where(
            BillingPlanVersion.plan_id == plan.id,
        ).order_by(BillingPlanVersion.version.desc()).limit(1).with_for_update())
        next_number = (previous.version if previous else 0) + 1
        if previous is not None:
            previous.active = False
            previous.retired_at = now
        version = BillingPlanVersion(
            plan_id=plan.id, version=next_number, currency=data.currency,
            monthly_price_minor=data.monthly_price_minor,
            yearly_price_minor=data.yearly_price_minor, active=True,
            effective_at=now, retired_at=None,
        )
        session.add(version); await session.flush()
        for key, value in data.entitlements.items():
            session.add(BillingPlanEntitlement(
                plan_version_id=version.id, entitlement_key=key,
                boolean_value=value if type(value) is bool else None,
                integer_value=value if type(value) is int else None,
            ))
        record_billing_audit(session, event_type="plan.version_published", target_type="billing_plan_version", reason=data.reason, actor_user_id=admin.id, business_id=None, target_id=version.id, before={"previous_version": previous.version if previous else None}, after={"version": version.version, "currency": version.currency, "monthly_price_minor": version.monthly_price_minor, "yearly_price_minor": version.yearly_price_minor})
        await session.commit()
        entitlements = data.entitlements
    except HTTPException:
        await session.rollback(); raise
    except (ValueError, SQLAlchemyError):
        await session.rollback(); raise _billing_unavailable() from None
    _private(response)
    return PlanResponse(id=plan.id, version_id=version.id, code=plan.code, display_name=plan.display_name, description=plan.description, version=version.version, currency=version.currency, monthly_price_minor=version.monthly_price_minor, yearly_price_minor=version.yearly_price_minor, trial_days=plan.trial_days, active=plan.active, public=plan.public, entitlements=entitlements)


@router.get("/platform/billing/subscriptions", response_model=AdminSubscriptionPage, tags=["Platform Billing"])
async def admin_subscriptions(_admin: PlatformAdminDependency, response: Response, session: SessionDependency, page: int = 1, page_size: int = 25):
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    base = select(BusinessSubscription, BillingPlan).join(BillingPlan, BillingPlan.id == BusinessSubscription.plan_id)
    rows = (await session.execute(base.order_by(BusinessSubscription.created_at.desc(), BusinessSubscription.id).offset((page - 1) * page_size).limit(page_size))).all()
    total = await session.scalar(select(func.count(BusinessSubscription.id))) or 0
    _private(response)
    return AdminSubscriptionPage(items=[AdminSubscriptionItem(
        business_id=sub.business_id, subscription_id=sub.id, plan_code=plan.code, plan_name=plan.display_name,
        status=sub.status, billing_interval=sub.billing_interval, current_period_end=sub.current_period_end,
        cancel_at_period_end=sub.cancel_at_period_end,
    ) for sub, plan in rows], page=page, page_size=page_size, total=int(total))


@router.get(
    "/platform/billing/subscriptions/{subscription_id}/events",
    response_model=AdminSubscriptionEventPage,
    tags=["Platform Billing"],
)
async def admin_subscription_events(
    subscription_id: UUID,
    _admin: PlatformAdminDependency,
    response: Response,
    session: SessionDependency,
    page: int = 1,
    page_size: int = 25,
):
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    condition = BillingSubscriptionEvent.subscription_id == subscription_id
    rows = list((await session.scalars(select(BillingSubscriptionEvent).where(
        condition,
    ).order_by(
        BillingSubscriptionEvent.created_at.desc(), BillingSubscriptionEvent.id.desc(),
    ).offset((page - 1) * page_size).limit(page_size))).all())
    total = await session.scalar(select(func.count(BillingSubscriptionEvent.id)).where(condition)) or 0
    _private(response)
    return AdminSubscriptionEventPage(
        items=[AdminSubscriptionEventItem.model_validate(item, from_attributes=True) for item in rows],
        page=page,
        page_size=page_size,
        total=int(total),
    )


@router.get(
    "/platform/billing/audit",
    response_model=AdminBillingAuditPage,
    tags=["Platform Billing"],
)
async def admin_billing_audit(
    _admin: PlatformAdminDependency,
    response: Response,
    session: SessionDependency,
    page: int = 1,
    page_size: int = 25,
    business_id: UUID | None = None,
):
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    conditions = [] if business_id is None else [BillingAuditEvent.business_id == business_id]
    rows = list((await session.scalars(select(BillingAuditEvent).where(*conditions).order_by(
        BillingAuditEvent.created_at.desc(), BillingAuditEvent.id.desc(),
    ).offset((page - 1) * page_size).limit(page_size))).all())
    total = await session.scalar(select(func.count(BillingAuditEvent.id)).where(*conditions)) or 0
    _private(response)
    return AdminBillingAuditPage(
        items=[AdminBillingAuditItem.model_validate(item, from_attributes=True) for item in rows],
        page=page,
        page_size=page_size,
        total=int(total),
    )


@router.put("/platform/billing/businesses/{business_id}/subscription", response_model=BillingOverviewResponse, tags=["Platform Billing"])
async def admin_assign_subscription(business_id: UUID, data: AdminSubscriptionAssignRequest, admin: PlatformAdminDependency, response: Response, session: SessionDependency):
    now = datetime.now(UTC)
    try:
        business = await session.scalar(select(Business).where(Business.id == business_id))
        if business is None:
            raise HTTPException(status_code=404, detail="Business not found.")
        row = (await session.execute(select(BillingPlan, BillingPlanVersion).join(BillingPlanVersion, BillingPlanVersion.plan_id == BillingPlan.id).where(BillingPlan.code == data.plan_code, BillingPlan.active.is_(True), BillingPlanVersion.active.is_(True)).order_by(BillingPlanVersion.version.desc()).limit(1))).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Plan not found.")
        plan, version = row
        sub = await session.scalar(select(BusinessSubscription).where(BusinessSubscription.business_id == business_id).with_for_update())
        before = {} if sub is None else {"status": sub.status, "plan_version_id": str(sub.plan_version_id)}
        old_status = sub.status if sub else None
        old_version = sub.plan_version_id if sub else None
        start = now
        end = add_billing_period(start, data.billing_interval)
        created = sub is None
        if created:
            sub = BusinessSubscription(business_id=business_id, plan_id=plan.id, plan_version_id=version.id, status="active", source="platform_admin", billing_interval=data.billing_interval, provider="disabled", current_period_start=start, current_period_end=end, cancel_at_period_end=False)
            session.add(sub); await session.flush()
        sub.plan_id, sub.plan_version_id, sub.source = plan.id, version.id, "platform_admin"
        sub.billing_interval, sub.current_period_start, sub.current_period_end = data.billing_interval, start, end
        sub.status = "trialing" if data.trial_days else "active"
        sub.trial_started_at = start if data.trial_days else None
        sub.trial_ends_at = start + timedelta(days=data.trial_days) if data.trial_days else None
        sub.cancel_at_period_end, sub.canceled_at, sub.ended_at = False, None, None
        if created:
            record_subscription_event(session, subscription=sub, event_type="subscription_created", idempotency_key=f"subscription-created:{sub.id}", actor_user_id=admin.id, from_status=None, reason=data.reason, now=now)
        record_subscription_event(session, subscription=sub, event_type="plan_assigned", idempotency_key=f"admin-assign:{sub.id}:{now.isoformat()}", actor_user_id=admin.id, from_status=old_status, from_plan_version_id=old_version, reason=data.reason, now=now)
        record_billing_audit(session, event_type="subscription.plan_assigned", target_type="business_subscription", reason=data.reason, actor_user_id=admin.id, business_id=business_id, target_id=sub.id, before=before, after={"status": sub.status, "plan_version_id": str(sub.plan_version_id)})
        session.add(Notification(
            business_id=business_id, recipient_user_id=None,
            category="billing_subscription", title="Subscription updated",
            message=f"This workspace is now assigned to the {plan.display_name} plan.",
            priority="medium", read=False,
            related_entity_type="business_subscription", related_entity_id=sub.id,
        ))
        await session.commit()
        snapshot = await resolve_entitlements(session, business_id=business_id)
    except HTTPException:
        await session.rollback(); raise
    except (BillingConfigurationError, SQLAlchemyError):
        await session.rollback(); raise _billing_unavailable() from None
    _private(response)
    return _overview_response(snapshot)


@router.post("/platform/billing/businesses/{business_id}/trial-extension", tags=["Platform Billing"])
async def admin_extend_trial(business_id: UUID, data: AdminTrialExtensionRequest, admin: PlatformAdminDependency, response: Response, session: SessionDependency):
    now = datetime.now(UTC)
    try:
        sub = await _subscription_for_update(session, business_id)
        before = sub.trial_ends_at
        previous_status = sub.status
        if sub.trial_started_at is None:
            sub.trial_started_at = now
        sub.trial_ends_at = max(before or now, now) + timedelta(days=data.days)
        sub.status = "trialing"
        record_subscription_event(session, subscription=sub, event_type="trial_extended", idempotency_key=f"trial-extend:{sub.id}:{now.isoformat()}", actor_user_id=admin.id, from_status=previous_status, reason=data.reason, now=now)
        record_billing_audit(session, event_type="subscription.trial_extended", target_type="business_subscription", reason=data.reason, actor_user_id=admin.id, business_id=business_id, target_id=sub.id, before={"trial_ends_at": before.isoformat() if before else None}, after={"trial_ends_at": sub.trial_ends_at.isoformat()})
        await session.commit()
    except (BillingNotFoundError, SQLAlchemyError):
        await session.rollback(); raise _billing_unavailable() from None
    _private(response)
    return {"subscription_id": sub.id, "status": sub.status, "trial_ends_at": sub.trial_ends_at}


@router.post("/platform/billing/businesses/{business_id}/status", tags=["Platform Billing"])
async def admin_subscription_status(business_id: UUID, data: AdminSubscriptionStatusRequest, admin: PlatformAdminDependency, response: Response, session: SessionDependency):
    now = datetime.now(UTC)
    try:
        sub = await _subscription_for_update(session, business_id)
        before = sub.status
        sub.status = data.status
        if data.status == "active" and sub.current_period_end <= now:
            sub.current_period_start, sub.current_period_end = now, add_billing_period(now, sub.billing_interval)  # type: ignore[arg-type]
        record_subscription_event(session, subscription=sub, event_type=f"subscription_{data.status}", idempotency_key=f"admin-status:{sub.id}:{now.isoformat()}", actor_user_id=admin.id, from_status=before, reason=data.reason, now=now)
        record_billing_audit(session, event_type="subscription.status_changed", target_type="business_subscription", reason=data.reason, actor_user_id=admin.id, business_id=business_id, target_id=sub.id, before={"status": before}, after={"status": sub.status})
        session.add(Notification(
            business_id=business_id, recipient_user_id=None,
            category="billing_subscription", title="Subscription status updated",
            message=f"The workspace subscription status changed to {sub.status}.",
            priority="high" if sub.status == "suspended" else "medium", read=False,
            related_entity_type="business_subscription", related_entity_id=sub.id,
        ))
        await session.commit()
    except (BillingNotFoundError, SQLAlchemyError):
        await session.rollback(); raise _billing_unavailable() from None
    _private(response)
    return {"subscription_id": sub.id, "status": sub.status}


@router.post("/platform/billing/businesses/{business_id}/entitlement-overrides", tags=["Platform Billing"])
async def admin_entitlement_override(business_id: UUID, data: AdminEntitlementOverrideRequest, admin: PlatformAdminDependency, response: Response, session: SessionDependency):
    try:
        require_entitlement(data.entitlement_key)
        value: bool | int = data.boolean_value if data.boolean_value is not None else int(data.integer_value)  # type: ignore[arg-type]
        validate_entitlement_value(data.entitlement_key, value)
        override = BusinessEntitlementOverride(business_id=business_id, entitlement_key=data.entitlement_key, boolean_value=data.boolean_value, integer_value=data.integer_value, active=data.active, expires_at=data.expires_at, reason=data.reason, created_by_user_id=admin.id, created_at=datetime.now(UTC))
        session.add(override); await session.flush()
        record_billing_audit(session, event_type="entitlement.override_created", target_type="entitlement_override", reason=data.reason, actor_user_id=admin.id, business_id=business_id, target_id=override.id, before={}, after={"entitlement_key": data.entitlement_key, "value": value, "active": data.active})
        await session.commit()
    except ValueError as error:
        await session.rollback(); raise HTTPException(status_code=422, detail=str(error)) from None
    except (IntegrityError, SQLAlchemyError):
        await session.rollback(); raise _billing_unavailable() from None
    _private(response)
    return {"id": override.id, "entitlement_key": override.entitlement_key, "active": override.active}


@router.get("/platform/billing/metrics", response_model=AdminBillingMetrics, tags=["Platform Billing"])
async def admin_billing_metrics(_admin: PlatformAdminDependency, response: Response, session: SessionDependency):
    status_rows = (await session.execute(select(BusinessSubscription.status, func.count(BusinessSubscription.id)).group_by(BusinessSubscription.status))).all()
    plan_rows = (await session.execute(select(BillingPlan.code, func.count(BusinessSubscription.id)).join(BusinessSubscription, BusinessSubscription.plan_id == BillingPlan.id).group_by(BillingPlan.code))).all()
    missing = await session.scalar(select(func.count(Business.id)).outerjoin(BusinessSubscription, BusinessSubscription.business_id == Business.id).where(BusinessSubscription.id.is_(None))) or 0
    _private(response)
    return AdminBillingMetrics(subscriptions_by_status={key: int(value) for key, value in status_rows}, subscriptions_by_plan={key: int(value) for key, value in plan_rows}, businesses_without_subscription=int(missing))
