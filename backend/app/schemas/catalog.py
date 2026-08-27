from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, field_validator

CatalogItemType = Literal["product", "service"]
CatalogItemStatus = Literal["active", "draft", "archived"]
MAX_DESCRIPTION_LENGTH = 10_000
MAX_CATALOG_PRICE = Decimal("999999999999.99")


def _trim_string(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _normalize_sku(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip().upper()
    return normalized or None


def _reject_url_credentials(value: HttpUrl | None) -> HttpUrl | None:
    if value is not None and (value.username or value.password):
        raise ValueError("product_url must not contain credentials")
    return value


class _CatalogItemFields(BaseModel):
    item_type: CatalogItemType
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    sku: str | None = Field(default=None, max_length=100)
    price: Decimal | None = Field(
        default=None,
        ge=Decimal(0),
        le=MAX_CATALOG_PRICE,
        max_digits=14,
        decimal_places=2,
    )
    compare_at_price: Decimal | None = Field(default=None, ge=0, le=MAX_CATALOG_PRICE, max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    cost: Decimal | None = Field(default=None, ge=0, le=MAX_CATALOG_PRICE, max_digits=14, decimal_places=2)
    product_url: HttpUrl | None = None
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
    status: CatalogItemStatus = "active"

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "description", "brand", "vendor", "gtin", "mpn", "google_product_category", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim_string(value)

    @field_validator("sku", mode="before")
    @classmethod
    def normalize_sku(cls, value: Any) -> Any:
        return _normalize_sku(value)

    @field_validator("product_url")
    @classmethod
    def safe_product_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        return _reject_url_credentials(value)


class CatalogItemCreate(_CatalogItemFields):
    pass


class CatalogItemUpdate(BaseModel):
    item_type: CatalogItemType | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    sku: str | None = Field(default=None, max_length=100)
    price: Decimal | None = Field(
        default=None,
        ge=Decimal(0),
        le=MAX_CATALOG_PRICE,
        max_digits=14,
        decimal_places=2,
    )
    compare_at_price: Decimal | None = Field(default=None, ge=0, le=MAX_CATALOG_PRICE, max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    cost: Decimal | None = Field(default=None, ge=0, le=MAX_CATALOG_PRICE, max_digits=14, decimal_places=2)
    product_url: HttpUrl | None = None
    inventory_quantity: int | None = Field(default=None, ge=0, le=2_147_483_647)
    availability: Literal["unknown", "in_stock", "out_of_stock", "preorder", "backorder"] | None = None
    brand: str | None = Field(default=None, max_length=160)
    vendor: str | None = Field(default=None, max_length=160)
    gtin: str | None = Field(default=None, max_length=32)
    mpn: str | None = Field(default=None, max_length=100)
    condition: Literal["new", "refurbished", "used"] | None = None
    google_product_category: str | None = Field(default=None, max_length=255)
    tags: list[str] | None = Field(default=None, max_length=100)
    published: bool | None = None
    status: CatalogItemStatus | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "description", "brand", "vendor", "gtin", "mpn", "google_product_category", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim_string(value)

    @field_validator("sku", mode="before")
    @classmethod
    def normalize_sku(cls, value: Any) -> Any:
        return _normalize_sku(value)

    @field_validator("product_url")
    @classmethod
    def safe_product_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        return _reject_url_credentials(value)

    @field_validator("item_type", "name", "status", "availability", "condition", "tags", "published")
    @classmethod
    def reject_null_required_fields(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("Field cannot be null")
        return value


class CatalogItemResponse(BaseModel):
    id: UUID
    business_id: UUID
    item_type: CatalogItemType
    name: str
    description: str | None
    sku: str | None
    price: Decimal | None
    compare_at_price: Decimal | None
    currency: str | None
    cost: Decimal | None
    product_url: str | None
    inventory_quantity: int | None
    availability: Literal["unknown", "in_stock", "out_of_stock", "preorder", "backorder"]
    brand: str | None
    vendor: str | None
    gtin: str | None
    mpn: str | None
    condition: Literal["new", "refurbished", "used"]
    google_product_category: str | None
    tags: list[str]
    published: bool
    source: str
    sync_state: Literal["manual", "in_sync", "pending", "local_override", "external_changed", "error"]
    last_synchronized_at: AwareDatetime | None
    provider_metadata: dict[str, object]
    status: CatalogItemStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_validator("availability", mode="before")
    @classmethod
    def default_availability(cls, value: Any) -> Any:
        return value or "unknown"

    @field_validator("condition", mode="before")
    @classmethod
    def default_condition(cls, value: Any) -> Any:
        return value or "new"

    @field_validator("tags", mode="before")
    @classmethod
    def default_tags(cls, value: Any) -> Any:
        return value or []

    @field_validator("published", mode="before")
    @classmethod
    def default_published(cls, value: Any) -> Any:
        return True if value is None else value

    @field_validator("source", mode="before")
    @classmethod
    def default_source(cls, value: Any) -> Any:
        return value or "manual"

    @field_validator("sync_state", mode="before")
    @classmethod
    def default_sync_state(cls, value: Any) -> Any:
        return value or "manual"

    @field_validator("provider_metadata", mode="before")
    @classmethod
    def default_provider_metadata(cls, value: Any) -> Any:
        return value or {}


class CatalogImportFileMetadata(BaseModel):
    filename: str
    file_type: Literal["csv", "xlsx"]
    size_bytes: int

    model_config = ConfigDict(extra="forbid")


class CatalogImportRowError(BaseModel):
    row: int = Field(ge=2)
    field: str | None = None
    message: str

    model_config = ConfigDict(extra="forbid")


class CatalogImportPreviewRow(BaseModel):
    row: int = Field(ge=2)
    normalized: dict[str, str | None]
    item: CatalogItemCreate | None
    errors: list[CatalogImportRowError]

    model_config = ConfigDict(extra="forbid")


class CatalogImportPreviewResponse(BaseModel):
    file: CatalogImportFileMetadata
    detected_columns: dict[str, str]
    total_rows: int
    valid_rows: int
    invalid_rows: int
    preview_rows: list[CatalogImportPreviewRow]
    errors: list[CatalogImportRowError]
    preview_limit: int

    model_config = ConfigDict(extra="forbid")


class CatalogImportResult(BaseModel):
    created_count: int
    total_rows: int

    model_config = ConfigDict(extra="forbid")
