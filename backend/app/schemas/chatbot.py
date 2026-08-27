from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.domain.chatbot import normalize_allowed_hostnames


PublicCapability = Literal[
    "answer_business_questions",
    "search_products_services",
    "recommend_products_services",
    "capture_lead",
    "lookup_available_appointments",
    "book_appointment",
    "lookup_order_status",
    "request_human_handoff",
]
LocaleCode = Annotated[str, StringConstraints(pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")]


def _trim(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _optional_trim(value: Any) -> Any:
    value = _trim(value)
    return value or None


class ChatbotConfigUpdate(BaseModel):
    enabled: bool
    display_name: str = Field(min_length=1, max_length=80)
    welcome_message: str = Field(min_length=1, max_length=500)
    placeholder_text: str = Field(min_length=1, max_length=160)
    tone: Literal["friendly", "professional", "concise", "warm"] = "friendly"
    theme: Literal["light", "dark", "auto"] = "light"
    position: Literal["bottom_right", "bottom_left"] = "bottom_right"
    launcher_style: Literal["bubble", "pill"] = "bubble"
    allowed_capabilities: list[PublicCapability] = Field(max_length=8)
    allowed_domains: list[str] = Field(max_length=50)
    privacy_policy_url: str | None = Field(default=None, max_length=2048)
    consent_text: str | None = Field(default=None, max_length=1000)
    require_lead_consent: bool = False
    default_locale: LocaleCode = "en"
    border_radius: int = Field(default=18, ge=0, le=28)
    model_config = ConfigDict(extra="forbid")

    @field_validator("display_name", "welcome_message", "placeholder_text", mode="before")
    @classmethod
    def trim_required(cls, value: Any) -> Any:
        return _trim(value)

    @field_validator("privacy_policy_url", "consent_text", mode="before")
    @classmethod
    def trim_optional(cls, value: Any) -> Any:
        return _optional_trim(value)

    @field_validator("privacy_policy_url")
    @classmethod
    def safe_privacy_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("privacy_policy_url must be an absolute HTTPS URL")
        return value

    @field_validator("allowed_capabilities")
    @classmethod
    def unique_capabilities(cls, values: list[PublicCapability]) -> list[PublicCapability]:
        if len(values) != len(set(values)):
            raise ValueError("allowed_capabilities cannot contain duplicates")
        return values

    @field_validator("allowed_domains")
    @classmethod
    def normalized_domains(cls, values: list[str]) -> list[str]:
        return normalize_allowed_hostnames(values)

    @model_validator(mode="after")
    def enabled_config_is_consistent(self) -> "ChatbotConfigUpdate":
        # Hosted chat is platform-owned and does not require an external
        # website origin. Standard embeds still fail closed at the public
        # request boundary unless an exact allowed hostname is configured.
        if self.require_lead_consent and not self.consent_text:
            raise ValueError("Consent text is required when explicit lead consent is enabled")
        return self


class ChatbotConfigResponse(ChatbotConfigUpdate):
    id: UUID
    business_id: UUID
    widget_public_id: str
    available_capabilities: list[PublicCapability]
    embed_snippet: str
    ai_runtime_status: Literal["ready", "configuration_required"]
    lifecycle_status: Literal["draft", "ready", "live", "needs_ai_provider"]
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(extra="forbid")


class ChatbotDeploymentTarget(BaseModel):
    target_type: Literal[
        "hosted", "shopify", "wordpress", "wix", "webflow", "squarespace",
        "google_tag_manager", "other", "manual_embed",
    ]
    display_name: str
    state: Literal[
        "available", "connection_required", "connected", "installation_supported",
        "installed", "needs_manual_step", "unsupported",
    ]
    provider_key: str | None = None
    deployment_target_key: str | None = Field(default=None, max_length=128)
    provider_resource_reference: str | None = Field(default=None, max_length=255)
    automatic_install: bool
    hosted_url: str | None = None
    instructions: list[str] = Field(default_factory=list, max_length=10)
    verification_status: Literal["not_checked", "healthy", "failed"]
    installed_at: AwareDatetime | None = None
    last_verified_at: AwareDatetime | None = None
    failure_code: str | None = None
    model_config = ConfigDict(extra="forbid")

    @field_validator("hosted_url")
    @classmethod
    def validate_hosted_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("hosted_url must be an absolute HTTP or HTTPS URL")
        return value


class ChatbotDeploymentList(BaseModel):
    targets: list[ChatbotDeploymentTarget]
    advanced_embed_snippet: str
    ai_runtime_status: Literal["ready", "configuration_required"] = (
        "configuration_required"
    )
    assistant_status: Literal["draft", "ready", "live", "needs_ai_provider"] = (
        "draft"
    )
    model_config = ConfigDict(extra="forbid")


class PublicAppointmentType(BaseModel):
    reference: str
    name: str
    description: str | None
    duration_minutes: int
    model_config = ConfigDict(extra="forbid")


class PublicWidgetConfig(BaseModel):
    widget_id: str
    display_name: str
    business_name: str
    welcome_message: str
    placeholder_text: str
    primary_color: str
    logo_url: str | None
    tone: str
    theme: str
    position: str
    launcher_style: str
    border_radius: int
    locale: LocaleCode
    capabilities: list[PublicCapability]
    privacy_policy_url: str | None
    consent_text: str | None
    require_lead_consent: bool
    appointment_types: list[PublicAppointmentType] = Field(default_factory=list, max_length=50)
    model_config = ConfigDict(extra="forbid")


class PublicSessionResponse(BaseModel):
    session_token: str = Field(min_length=48, max_length=128)
    expires_at: AwareDatetime
    locale: LocaleCode
    model_config = ConfigDict(extra="forbid")


class PublicChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    model_config = ConfigDict(extra="forbid")

    @field_validator("message", mode="before")
    @classmethod
    def safe_message(cls, value: Any) -> Any:
        value = _trim(value)
        if isinstance(value, str) and any(
            ord(character) < 32 and character not in {"\n", "\r", "\t"}
            for character in value
        ):
            raise ValueError("message contains unsupported control characters")
        return value


class PublicProductCard(BaseModel):
    reference: str
    item_type: Literal["product", "service"]
    name: str
    description: str | None
    price: Decimal | None
    currency: str
    availability: Literal["unknown", "in_stock", "out_of_stock", "preorder", "backorder"] = "unknown"
    product_url: str | None = None
    model_config = ConfigDict(extra="forbid")


class PublicChatMessageResponse(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)
    suggested_actions: list[PublicCapability] = Field(default_factory=list, max_length=8)
    products: list[PublicProductCard] = Field(default_factory=list, max_length=5)
    handoff_status: Literal["none", "requested"] = "none"
    lead_capture_requested: bool = False
    model_config = ConfigDict(extra="forbid")


class PublicLeadCaptureRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    phone: str | None = Field(default=None, min_length=3, max_length=32)
    message: str | None = Field(default=None, max_length=1000)
    consent: bool = False
    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "email", "phone", "message", mode="before")
    @classmethod
    def trim_fields(cls, value: Any) -> Any:
        return _optional_trim(value)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("email is invalid")
        return normalized

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        digits = "".join(character for character in value if character.isdigit())
        if not 7 <= len(digits) <= 15:
            raise ValueError("phone is invalid")
        return digits

    @model_validator(mode="after")
    def contact_method_required(self) -> "PublicLeadCaptureRequest":
        if not self.email and not self.phone:
            raise ValueError("An email or phone number is required")
        return self


class PublicLeadCaptureResponse(BaseModel):
    captured: Literal[True] = True
    message: str
    model_config = ConfigDict(extra="forbid")


class PublicHandoffRequest(BaseModel):
    reason: Literal["visitor_requested", "information_unavailable", "sensitive_request", "other"] = "visitor_requested"
    model_config = ConfigDict(extra="forbid")


class PublicHandoffResponse(BaseModel):
    status: Literal["requested"] = "requested"
    message: str
    model_config = ConfigDict(extra="forbid")


class PublicOrderLookupRequest(BaseModel):
    order_reference: str = Field(min_length=1, max_length=40)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    phone: str | None = Field(default=None, min_length=3, max_length=32)
    model_config = ConfigDict(extra="forbid")

    @field_validator("order_reference", mode="before")
    @classmethod
    def trim_reference(cls, value: Any) -> Any:
        return _trim(value)

    @field_validator("email")
    @classmethod
    def normalize_lookup_email(cls, value: str | None) -> str | None:
        return PublicLeadCaptureRequest.normalize_email(_optional_trim(value))

    @field_validator("phone")
    @classmethod
    def normalize_lookup_phone(cls, value: str | None) -> str | None:
        return PublicLeadCaptureRequest.normalize_phone(_optional_trim(value))

    @model_validator(mode="after")
    def verification_required(self) -> "PublicOrderLookupRequest":
        if not self.email and not self.phone:
            raise ValueError("Email or phone verification is required")
        return self


class PublicOrderRefundFact(BaseModel):
    amount: Decimal
    currency: str
    occurred_at: AwareDatetime
    model_config = ConfigDict(extra="forbid")


class PublicOrderFulfillmentFact(BaseModel):
    status: Literal["pending", "open", "in_progress", "fulfilled", "canceled", "failed"]
    occurred_at: AwareDatetime | None
    tracking_company: str | None
    tracking_number: str | None
    tracking_url: str | None
    external_order_line_ids: list[str]
    model_config = ConfigDict(extra="forbid")


class PublicOrderStatusResponse(BaseModel):
    order_reference: str
    status: Literal["draft", "confirmed", "processing", "completed", "canceled"]
    payment_status: Literal["unknown", "pending", "authorized", "paid", "partially_refunded", "refunded", "voided", "failed"]
    fulfillment_status: Literal["unknown", "unfulfilled", "partial", "fulfilled", "canceled"]
    refunded_amount: Decimal
    refunds: list[PublicOrderRefundFact]
    fulfillments: list[PublicOrderFulfillmentFact]
    model_config = ConfigDict(extra="forbid")


class PublicAvailabilityRequest(BaseModel):
    appointment_type_reference: str = Field(min_length=16, max_length=64)
    window_start: AwareDatetime
    window_end: AwareDatetime
    desired_results: int = Field(default=5, ge=1, le=10)
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def valid_window(self) -> "PublicAvailabilityRequest":
        if self.window_end <= self.window_start or (self.window_end - self.window_start).days > 31:
            raise ValueError("availability window must be between 0 and 31 days")
        return self


class PublicAvailabilitySlot(BaseModel):
    slot_reference: str
    appointment_type_reference: str
    provider_reference: str
    provider_display_name: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    timezone: str
    location_reference: str | None
    model_config = ConfigDict(extra="forbid")


class PublicAvailabilityResponse(BaseModel):
    slots: list[PublicAvailabilitySlot] = Field(max_length=10)
    model_config = ConfigDict(extra="forbid")


class PublicAppointmentBookingRequest(BaseModel):
    slot_reference: str = Field(min_length=16, max_length=64)
    appointment_type_reference: str = Field(min_length=16, max_length=64)
    provider_reference: str = Field(min_length=16, max_length=64)
    starts_at: AwareDatetime
    name: str = Field(min_length=1, max_length=160)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    phone: str | None = Field(default=None, min_length=3, max_length=32)
    consent: bool = False
    model_config = ConfigDict(extra="forbid")

    @field_validator("name", mode="before")
    @classmethod
    def name_trimmed(cls, value: Any) -> Any:
        return _trim(value)

    @field_validator("email")
    @classmethod
    def email_normalized(cls, value: str | None) -> str | None:
        return PublicLeadCaptureRequest.normalize_email(_optional_trim(value))

    @field_validator("phone")
    @classmethod
    def phone_normalized(cls, value: str | None) -> str | None:
        return PublicLeadCaptureRequest.normalize_phone(_optional_trim(value))

    @model_validator(mode="after")
    def identity_required(self) -> "PublicAppointmentBookingRequest":
        if not self.email and not self.phone:
            raise ValueError("An email or phone number is required")
        return self


class PublicAppointmentBookingResponse(BaseModel):
    booked: Literal[True] = True
    status: Literal["confirmed"] = "confirmed"
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    provider_display_name: str
    appointment_type_name: str
    model_config = ConfigDict(extra="forbid")


class ChatbotAnalyticsResponse(BaseModel):
    period_start: date
    period_end: date
    sessions: int
    conversations: int
    messages: int
    leads_captured: int
    handoffs: int
    appointments_booked: int
    order_lookups: int
    product_recommendations: int
    ai_failures: int
    average_response_duration_ms: int | None
    model_config = ConfigDict(extra="forbid")
