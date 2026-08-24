from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.billing import USAGE_ENTITLEMENTS, add_billing_period
from app.models.billing import BillingSubscriptionEvent, BusinessSubscription
from app.models.notification import Notification
from app.services.billing import current_usage, record_subscription_event, resolve_entitlements


async def _event_exists(session: AsyncSession, key: str) -> bool:
    return await session.scalar(select(BillingSubscriptionEvent.id).where(
        BillingSubscriptionEvent.idempotency_key == key,
    )) is not None


async def maintain_subscription(
    session: AsyncSession, *, subscription_id: UUID, business_id: UUID,
    now: datetime | None = None,
) -> BusinessSubscription | None:
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    subscription = await session.scalar(select(BusinessSubscription).where(
        BusinessSubscription.id == subscription_id,
        BusinessSubscription.business_id == business_id,
    ).with_for_update())
    if subscription is None:
        return None

    if subscription.status == "trialing" and subscription.trial_ends_at is not None:
        if subscription.trial_ends_at <= instant:
            previous = subscription.status
            subscription.status = "expired"
            subscription.ended_at = instant
            key = f"trial-expired:{subscription.id}:{subscription.trial_ends_at.isoformat()}"
            if not await _event_exists(session, key):
                record_subscription_event(
                    session, subscription=subscription, event_type="trial_expired",
                    idempotency_key=key, from_status=previous,
                    reason="Trial ended automatically.", now=instant,
                )
                session.add(Notification(
                    business_id=business_id, recipient_user_id=None, category="billing_trial",
                    title="Your trial has ended",
                    message="Paid feature execution now follows the explicit Free baseline. Your workspace data remains available.",
                    priority="high", read=False, related_entity_type="business_subscription",
                    related_entity_id=subscription.id,
                ))
        elif subscription.trial_ends_at <= instant + timedelta(days=3):
            key = f"trial-ending:{subscription.id}:{subscription.trial_ends_at.date().isoformat()}"
            if not await _event_exists(session, key):
                record_subscription_event(
                    session, subscription=subscription, event_type="trial_ending_notification",
                    idempotency_key=key, from_status=subscription.status,
                    reason="Three-day trial ending notice generated.", now=instant,
                )
                session.add(Notification(
                    business_id=business_id, recipient_user_id=None, category="billing_trial",
                    title="Your trial is ending soon",
                    message="Your trial ends within three days. Review Billing to choose the right plan.",
                    priority="medium", read=False, related_entity_type="business_subscription",
                    related_entity_id=subscription.id,
                ))

    if subscription.status in {"active", "trialing"} and subscription.current_period_end <= instant:
        if subscription.cancel_at_period_end:
            previous = subscription.status
            subscription.status = "canceled"
            subscription.ended_at = subscription.current_period_end
            key = f"canceled:{subscription.id}:{subscription.current_period_end.isoformat()}"
            if not await _event_exists(session, key):
                record_subscription_event(
                    session, subscription=subscription, event_type="subscription_canceled",
                    idempotency_key=key, from_status=previous,
                    reason="Cancellation took effect at period end.", now=instant,
                )
                session.add(Notification(
                    business_id=business_id, recipient_user_id=None, category="billing_subscription",
                    title="Subscription canceled",
                    message="The workspace now follows the explicit Free baseline. No workspace data was deleted.",
                    priority="high", read=False, related_entity_type="business_subscription",
                    related_entity_id=subscription.id,
                ))
        else:
            old_start = subscription.current_period_start
            while subscription.current_period_end <= instant:
                subscription.current_period_start = subscription.current_period_end
                subscription.current_period_end = add_billing_period(
                    subscription.current_period_end, subscription.billing_interval  # type: ignore[arg-type]
                )
            key = f"period-rollover:{subscription.id}:{subscription.current_period_start.isoformat()}"
            if not await _event_exists(session, key):
                record_subscription_event(
                    session, subscription=subscription, event_type="period_rolled_over",
                    idempotency_key=key, from_status=subscription.status,
                    reason=f"Calendar billing period advanced from {old_start.isoformat()}.", now=instant,
                )

    if subscription.status in {"active", "trialing"}:
        snapshot = await resolve_entitlements(session, business_id=business_id, now=instant)
        usage = await current_usage(session, business_id=business_id, snapshot=snapshot, now=instant)
        for entitlement_key in sorted(USAGE_ENTITLEMENTS):
            limit = usage.limits.get(entitlement_key, 0)
            consumed = usage.usage.get(entitlement_key, 0)
            if limit <= 0 or consumed * 100 < limit * 80:
                continue
            threshold = 100 if consumed >= limit else 80
            key = f"usage-threshold:{subscription.id}:{usage.period_start.isoformat()}:{entitlement_key}:{threshold}"
            if await _event_exists(session, key):
                continue
            record_subscription_event(
                session, subscription=subscription, event_type="usage_threshold_notification",
                idempotency_key=key, from_status=subscription.status,
                reason=f"{entitlement_key} reached {threshold}% threshold.", now=instant,
            )
            session.add(Notification(
                business_id=business_id, recipient_user_id=None, category="billing_usage",
                title="Plan usage needs attention",
                message=f"{entitlement_key} has reached {threshold}% of its current-period limit.",
                priority="high" if threshold == 100 else "medium", read=False,
                related_entity_type="business_subscription", related_entity_id=subscription.id,
            ))
    return subscription
