from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanResponse(BaseModel):
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


class BillingOverviewResponse(BaseModel):
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
    provider_configured: bool = False


class UsageResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    usage: dict[str, int]
    limits: dict[str, int]
    remaining: dict[str, int]
    informational: dict[str, int]


class PlanChangeIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_code: str = Field(min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_]*$")
    billing_interval: Literal["month", "year"]


class PlanChangeIntentResponse(BaseModel):
    status: Literal["provider_unavailable", "blocked", "checkout_ready"]
    message: str
    blockers: list[dict[str, int | str]] = []
    checkout_url: str | None = None


class CancellationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)


class SubscriptionMutationResponse(BaseModel):
    status: str
    cancel_at_period_end: bool
    current_period_end: datetime


class AdminPlanAvailabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool
    public: bool
    reason: str = Field(min_length=3, max_length=500)


class AdminPlanVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str = Field(default="USD", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    monthly_price_minor: int | None = Field(default=None, ge=0)
    yearly_price_minor: int | None = Field(default=None, ge=0)
    entitlements: dict[str, bool | int]
    reason: str = Field(min_length=3, max_length=500)


class AdminSubscriptionAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_code: str = Field(min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_]*$")
    billing_interval: Literal["month", "year"] = "month"
    trial_days: int = Field(default=0, ge=0, le=365)
    reason: str = Field(min_length=3, max_length=500)


class AdminTrialExtensionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(ge=1, le=365)
    reason: str = Field(min_length=3, max_length=500)


class AdminSubscriptionStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "suspended"]
    reason: str = Field(min_length=3, max_length=500)


class AdminEntitlementOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entitlement_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    boolean_value: bool | None = None
    integer_value: int | None = Field(default=None, ge=0)
    active: bool = True
    expires_at: datetime | None = None
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def exactly_one_value(self) -> "AdminEntitlementOverrideRequest":
        if (self.boolean_value is None) == (self.integer_value is None):
            raise ValueError("Exactly one typed entitlement value is required")
        return self


class AdminSubscriptionItem(BaseModel):
    business_id: UUID
    subscription_id: UUID
    plan_code: str
    plan_name: str
    status: str
    billing_interval: str
    current_period_end: datetime
    cancel_at_period_end: bool


class AdminSubscriptionPage(BaseModel):
    items: list[AdminSubscriptionItem]
    page: int
    page_size: int
    total: int


class AdminSubscriptionEventItem(BaseModel):
    id: UUID
    subscription_id: UUID
    business_id: UUID
    event_type: str
    actor_user_id: UUID | None
    from_status: str | None
    to_status: str | None
    from_plan_version_id: UUID | None
    to_plan_version_id: UUID | None
    reason: str | None
    created_at: datetime


class AdminSubscriptionEventPage(BaseModel):
    items: list[AdminSubscriptionEventItem]
    page: int
    page_size: int
    total: int


class AdminBillingAuditItem(BaseModel):
    id: UUID
    business_id: UUID | None
    actor_user_id: UUID | None
    event_type: str
    target_type: str
    target_id: UUID | None
    before_state: dict[str, object]
    after_state: dict[str, object]
    reason: str
    created_at: datetime


class AdminBillingAuditPage(BaseModel):
    items: list[AdminBillingAuditItem]
    page: int
    page_size: int
    total: int


class AdminBillingMetrics(BaseModel):
    subscriptions_by_status: dict[str, int]
    subscriptions_by_plan: dict[str, int]
    businesses_without_subscription: int
    note: str = "Revenue metrics are unavailable because no payment provider is configured."
