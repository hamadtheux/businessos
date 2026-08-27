from __future__ import annotations

from decimal import Decimal
from ipaddress import ip_address
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, SecretStr, StringConstraints, field_validator, model_validator


Provider = Literal[
    "shopify", "woocommerce", "bigcommerce", "magento", "custom_api",
    "csv", "xml_feed", "google_product_feed", "website", "manual",
]
EventType = Literal[
    "product_viewed", "collection_viewed", "search_performed", "cart_created",
    "cart_updated", "checkout_started", "checkout_abandoned", "order_created",
    "order_paid", "order_fulfilled", "order_refunded", "coupon_used",
    "chat_started", "lead_captured", "campaign_clicked",
]
class CommerceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _credential_free_url(value: HttpUrl | None) -> HttpUrl | None:
    if value is not None and (value.username or value.password):
        raise ValueError("URL credentials are not permitted")
    return value


class CommerceConnectionCreate(CommerceSchema):
    provider: Provider
    display_name: str = Field(min_length=1, max_length=160)
    external_account_id: str | None = Field(default=None, min_length=1, max_length=255)
    store_url: HttpUrl | None = None
    integration_connection_id: UUID | None = None

    @field_validator("store_url")
    @classmethod
    def reject_credential_or_private_store_urls(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is None:
            return value
        if value.username or value.password:
            raise ValueError("store_url must not contain credentials")
        host = (value.host or "").rstrip(".").casefold()
        if host == "localhost" or host.endswith((".localhost", ".local")):
            raise ValueError("store_url must not target a local host")
        try:
            address = ip_address(host.strip("[]"))
        except ValueError:
            return value
        if not address.is_global:
            raise ValueError("store_url must use a globally routable address")
        return value


class CommerceConnectionConfigure(CommerceSchema):
    """Write-only provider material. Values are moved to the credential store."""

    credentials: dict[str, SecretStr] = Field(min_length=1, max_length=12)

    @field_validator("credentials")
    @classmethod
    def validate_credential_names(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        if any(
            not key or len(key) > 64 or not key.replace("_", "").isalnum()
            for key in value
        ):
            raise ValueError("credential names are invalid")
        return value


class CommerceConnectionResponse(CommerceSchema):
    id: UUID
    business_id: UUID
    integration_connection_id: UUID | None
    provider: Provider
    display_name: str
    external_account_id: str | None
    store_url: str | None
    status: Literal[
        "configuration_required", "connection_required", "connected", "syncing",
        "attention_required", "authentication_expired", "rate_limited", "failed", "disabled",
    ]
    health: Literal[
        "not_checked", "healthy", "degraded", "reauth_required", "rate_limited", "failed", "disabled",
    ]
    capabilities: list[str]
    last_sync_started_at: AwareDatetime | None
    last_sync_completed_at: AwareDatetime | None
    last_success_at: AwareDatetime | None
    failure_code: str | None
    consecutive_failures: int
    store_name: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CommerceSyncRequest(CommerceSchema):
    mode: Literal["initial", "incremental", "full", "manual_retry"] = "incremental"
    idempotency_key: str = Field(min_length=8, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]{7,254}$")


class CommerceSyncRunResponse(CommerceSchema):
    id: UUID
    business_id: UUID
    connection_id: UUID
    mode: Literal["initial", "incremental", "full", "manual_retry"]
    idempotency_key: str
    status: Literal["queued", "running", "completed", "completed_with_issues", "failed", "configuration_required"]
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    products_created: int
    products_updated: int
    products_archived: int
    variants_processed: int
    customers_created: int
    customers_updated: int
    orders_created: int
    orders_updated: int
    refunds_processed: int
    fulfillments_processed: int
    pages_processed: int
    warnings: int
    failures: int
    failure_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CommerceSyncIssueResponse(CommerceSchema):
    id: UUID
    business_id: UUID
    sync_run_id: UUID
    external_object_id: str | None
    severity: Literal["warning", "error"]
    code: str
    message: str
    created_at: AwareDatetime
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CommerceWebhookReceiptResponse(CommerceSchema):
    id: UUID
    external_event_id: str
    status: Literal["received", "queued", "reconciled", "failed", "duplicate"]
    duplicate: bool = False
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CommerceImportMapping(CommerceSchema):
    fields: dict[str, str] = Field(default_factory=dict, max_length=40)

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {
            "external_object_id", "name", "description", "sku", "product_url",
            "image_url", "price", "compare_at_price", "currency", "inventory_quantity",
            "availability", "brand", "vendor", "gtin", "mpn", "condition", "category",
            "tags", "published", "updated_at",
        }
        if set(value) - allowed or any(not item or len(item) > 160 for item in value.values()):
            raise ValueError("import field mapping is invalid")
        return value


class CommerceImportFailure(CommerceSchema):
    item_number: int = Field(ge=1)
    external_object_id: str | None = None
    code: str
    message: str


class CommerceImportPreviewResponse(CommerceSchema):
    file_type: Literal["csv", "xml_feed", "google_product_feed"]
    detected_fields: list[str]
    products: list[NormalizedProduct]
    failures: list[CommerceImportFailure]
    truncated: bool


class CommerceImportResultResponse(CommerceSchema):
    sync_run_id: UUID
    status: Literal["completed", "completed_with_issues", "failed"]
    products_created: int
    products_updated: int
    products_failed: int
    failures: list[CommerceImportFailure]


class NormalizedVariant(CommerceSchema):
    external_object_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=100)
    price: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"))
    compare_at_price: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"))
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    inventory_quantity: int | None = Field(default=None, ge=0, le=2_147_483_647)
    available: bool = True
    published: bool = True
    barcode: str | None = Field(default=None, max_length=64)
    option_values: dict[str, str] = Field(default_factory=dict)

    @field_validator("option_values")
    @classmethod
    def bounded_option_values(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 50 or any(
            not key.strip() or len(key) > 80 or len(item) > 255
            for key, item in value.items()
        ):
            raise ValueError("option_values is too large")
        return value


class NormalizedMedia(CommerceSchema):
    external_object_id: str | None = Field(default=None, max_length=255)
    media_type: Literal["image", "video", "document"] = "image"
    source_url: HttpUrl
    alt_text: str | None = Field(default=None, max_length=1000)
    position: int = Field(default=0, ge=0, le=10_000)

    @field_validator("source_url")
    @classmethod
    def reject_url_credentials(cls, value: HttpUrl) -> HttpUrl:
        cleaned = _credential_free_url(value)
        if cleaned is None:
            raise ValueError("source URL is required")
        return cleaned


class NormalizedCollection(CommerceSchema):
    external_object_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    handle: str | None = Field(default=None, max_length=255)


class NormalizedInventory(CommerceSchema):
    external_variant_id: str = Field(min_length=1, max_length=255)
    quantity: int | None = Field(default=None, ge=0, le=2_147_483_647)
    availability: Literal["unknown", "in_stock", "out_of_stock", "preorder", "backorder"] = "unknown"
    location_external_id: str | None = Field(default=None, max_length=255)
    provider_updated_at: AwareDatetime | None = None


class NormalizedProduct(CommerceSchema):
    external_object_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    sku: str | None = Field(default=None, max_length=100)
    product_url: HttpUrl | None = None
    image_urls: list[HttpUrl] = Field(default_factory=list, max_length=50)
    media: list[NormalizedMedia] = Field(default_factory=list, max_length=100)
    collections: list[NormalizedCollection] = Field(default_factory=list, max_length=100)
    price: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"))
    compare_at_price: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"))
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    cost: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"))
    inventory_quantity: int | None = Field(default=None, ge=0, le=2_147_483_647)
    availability: Literal["unknown", "in_stock", "out_of_stock", "preorder", "backorder"] = "unknown"
    brand: str | None = Field(default=None, max_length=160)
    vendor: str | None = Field(default=None, max_length=160)
    gtin: str | None = Field(default=None, max_length=32)
    mpn: str | None = Field(default=None, max_length=100)
    condition: Literal["new", "refurbished", "used"] = "new"
    google_product_category: str | None = Field(default=None, max_length=255)
    tags: list[str] = Field(default_factory=list, max_length=100)
    published: bool = True
    status: Literal["active", "draft", "archived"] = "active"
    provider_updated_at: AwareDatetime | None = None
    variants: list[NormalizedVariant] = Field(default_factory=list, max_length=500)
    safe_metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)

    @field_validator("sku", mode="before")
    @classmethod
    def normalize_sku(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        value = value.strip().upper()
        return value or None

    @field_validator("product_url")
    @classmethod
    def reject_product_url_credentials(cls, value: HttpUrl | None) -> HttpUrl | None:
        return _credential_free_url(value)

    @field_validator("image_urls")
    @classmethod
    def reject_image_url_credentials(cls, value: list[HttpUrl]) -> list[HttpUrl]:
        for item in value:
            _credential_free_url(item)
        return value

    @field_validator("tags")
    @classmethod
    def bounded_tags(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(
            not item.strip() or len(item) > 80 for item in value
        ):
            raise ValueError("tags contains invalid or duplicate values")
        return value

    @field_validator("safe_metadata")
    @classmethod
    def bounded_safe_metadata(
        cls, value: dict[str, str | int | bool | None],
    ) -> dict[str, str | int | bool | None]:
        if (
            len(value) > 100
            or any(not key.strip() or len(key) > 80 for key in value)
            or len(json.dumps(value, separators=(",", ":"))) > 16_384
        ):
            raise ValueError("safe_metadata is too large")
        return value


class NormalizedStore(CommerceSchema):
    external_account_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=160)
    public_url: HttpUrl | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    timezone: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=320)
    provider_updated_at: AwareDatetime | None = None

    @field_validator("public_url")
    @classmethod
    def reject_public_url_credentials(cls, value: HttpUrl | None) -> HttpUrl | None:
        return _credential_free_url(value)


class NormalizedAddress(CommerceSchema):
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    company: str | None = Field(default=None, max_length=160)
    address1: str | None = Field(default=None, max_length=255)
    address2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=32)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    phone: str | None = Field(default=None, max_length=32)


