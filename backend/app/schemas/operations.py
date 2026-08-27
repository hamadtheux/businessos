from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


SafeSlug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
SourceSlug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
CustomerStatus = Literal["active", "inactive", "archived"]
LeadStage = Literal["new", "qualified", "contacted", "viewing", "proposal", "won", "lost"]
OrderStatus = Literal["draft", "confirmed", "processing", "completed", "canceled"]
ConversationChannel = Literal["website", "whatsapp", "email", "facebook", "instagram", "manual", "other"]
ConversationStatus = Literal["open", "escalated", "resolved"]
OpportunityStatus = Literal["open", "in_progress", "won", "lost", "dismissed"]
Priority = Literal["low", "medium", "high", "urgent"]

T = TypeVar("T")
SafeMetricValue = int | float | str | dict[str, int] | list[dict[str, int | float | str]]


def _trim(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = value.strip()
    return value or None


class PageResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    model_config = ConfigDict(extra="forbid")


class CustomerCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    phone: str | None = Field(default=None, min_length=3, max_length=32)
    status: CustomerStatus = "active"
    source: SourceSlug = "manual"
    tags: list[str] = Field(default_factory=list, max_length=20)
    company: str | None = Field(default=None, min_length=1, max_length=160)
    notes: str | None = Field(default=None, max_length=4000)
    model_config = ConfigDict(extra="forbid")

    @field_validator("display_name", "first_name", "last_name", "email", "phone", "company", "notes", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim(value)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return value.casefold() if value else value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip()
            if not value or len(value) > 40:
                raise ValueError("tags must contain 1 to 40 characters")
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                result.append(value)
        return result


class CustomerUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    phone: str | None = Field(default=None, min_length=3, max_length=32)
    status: CustomerStatus | None = None
    source: SourceSlug | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    company: str | None = Field(default=None, min_length=1, max_length=160)
    notes: str | None = Field(default=None, max_length=4000)
    model_config = ConfigDict(extra="forbid")

    @field_validator("display_name", "first_name", "last_name", "email", "phone", "company", "notes", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim(value)

    @field_validator("display_name", "status", "source", "tags")
    @classmethod
    def required_when_supplied(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("field cannot be null")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return value.casefold() if value else value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str] | None) -> list[str] | None:
        return CustomerCreate.normalize_tags(values) if values is not None else None


class CustomerResponse(CustomerCreate):
    id: UUID
    business_id: UUID
    active: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class LeadCreate(BaseModel):
    customer_id: UUID | None = None
    owner_user_id: UUID | None = None
    display_name: str = Field(min_length=1, max_length=160)
    company: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    phone: str | None = Field(default=None, min_length=3, max_length=32)
    stage: LeadStage = "new"
    source: SourceSlug = "manual"
    priority: Priority = "medium"
    qualification_state: Literal["unqualified", "qualifying", "qualified", "disqualified"] = "unqualified"
    estimated_value: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"), max_digits=14, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    expected_close_date: date | None = None
    next_follow_up_at: AwareDatetime | None = None
    notes: str | None = Field(default=None, max_length=4000)
    model_config = ConfigDict(extra="forbid")

    @field_validator("display_name", "company", "email", "phone", "notes", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim(value)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return value.casefold() if value else value


class LeadUpdate(BaseModel):
    customer_id: UUID | None = None
    owner_user_id: UUID | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    company: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    phone: str | None = Field(default=None, min_length=3, max_length=32)
    source: SourceSlug | None = None
    priority: Priority | None = None
    estimated_value: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"), max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    expected_close_date: date | None = None
    next_follow_up_at: AwareDatetime | None = None
    notes: str | None = Field(default=None, max_length=4000)
    model_config = ConfigDict(extra="forbid")


class LeadStageUpdate(BaseModel):
    stage: LeadStage
    model_config = ConfigDict(extra="forbid")


class LeadQualificationUpdate(BaseModel):
    qualification_state: Literal["unqualified", "qualifying", "qualified", "disqualified"]
    model_config = ConfigDict(extra="forbid")


class LeadResponse(LeadCreate):
    id: UUID
    business_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class OrderLineCreate(BaseModel):
    catalog_item_id: UUID | None = None
    description: str = Field(min_length=1, max_length=300)
    quantity: int = Field(ge=1, le=100000)
    unit_price: Decimal = Field(ge=0, le=Decimal("999999999999.99"), max_digits=14, decimal_places=2)
    model_config = ConfigDict(extra="forbid")

    @field_validator("description", mode="before")
    @classmethod
    def trim_description(cls, value: Any) -> Any:
        return _trim(value)


class OrderCreate(BaseModel):
    customer_id: UUID
    source: SourceSlug = "manual"
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    adjustment_amount: Decimal = Field(default=Decimal("0.00"), ge=0, le=Decimal("999999999999.99"), max_digits=14, decimal_places=2)
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[OrderLineCreate] = Field(min_length=1, max_length=100)
    model_config = ConfigDict(extra="forbid")


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    model_config = ConfigDict(extra="forbid")


class OrderLineResponse(OrderLineCreate):
    id: UUID
    business_id: UUID
    order_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class OrderResponse(BaseModel):
    id: UUID
    business_id: UUID
    customer_id: UUID
    customer_display_name: str
    order_number: str
    status: OrderStatus
    source: str
    currency: str
    subtotal: Decimal
    adjustment_amount: Decimal
    total: Decimal
    notes: str | None
    lines: list[OrderLineResponse]
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(extra="forbid")


class ConversationCreate(BaseModel):
    customer_id: UUID | None = None
    channel: ConversationChannel
    external_reference: str | None = Field(default=None, min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$")
    assigned_user_id: UUID | None = None
    model_config = ConfigDict(extra="forbid")


class ConversationUpdate(BaseModel):
    status: ConversationStatus | None = None
    assigned_user_id: UUID | None = None
    model_config = ConfigDict(extra="forbid")


class MessageCreate(BaseModel):
    direction: Literal["outbound", "internal"] = "outbound"
    content: str = Field(min_length=1, max_length=10000)
    model_config = ConfigDict(extra="forbid")

    @field_validator("content", mode="before")
    @classmethod
    def trim_content(cls, value: Any) -> Any:
        return _trim(value)


class MessageResponse(BaseModel):
    id: UUID
    business_id: UUID
    conversation_id: UUID
    direction: Literal["inbound", "outbound", "internal"]
    sender_type: Literal["customer", "user", "ai", "system"]
    sender_user_id: UUID | None
    content: str
    sent_at: AwareDatetime
    external_reference: str | None
    delivery_status: Literal[
        "received", "recorded", "submitted", "sent", "delivered", "read", "failed",
    ]
    action_execution_attempt_id: UUID | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ConversationResponse(BaseModel):
    id: UUID
    business_id: UUID
    customer_id: UUID | None
    integration_connection_id: UUID | None = None
    customer_display_name: str | None
    channel: ConversationChannel
    external_reference: str | None
    status: ConversationStatus
    assigned_user_id: UUID | None
    last_activity_at: AwareDatetime
    latest_message: str | None
    unread: bool
    messages: list[MessageResponse] = Field(default_factory=list)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(extra="forbid")


class NotificationCreate(BaseModel):
    recipient_user_id: UUID | None = None
    category: SafeSlug
    title: str = Field(min_length=1, max_length=180)
    message: str = Field(min_length=1, max_length=1000)
    priority: Literal["low", "medium", "high"] = "medium"
    related_entity_type: SafeSlug | None = None
    related_entity_id: UUID | None = None
    model_config = ConfigDict(extra="forbid")


class NotificationResponse(NotificationCreate):
    id: UUID
    business_id: UUID
    read: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class OpportunityCreate(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    description: str = Field(min_length=1, max_length=3000)
    category: SafeSlug
    source: SourceSlug
    priority: Priority = "medium"
    estimated_value: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"), max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    status: OpportunityStatus = "open"
    customer_id: UUID | None = None
    lead_id: UUID | None = None
    model_config = ConfigDict(extra="forbid")


class OpportunityUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, min_length=1, max_length=3000)
    category: SafeSlug | None = None
    priority: Priority | None = None
    estimated_value: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"), max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    customer_id: UUID | None = None
    lead_id: UUID | None = None
    model_config = ConfigDict(extra="forbid")


class OpportunityStatusUpdate(BaseModel):
    status: OpportunityStatus
    model_config = ConfigDict(extra="forbid")


class OpportunityResponse(OpportunityCreate):
    id: UUID
    business_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    source_entity_type: str | None = None
    source_entity_id: UUID | None = None
    reason: str | None = None
    confidence: Decimal | None = None
    recommendation: str | None = None
    suggested_action: str | None = None
    provenance: list[dict[str, object]] | None = None
    dedupe_key: str | None = None
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AuditLogResponse(BaseModel):
    id: UUID
    business_id: UUID
    actor_user_id: UUID | None
    actor_type: Literal["user", "ai", "system"]
    event_type: str
    entity_type: str
    entity_id: UUID | None
    summary: str
    before_value: str | None
    after_value: str | None
    status: Literal["completed", "failed", "pending"]
    created_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ReportGenerateRequest(BaseModel):
    report_type: Literal["daily_operations", "sales", "customer", "scheduling", "marketing"] = "daily_operations"
    period_start: date
    period_end: date
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def valid_period(self) -> "ReportGenerateRequest":
        if self.period_end < self.period_start or (self.period_end - self.period_start).days > 366:
            raise ValueError("report period must be between 0 and 366 days")
        return self


class ReportResponse(BaseModel):
    id: UUID
    business_id: UUID
    report_type: Literal["daily_operations", "sales", "customer", "scheduling", "marketing"]
    period_start: date
    period_end: date
    status: Literal["ready", "failed"]
    generated_at: AwareDatetime
    summary: str
    metrics: dict[str, SafeMetricValue]
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AnalyticsPoint(BaseModel):
    label: str
    revenue: Decimal
    orders: int
    model_config = ConfigDict(extra="forbid")


class CoreAnalyticsResponse(BaseModel):
    period_start: date
    period_end: date
    customers: int
    leads: int
    crm_stage_counts: dict[str, int]
    orders: int
    order_revenue: Decimal
    average_order_value: Decimal
    appointments: int
    appointment_status_counts: dict[str, int]
    providers: int
    opportunities: int
    opportunity_status_counts: dict[str, int]
    ai_executions: int
    ai_actions: int
    revenue_series: list[AnalyticsPoint]
    lead_source_counts: dict[str, int]
    model_config = ConfigDict(extra="forbid")
