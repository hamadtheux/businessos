from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

BusinessKnowledgeCategory = Literal[
    "general",
    "faq",
    "policy",
    "procedure",
    "brand",
    "sales",
    "support",
    "operations",
    "marketing",
]
BusinessKnowledgeStatus = Literal["active", "draft", "archived"]
BusinessKnowledgeSourceType = Literal["manual", "system"]
BusinessBrainSourceType = Literal[
    "business_profile",
    "branding",
    "catalog_item",
    "knowledge_entry",
]
MAX_KNOWLEDGE_CONTENT_LENGTH = 50_000
MAX_SOURCE_REFERENCE_LENGTH = 1024


def _trim_string(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


class BusinessKnowledgeEntryCreate(BaseModel):
    category: BusinessKnowledgeCategory
    title: str = Field(min_length=1, max_length=250)
    content: str = Field(min_length=1, max_length=MAX_KNOWLEDGE_CONTENT_LENGTH)
    status: BusinessKnowledgeStatus = "active"

    model_config = ConfigDict(extra="forbid")

    @field_validator("title", "content", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim_string(value)


class BusinessKnowledgeEntryUpdate(BaseModel):
    category: BusinessKnowledgeCategory | None = None
    title: str | None = Field(default=None, min_length=1, max_length=250)
    content: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_KNOWLEDGE_CONTENT_LENGTH,
    )
    status: BusinessKnowledgeStatus | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("title", "content", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim_string(value)

    @field_validator("category", "title", "content", "status")
    @classmethod
    def reject_null_fields(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("Field cannot be null")
        return value


class BusinessKnowledgeEntryResponse(BaseModel):
    id: UUID
    business_id: UUID
    category: BusinessKnowledgeCategory
    title: str
    content: str
    status: BusinessKnowledgeStatus
    source_type: BusinessKnowledgeSourceType
    source_reference: str | None = Field(max_length=MAX_SOURCE_REFERENCE_LENGTH)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class BusinessBrainManifestResponse(BaseModel):
    business_id: UUID
    source_count: int = Field(ge=0)
    source_counts_by_type: dict[BusinessBrainSourceType, int]
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(from_attributes=True, extra="forbid")
