from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

BusinessMemoryType = Literal[
    "episodic",
    "semantic",
    "procedural",
    "decision",
    "customer",
    "ai_learning",
]

BusinessMemoryStatus = Literal[
    "active",
    "superseded",
    "archived",
]

BusinessMemorySourceType = Literal[
    "manual",
    "system",
]

MAX_MEMORY_CONTENT_LENGTH = 10_000
MAX_MEMORY_SOURCE_REFERENCE_LENGTH = 1024
DEFAULT_MEMORY_IMPORTANCE = 3
DEFAULT_MANUAL_MEMORY_CONFIDENCE = Decimal("1.000")


def _trim_string(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


class BusinessMemoryCreate(BaseModel):
    """Public payload for manually creating one business memory."""

    memory_type: BusinessMemoryType
    content: str = Field(
        min_length=1,
        max_length=MAX_MEMORY_CONTENT_LENGTH,
    )
    importance: int = Field(
        default=DEFAULT_MEMORY_IMPORTANCE,
        ge=1,
        le=5,
    )
    occurred_at: AwareDatetime | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("content", mode="before")
    @classmethod
    def trim_content(cls, value: Any) -> Any:
        return _trim_string(value)


class BusinessMemoryUpdate(BaseModel):
    """PATCH payload containing only fields a public client may edit."""

    memory_type: BusinessMemoryType | None = None
    content: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_MEMORY_CONTENT_LENGTH,
    )
    importance: int | None = Field(
        default=None,
        ge=1,
        le=5,
    )
    occurred_at: AwareDatetime | None = None
    status: BusinessMemoryStatus | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("content", mode="before")
    @classmethod
    def trim_content(cls, value: Any) -> Any:
        return _trim_string(value)

    @field_validator(
        "memory_type",
        "content",
        "importance",
        "status",
    )
    @classmethod
    def reject_null_required_fields(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("Field cannot be null")
        return value


class BusinessMemoryResponse(BaseModel):
    """Safe public representation of a persistent business memory."""

    id: UUID
    business_id: UUID
    memory_type: BusinessMemoryType
    content: str
    status: BusinessMemoryStatus
    importance: int = Field(ge=1, le=5)
    confidence: Decimal = Field(
        ge=Decimal("0.000"),
        le=Decimal("1.000"),
        decimal_places=3,
    )
    source_type: BusinessMemorySourceType
    occurred_at: AwareDatetime | None
    last_reinforced_at: AwareDatetime | None
    superseded_by_memory_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class BusinessMemoryPageResponse(BaseModel):
    """Bounded cursor-based page returned by the memory list endpoint."""

    items: list[BusinessMemoryResponse]
    next_cursor: str | None = None

    model_config = ConfigDict(extra="forbid")