class NormalizedCustomer(CommerceSchema):
    external_object_id: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=160)
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=32)
    company: str | None = Field(default=None, max_length=160)
    tags: list[str] = Field(default_factory=list, max_length=20)
    address: NormalizedAddress | None = None
    accepts_marketing: bool | None = None
    provider_updated_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_provider_or_verified_identity(self) -> "NormalizedCustomer":
        if not self.external_object_id and not (self.email or self.phone):
            raise ValueError("customer identity is required")
        return self


class NormalizedOrderLine(CommerceSchema):
    external_object_id: str = Field(min_length=1, max_length=255)
    external_product_id: str | None = Field(default=None, max_length=255)
    external_variant_id: str | None = Field(default=None, max_length=255)
    sku: str | None = Field(default=None, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    quantity: int = Field(ge=1, le=100_000)
    unit_price: Decimal = Field(ge=0, le=Decimal("999999999999.99"))
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("999999999999.99"))
    tax_amount: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("999999999999.99"))


class NormalizedDiscount(CommerceSchema):
    code: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    amount: Decimal = Field(ge=0, le=Decimal("999999999999.99"))


class NormalizedTax(CommerceSchema):
    title: str = Field(min_length=1, max_length=255)
    amount: Decimal = Field(ge=0, le=Decimal("999999999999.99"))


