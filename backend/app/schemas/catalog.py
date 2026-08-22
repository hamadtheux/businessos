from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

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
    status: CatalogItemStatus = "active"

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim_string(value)

    @field_validator("sku", mode="before")
    @classmethod
    def normalize_sku(cls, value: Any) -> Any:
        return _normalize_sku(value)


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
    status: CatalogItemStatus | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim_string(value)

    @field_validator("sku", mode="before")
    @classmethod
    def normalize_sku(cls, value: Any) -> Any:
        return _normalize_sku(value)

    @field_validator("item_type", "name", "status")
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
    status: CatalogItemStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


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
