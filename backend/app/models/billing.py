from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.billing import (
    FEATURE_ENTITLEMENTS,
    LEGACY_INTEGER_ENTITLEMENT_KEYS,
    RESOURCE_ENTITLEMENTS,
    USAGE_ENTITLEMENTS,
)
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


def _sql_keys(keys: frozenset[str]) -> str:
    return "(" + ",".join(f"'{key}'" for key in sorted(keys)) + ")"


_FEATURE_KEYS_SQL = _sql_keys(FEATURE_ENTITLEMENTS)
_INTEGER_KEYS_SQL = _sql_keys(
    RESOURCE_ENTITLEMENTS | USAGE_ENTITLEMENTS | LEGACY_INTEGER_ENTITLEMENT_KEYS
)
_REGISTERED_TYPED_VALUE_SQL = (
    f"(entitlement_key IN {_FEATURE_KEYS_SQL} AND boolean_value IS NOT NULL AND integer_value IS NULL) OR "
    f"(entitlement_key IN {_INTEGER_KEYS_SQL} AND boolean_value IS NULL "
    "AND integer_value IS NOT NULL AND integer_value >= 0)"
)


class BillingPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_plans"
    __table_args__ = (
        CheckConstraint("code ~ '^[a-z][a-z0-9_]{0,47}$'", name="valid_code"),
        CheckConstraint("char_length(btrim(display_name)) BETWEEN 1 AND 100", name="valid_display_name"),
        CheckConstraint("trial_days BETWEEN 0 AND 365", name="valid_trial_days"),
        UniqueConstraint("code", name="uq_billing_plans_code"),
    )

    code: Mapped[str] = mapped_column(String(48), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class BillingPlanVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_plan_versions"
    __table_args__ = (
        UniqueConstraint("plan_id", "version", name="uq_billing_plan_versions_plan_version"),
        UniqueConstraint("id", "plan_id", name="uq_billing_plan_versions_id_plan"),
        CheckConstraint("version >= 1", name="valid_version"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="valid_currency"),
        CheckConstraint("monthly_price_minor IS NULL OR monthly_price_minor >= 0", name="valid_monthly_price"),
        CheckConstraint("yearly_price_minor IS NULL OR yearly_price_minor >= 0", name="valid_yearly_price"),
        Index("ix_billing_plan_versions_plan_active", "plan_id", "active", "version"),
    )

    plan_id: Mapped[UUID] = mapped_column(ForeignKey("billing_plans.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    monthly_price_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    yearly_price_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BillingPlanEntitlement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_plan_entitlements"
    __table_args__ = (
        UniqueConstraint("plan_version_id", "entitlement_key", name="uq_plan_entitlements_version_key"),
        CheckConstraint("entitlement_key ~ '^[a-z][a-z0-9_]{0,63}$'", name="valid_entitlement_key"),
        CheckConstraint(_REGISTERED_TYPED_VALUE_SQL, name="registered_typed_value"),
    )

    plan_version_id: Mapped[UUID] = mapped_column(ForeignKey("billing_plan_versions.id", ondelete="CASCADE"), nullable=False)
    entitlement_key: Mapped[str] = mapped_column(String(64), nullable=False)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    integer_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class BusinessSubscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "business_subscriptions"
    __table_args__ = (
        UniqueConstraint("business_id", name="uq_business_subscriptions_business"),
        ForeignKeyConstraint(
            ["plan_version_id", "plan_id"],
            ["billing_plan_versions.id", "billing_plan_versions.plan_id"],
            name="fk_business_subscriptions_version_plan",
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('trialing','active','canceled','expired','suspended')", name="valid_status"),
        CheckConstraint("billing_interval IN ('month','year')", name="valid_billing_interval"),
        CheckConstraint("source IN ('free_default','legacy_bootstrap','platform_admin','provider','billing_test_mode')", name="valid_source"),
        CheckConstraint("provider IN ('disabled')", name="valid_provider"),
        CheckConstraint("current_period_end > current_period_start", name="valid_period"),
        CheckConstraint(
            "(trial_started_at IS NULL AND trial_ends_at IS NULL) OR "
            "(trial_started_at IS NOT NULL AND trial_ends_at IS NOT NULL "
            "AND trial_ends_at >= trial_started_at)",
            name="valid_trial_period",
        ),
        CheckConstraint(
            "status <> 'trialing' OR trial_started_at IS NOT NULL",
            name="trialing_requires_period",
        ),
        Index("ix_business_subscriptions_status_period_end", "status", "current_period_end", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("billing_plans.id", ondelete="RESTRICT"), nullable=False)
    plan_version_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_interval: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="disabled", server_default="disabled")
    provider_customer_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_subscription_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BillingSubscriptionEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "billing_subscription_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_billing_subscription_events_idempotency"),
        CheckConstraint("event_type ~ '^[a-z][a-z0-9_]{0,63}$'", name="valid_event_type"),
        Index("ix_billing_subscription_events_subscription_created", "subscription_id", "created_at", "id"),
    )

    subscription_id: Mapped[UUID] = mapped_column(ForeignKey("business_subscriptions.id", ondelete="CASCADE"), nullable=False)
    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    from_plan_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("billing_plan_versions.id", ondelete="RESTRICT"), nullable=True)
    to_plan_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("billing_plan_versions.id", ondelete="RESTRICT"), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BusinessEntitlementOverride(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "business_entitlement_overrides"
    __table_args__ = (
        CheckConstraint("entitlement_key ~ '^[a-z][a-z0-9_]{0,63}$'", name="valid_entitlement_key"),
        CheckConstraint(_REGISTERED_TYPED_VALUE_SQL, name="registered_typed_value"),
        CheckConstraint("char_length(btrim(reason)) BETWEEN 3 AND 500", name="valid_reason"),
        Index("ix_business_entitlement_overrides_business_key_created", "business_id", "entitlement_key", "created_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    entitlement_key: Mapped[str] = mapped_column(String(64), nullable=False)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    integer_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BillingWebhookEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_billing_webhook_events_provider_event"),
        CheckConstraint("status IN ('received','processed','rejected','failed')", name="valid_status"),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    business_id: Mapped[UUID | None] = mapped_column(ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BillingAuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "billing_audit_events"
    __table_args__ = (
        CheckConstraint("event_type ~ '^[a-z][a-z0-9_.]{0,79}$'", name="valid_event_type"),
        CheckConstraint("char_length(btrim(reason)) BETWEEN 3 AND 500", name="valid_reason"),
        Index("ix_billing_audit_events_business_created", "business_id", "created_at", "id"),
    )

    business_id: Mapped[UUID | None] = mapped_column(ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(48), nullable=False)
    target_id: Mapped[UUID | None] = mapped_column(nullable=True)
    before_state: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    after_state: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