class NormalizedShipping(CommerceSchema):
    title: str | None = Field(default=None, max_length=255)
    amount: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("999999999999.99"))
    tracking_company: str | None = Field(default=None, max_length=160)
    tracking_number: str | None = Field(default=None, max_length=255)
    tracking_url: HttpUrl | None = None

    @field_validator("tracking_url")
    @classmethod
    def reject_tracking_url_credentials(cls, value: HttpUrl | None) -> HttpUrl | None:
        return _credential_free_url(value)


class NormalizedRefundLine(CommerceSchema):
    external_order_line_id: str | None = Field(default=None, max_length=255)
    quantity: int = Field(default=1, ge=1, le=100_000)
    amount: Decimal = Field(ge=0, le=Decimal("999999999999.99"))


class NormalizedRefund(CommerceSchema):
    external_object_id: str = Field(min_length=1, max_length=255)
    amount: Decimal = Field(ge=0, le=Decimal("999999999999.99"))
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    occurred_at: AwareDatetime
    reason: str | None = Field(default=None, max_length=1000)
    lines: list[NormalizedRefundLine] = Field(default_factory=list, max_length=500)


class NormalizedFulfillment(CommerceSchema):
    external_object_id: str = Field(min_length=1, max_length=255)
    status: Literal["pending", "open", "in_progress", "fulfilled", "canceled", "failed"]
    occurred_at: AwareDatetime | None = None
    tracking_company: str | None = Field(default=None, max_length=160)
    tracking_number: str | None = Field(default=None, max_length=255)
    tracking_url: HttpUrl | None = None
    external_order_line_ids: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("tracking_url")
    @classmethod
    def reject_tracking_url_credentials(cls, value: HttpUrl | None) -> HttpUrl | None:
        return _credential_free_url(value)


