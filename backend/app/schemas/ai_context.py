from decimal import Decimal
from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.schemas.business_brain import BusinessBrainSourceType
from app.schemas.business_memory import BusinessMemoryType


AIContextPurpose = Literal[
    "general",
    "business_manager",
    "marketing",
    "sales",
    "support",
    "operations",
    "analytics",
]

AIContextSourceOrigin = Literal[
    "business_brain",
    "business_memory",
]

MAX_AI_CONTEXT_TASK_LENGTH = 4_000

DEFAULT_BRAIN_SOURCE_LIMIT = 200
MAX_BRAIN_SOURCE_LIMIT = 1_000

DEFAULT_MEMORY_LIMIT = 50
MAX_MEMORY_LIMIT = 200

DEFAULT_MIN_MEMORY_CONFIDENCE = Decimal("0.000")
DEFAULT_MIN_MEMORY_IMPORTANCE = 1


def _trim_string(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _reject_duplicate_values(
    values: list[str] | None,
    *,
    field_name: str,
) -> list[str] | None:
    if values is None:
        return None

    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} cannot contain duplicate values")

    return values


class AIContextRequest(BaseModel):
    """
    Internal request describing the bounded trusted context needed by an AI role.

    The task is kept separate from authoritative Business Brain and Persistent
    Memory content. It describes what the caller is trying to accomplish; it is
    not itself trusted business knowledge.
    """

    purpose: AIContextPurpose = "general"

    task: str = Field(
        min_length=1,
        max_length=MAX_AI_CONTEXT_TASK_LENGTH,
    )

    include_business_brain: bool = True
    include_memory: bool = True

    brain_source_types: list[BusinessBrainSourceType] | None = None
    memory_types: list[BusinessMemoryType] | None = None

    brain_source_limit: int = Field(
        default=DEFAULT_BRAIN_SOURCE_LIMIT,
        ge=1,
        le=MAX_BRAIN_SOURCE_LIMIT,
    )

    memory_limit: int = Field(
        default=DEFAULT_MEMORY_LIMIT,
        ge=1,
        le=MAX_MEMORY_LIMIT,
    )

    min_memory_importance: int = Field(
        default=DEFAULT_MIN_MEMORY_IMPORTANCE,
        ge=1,
        le=5,
    )

    min_memory_confidence: Decimal = Field(
        default=DEFAULT_MIN_MEMORY_CONFIDENCE,
        ge=Decimal("0.000"),
        le=Decimal("1.000"),
        decimal_places=3,
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("task", mode="before")
    @classmethod
    def trim_task(cls, value: Any) -> Any:
        return _trim_string(value)

    @field_validator("brain_source_types")
    @classmethod
    def validate_brain_source_types(
        cls,
        value: list[BusinessBrainSourceType] | None,
    ) -> list[BusinessBrainSourceType] | None:
        return _reject_duplicate_values(
            value,
            field_name="brain_source_types",
        )

    @field_validator("memory_types")
    @classmethod
    def validate_memory_types(
        cls,
        value: list[BusinessMemoryType] | None,
    ) -> list[BusinessMemoryType] | None:
        return _reject_duplicate_values(
            value,
            field_name="memory_types",
        )

    @model_validator(mode="after")
    def require_at_least_one_context_source(self) -> "AIContextRequest":
        if not self.include_business_brain and not self.include_memory:
            raise ValueError(
                "At least one context source must be enabled"
            )

        if not self.include_business_brain and self.brain_source_types is not None:
            raise ValueError(
                "brain_source_types cannot be supplied when "
                "include_business_brain is false"
            )

        if not self.include_memory and self.memory_types is not None:
            raise ValueError(
                "memory_types cannot be supplied when include_memory is false"
            )

        return self


class BusinessBrainContextSource(BaseModel):
    """
    One authoritative Business Brain source included in runtime AI context.
    """

    origin: Literal["business_brain"] = "business_brain"

    business_id: UUID
    source_type: BusinessBrainSourceType

    source_id: str = Field(
        min_length=1,
        max_length=512,
    )

    title: str = Field(
        min_length=1,
        max_length=500,
    )

    content: str = Field(
        min_length=1,
    )

    updated_at: AwareDatetime

    content_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )

    model_config = ConfigDict(extra="forbid")


class BusinessMemoryContextSource(BaseModel):
    """
    One active persistent memory included in runtime AI context.

    Provenance such as raw internal source_reference is intentionally excluded
    from this context contract. Only trusted memory metadata needed for
    retrieval and reasoning is carried forward.
    """

    origin: Literal["business_memory"] = "business_memory"

    business_id: UUID
    memory_id: UUID
    memory_type: BusinessMemoryType

    content: str = Field(
        min_length=1,
    )

    importance: int = Field(
        ge=1,
        le=5,
    )

    confidence: Decimal = Field(
        ge=Decimal("0.000"),
        le=Decimal("1.000"),
        decimal_places=3,
    )

    occurred_at: AwareDatetime | None = None
    updated_at: AwareDatetime

    content_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )

    model_config = ConfigDict(extra="forbid")


AIContextSource = Annotated[
    Union[
        BusinessBrainContextSource,
        BusinessMemoryContextSource,
    ],
    Field(discriminator="origin"),
]


class AIContextBundle(BaseModel):
    """
    Immutable logical result of AI context assembly.

    `revision` is a deterministic hash of the selected authoritative sources.
    The task and purpose are execution metadata and are deliberately separate
    from trusted business source content.
    """

    business_id: UUID

    purpose: AIContextPurpose
    task: str = Field(
        min_length=1,
        max_length=MAX_AI_CONTEXT_TASK_LENGTH,
    )

    sources: list[AIContextSource]

    source_count: int = Field(
        ge=0,
    )

    business_brain_source_count: int = Field(
        ge=0,
    )

    memory_source_count: int = Field(
        ge=0,
    )

    revision: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )

    model_config = ConfigDict(
        extra="forbid",
    )

    @model_validator(mode="after")
    def validate_source_counts(self) -> "AIContextBundle":
        actual_total = len(self.sources)

        actual_brain = sum(
            source.origin == "business_brain"
            for source in self.sources
        )

        actual_memory = sum(
            source.origin == "business_memory"
            for source in self.sources
        )

        if self.source_count != actual_total:
            raise ValueError(
                "source_count does not match sources"
            )

        if self.business_brain_source_count != actual_brain:
            raise ValueError(
                "business_brain_source_count does not match sources"
            )

        if self.memory_source_count != actual_memory:
            raise ValueError(
                "memory_source_count does not match sources"
            )

        if actual_brain + actual_memory != actual_total:
            raise ValueError(
                "context source counts are inconsistent"
            )

        return self