class NormalizedOrder(CommerceSchema):
    external_object_id: str = Field(min_length=1, max_length=255)
    order_number: str = Field(min_length=1, max_length=40)
    external_customer_id: str | None = Field(default=None, max_length=255)
    customer: NormalizedCustomer | None = None
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    subtotal: Decimal = Field(ge=0, le=Decimal("999999999999.99"))
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("999999999999.99"))
    tax_amount: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("999999999999.99"))
    shipping_amount: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("999999999999.99"))
    total: Decimal = Field(ge=0, le=Decimal("999999999999.99"))
    payment_status: Literal["unknown", "pending", "authorized", "paid", "partially_refunded", "refunded", "voided", "failed"] = "unknown"
    fulfillment_status: Literal["unknown", "unfulfilled", "partial", "fulfilled", "canceled"] = "unknown"
    status: Literal["draft", "confirmed", "processing", "completed", "canceled"] = "confirmed"
    created_at: AwareDatetime
    updated_at: AwareDatetime | None = None
    billing_address: NormalizedAddress | None = None
    shipping_address: NormalizedAddress | None = None
    lines: list[NormalizedOrderLine] = Field(default_factory=list, max_length=1000)
    discounts: list[NormalizedDiscount] = Field(default_factory=list, max_length=100)
    taxes: list[NormalizedTax] = Field(default_factory=list, max_length=100)
    shipping: NormalizedShipping | None = None
    refunds: list[NormalizedRefund] = Field(default_factory=list, max_length=500)
    fulfillments: list[NormalizedFulfillment] = Field(default_factory=list, max_length=500)
    safe_metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)


class CommerceCursorPage(CommerceSchema):
    domain: Literal["store", "products", "customers", "orders"]
    cursor: dict[str, str | int | bool | None] = Field(default_factory=dict)
    has_more: bool = False
    complete_snapshot: bool = False


class NormalizedWebhookEvent(CommerceSchema):
    external_event_id: str = Field(min_length=1, max_length=255)
    topic: str = Field(min_length=1, max_length=100)
    external_object_id: str | None = Field(default=None, max_length=255)
    occurred_at: AwareDatetime | None = None
    reconciliation_domain: Literal["products", "customers", "orders", "inventory"]


class ProviderSyncError(CommerceSchema):
    code: Literal[
        "configuration_required", "authentication_failed", "rate_limited",
        "authorization_required", "temporary_provider_failure",
        "provider_validation_error", "provider_not_found",
        "provider_payload_incomplete", "provider_unavailable", "invalid_cursor",
        "invalid_response", "request_failed",
    ]
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False
    retry_after_seconds: int | None = Field(default=None, ge=1, le=86_400)

class CommerceEventCreate(CommerceSchema):
    event_type: EventType
    source: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
    external_event_id: str = Field(min_length=1, max_length=255)
    occurred_at: AwareDatetime
    customer_id: UUID | None = None
    customer_display_name: str | None = Field(default=None, max_length=160)
    customer_email: str | None = Field(default=None, max_length=320)
    customer_phone: str | None = Field(default=None, max_length=32)
    anonymous_session_id: str | None = Field(default=None, min_length=8, max_length=255)
    catalog_item_id: UUID | None = None
    order_id: UUID | None = None
    safe_metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_identity(self) -> "CommerceEventCreate":
        if not any((self.customer_id, self.customer_email, self.customer_phone, self.anonymous_session_id)):
            raise ValueError("a deterministic customer or anonymous session identity is required")
        if len(self.safe_metadata) > 100 or len(json.dumps(self.safe_metadata, separators=(",", ":"))) > 16_384:
            raise ValueError("safe_metadata is too large")
        return self


class CommerceEventResponse(CommerceSchema):
    id: UUID
    business_id: UUID
    customer_id: UUID | None
    catalog_item_id: UUID | None
    order_id: UUID | None
    event_type: EventType
    source: str
    external_event_id: str
    occurred_at: AwareDatetime
    safe_metadata: dict[str, object]
    created_at: AwareDatetime
    updated_at: AwareDatetime
    duplicate: bool = False
    model_config = ConfigDict(extra="forbid", from_attributes=True)


RuleField = Literal[
    "customer.status", "customer.source", "customer.tags", "order.count",
    "order.total", "order.last_at", "event.count", "event.last_at",
]
RuleOperator = Literal["equals", "not_equals", "contains", "gte", "lte", "within_days", "not_within_days"]


class AudienceRuleCondition(CommerceSchema):
    field: RuleField
    operator: RuleOperator
    value: str | int | Decimal | list[str]
    event_type: EventType | None = None
    product_id: UUID | None = None
    lookback_days: int | None = Field(default=None, ge=1, le=3650)

    @model_validator(mode="after")
    def validate_field_contract(self) -> "AudienceRuleCondition":
        text_fields = {"customer.status", "customer.source"}
        aggregate_fields = {"order.count", "order.total", "event.count"}
        last_seen_fields = {"order.last_at", "event.last_at"}
        if self.field in text_fields:
            valid = self.operator in {"equals", "not_equals"} and isinstance(self.value, str)
        elif self.field == "customer.tags":
            valid = self.operator == "contains" and isinstance(self.value, str)
        elif self.field in aggregate_fields:
            valid = (
                self.operator in {"equals", "not_equals", "gte", "lte"}
                and isinstance(self.value, (int, Decimal))
                and not isinstance(self.value, bool)
                and self.value >= 0
            )
        else:
            valid = (
                self.field in last_seen_fields
                and self.operator in {"within_days", "not_within_days"}
                and isinstance(self.value, int)
                and not isinstance(self.value, bool)
                and 1 <= self.value <= 3650
            )
        if not valid:
            raise ValueError("operator or value is incompatible with field")
        if isinstance(self.value, str) and (not self.value.strip() or len(self.value) > 255):
            raise ValueError("rule value is invalid")
        if isinstance(self.value, list) and (
            len(self.value) > 100
            or any(not item.strip() or len(item) > 80 for item in self.value)
        ):
            raise ValueError("rule value is invalid")
        if self.event_type is not None and not self.field.startswith("event."):
            raise ValueError("event_type is only valid for event rules")
        if self.product_id is not None and not self.field.startswith(("order.", "event.")):
            raise ValueError("product_id is only valid for order or event rules")
        if self.lookback_days is not None and self.field not in aggregate_fields:
            raise ValueError("lookback_days is only valid for aggregate rules")
        return self


class AudienceRule(CommerceSchema):
    all: list[AudienceRuleCondition] = Field(min_length=1, max_length=20)
    exclude: list[AudienceRuleCondition] = Field(default_factory=list, max_length=10)


class AudienceSegmentCreate(CommerceSchema):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    rule: AudienceRule
    source_classification: Literal["first_party_observed", "platform_supplied", "public_research", "ai_inference"] = "first_party_observed"


class AudienceSegmentCompileRequest(CommerceSchema):
    definition: str = Field(min_length=8, max_length=2000)
    name: str | None = Field(default=None, min_length=1, max_length=160)


class AudienceSegmentResponse(CommerceSchema):
    id: UUID
    business_id: UUID
    name: str
    description: str | None
    natural_language_definition: str | None
    rule: AudienceRule
    source_classification: Literal["first_party_observed", "platform_supplied", "public_research", "ai_inference"]
    status: Literal["draft", "active", "paused", "archived"]
    matched_customer_count: int
    last_refreshed_at: AwareDatetime | None
    created_by_user_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class AudienceExportPreflightRequest(CommerceSchema):
    provider: Literal["google", "meta"]


class AudienceExportPreflightResponse(CommerceSchema):
    segment_id: UUID
    provider: Literal["google", "meta"]
    ready: bool
    matched_member_count: int
    consented_member_count: int
    consent_registry_available: bool
    provider_acknowledgement_required: bool
    identity_handling: Literal["normalize_and_sha256_in_memory"]
    issues: list[str]


class FeedDestinationCreate(CommerceSchema):
    provider: Literal["google_merchant_center", "meta_product_catalog"]
    display_name: str = Field(min_length=1, max_length=160)
    external_account_id: str | None = Field(default=None, min_length=1, max_length=255)
    integration_connection_id: UUID | None = None
    external_resource_id: str | None = Field(default=None, min_length=1, max_length=512)
    content_language: str = Field(default="en", min_length=2, max_length=16)
    feed_label: str | None = Field(default=None, min_length=2, max_length=20)
    managed: bool = False


class FeedDestinationResponse(CommerceSchema):
    id: UUID
    business_id: UUID
    provider: Literal["google_merchant_center", "meta_product_catalog"]
    external_account_id: str | None
    integration_connection_id: UUID | None
    external_resource_id: str | None
    managed: bool
    content_language: str
    feed_label: str | None
    display_name: str
    status: Literal["configuration_required", "connection_required", "connected", "syncing", "attention_required", "disabled"]
    synchronized_count: int
    submitted_count: int
    eligible_count: int
    limited_count: int
    warning_count: int
    rejected_count: int
    last_synchronized_at: AwareDatetime | None
    failure_code: str | None
    safe_metadata: dict[str, object] = Field(default_factory=dict)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class FeedProductStatusResponse(CommerceSchema):
    id: UUID
    business_id: UUID
    destination_id: UUID
    catalog_item_id: UUID
    external_product_id: str | None
    status: Literal[
        "attention_required", "pending", "submitted", "processing", "eligible",
        "limited", "warning", "ineligible", "rejected", "error", "archived", "removed",
    ]
    missing_attributes: list[str]
    warnings: list[str]
    provider_error_code: str | None
    provider_issues: list[dict[str, object]]
    owned_by_aibos: bool
    submitted_at: AwareDatetime | None
    last_synchronized_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class FeedDestinationSyncRequest(CommerceSchema):
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]{7,199}$")
    reconcile_only: bool = False


class ProductGroupCreate(CommerceSchema):
    name: str = Field(min_length=1, max_length=160)
    external_key: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$")
    group_type: Literal[
        "manual", "category", "collection", "brand", "tag", "price", "margin",
        "best_sellers", "new_products", "promotion", "custom_rule",
    ] = "manual"
    rule: dict[str, object] = Field(default_factory=dict)
    catalog_item_ids: list[UUID] = Field(min_length=1, max_length=1000)

    @field_validator("catalog_item_ids")
    @classmethod
    def unique_catalog_items(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("catalog_item_ids cannot contain duplicates")
        return values


class ProductGroupResponse(CommerceSchema):
    id: UUID
    business_id: UUID
    name: str
    external_key: str
    group_type: str
    rule: dict[str, object]
    status: Literal["draft", "active", "archived"]
    created_by_user_id: UUID | None
    catalog_item_ids: list[UUID] = Field(default_factory=list)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ProductGroupSyncRequest(CommerceSchema):
    destination_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]{7,199}$")


class ProductGroupDestinationResponse(CommerceSchema):
    id: UUID
    business_id: UUID
    product_group_id: UUID
    destination_id: UUID
    external_reference: str | None
    status: Literal["pending", "submitted", "ready", "attention_required", "archived"]
    failure_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(extra="forbid", from_attributes=True)
