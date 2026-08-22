from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.business_brain import BusinessBrainSourceType
from app.schemas.business_memory import BusinessMemoryType


AIAgentRole = Literal[
    "business_manager",
    "cmo",
    "sales",
    "support",
    "operations",
    "analytics",
]

AIAgentRiskLevel = Literal[
    "low",
    "medium",
    "high",
    "critical",
]

AIAgentExecutionStatus = Literal[
    "completed",
    "needs_approval",
    "blocked",
]

MAX_AGENT_TASK_LENGTH = 4_000
MAX_AGENT_SUMMARY_LENGTH = 12_000
MAX_AGENT_ITEM_LENGTH = 2_000
MAX_AGENT_ACTION_TYPE_LENGTH = 100
MAX_AGENT_ACTIONS = 20
MAX_AGENT_RECOMMENDATIONS = 20

DEFAULT_AGENT_MEMORY_LIMIT = 50
MAX_AGENT_MEMORY_LIMIT = 200

DEFAULT_AGENT_BRAIN_SOURCE_LIMIT = 200
MAX_AGENT_BRAIN_SOURCE_LIMIT = 1_000


def _trim_string(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _normalize_string_list(
    values: list[str],
    *,
    field_name: str,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for value in values:
        if not isinstance(value, str):
            raise ValueError(
                f"{field_name} must contain only strings"
            )

        item = value.strip()

        if not item:
            raise ValueError(
                f"{field_name} cannot contain blank values"
            )

        if len(item) > MAX_AGENT_ITEM_LENGTH:
            raise ValueError(
                f"{field_name} item exceeds maximum length"
            )

        if item in seen:
            raise ValueError(
                f"{field_name} cannot contain duplicate values"
            )

        seen.add(item)
        normalized.append(item)

    return normalized


class AIAgentExecutionRequest(BaseModel):
    """
    Internal request to execute one AI employee task.

    This is intentionally provider-independent. API keys, model names,
    provider payloads, temperature, token limits, and other vendor-specific
    configuration do not belong in this runtime contract.
    """

    role: AIAgentRole

    task: str = Field(
        min_length=1,
        max_length=MAX_AGENT_TASK_LENGTH,
    )

    include_business_brain: bool = True
    include_memory: bool = True

    brain_source_types: list[BusinessBrainSourceType] | None = None
    memory_types: list[BusinessMemoryType] | None = None

    brain_source_limit: int = Field(
        default=DEFAULT_AGENT_BRAIN_SOURCE_LIMIT,
        ge=1,
        le=MAX_AGENT_BRAIN_SOURCE_LIMIT,
    )

    memory_limit: int = Field(
        default=DEFAULT_AGENT_MEMORY_LIMIT,
        ge=1,
        le=MAX_AGENT_MEMORY_LIMIT,
    )

    min_memory_importance: int = Field(
        default=1,
        ge=1,
        le=5,
    )

    min_memory_confidence: Decimal = Field(
        default=Decimal("0.000"),
        ge=Decimal("0.000"),
        le=Decimal("1.000"),
        decimal_places=3,
    )

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator("task", mode="before")
    @classmethod
    def trim_task(cls, value: Any) -> Any:
        return _trim_string(value)

    @field_validator("brain_source_types")
    @classmethod
    def reject_duplicate_brain_source_types(
        cls,
        value: list[BusinessBrainSourceType] | None,
    ) -> list[BusinessBrainSourceType] | None:
        if value is None:
            return None

        if len(value) != len(set(value)):
            raise ValueError(
                "brain_source_types cannot contain duplicate values"
            )

        return value

    @field_validator("memory_types")
    @classmethod
    def reject_duplicate_memory_types(
        cls,
        value: list[BusinessMemoryType] | None,
    ) -> list[BusinessMemoryType] | None:
        if value is None:
            return None

        if len(value) != len(set(value)):
            raise ValueError(
                "memory_types cannot contain duplicate values"
            )

        return value

    @model_validator(mode="after")
    def validate_context_configuration(
        self,
    ) -> "AIAgentExecutionRequest":
        if (
            not self.include_business_brain
            and not self.include_memory
        ):
            raise ValueError(
                "At least one trusted context source must be enabled"
            )

        if (
            not self.include_business_brain
            and self.brain_source_types is not None
        ):
            raise ValueError(
                "brain_source_types cannot be supplied when "
                "include_business_brain is false"
            )

        if (
            not self.include_memory
            and self.memory_types is not None
        ):
            raise ValueError(
                "memory_types cannot be supplied when "
                "include_memory is false"
            )

        return self


class AIAgentProposedAction(BaseModel):
    """
    A non-executing action proposal produced by an AI employee.

    Producing this object never performs the action. Actual side effects will
    later pass through the dedicated approval/action execution layer.
    """

    action_type: str = Field(
        min_length=1,
        max_length=MAX_AGENT_ACTION_TYPE_LENGTH,
    )

    description: str = Field(
        min_length=1,
        max_length=MAX_AGENT_ITEM_LENGTH,
    )

    risk_level: AIAgentRiskLevel = "medium"

    requires_approval: bool = True

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator(
        "action_type",
        "description",
        mode="before",
    )
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return _trim_string(value)

    @model_validator(mode="after")
    def critical_actions_require_approval(
        self,
    ) -> "AIAgentProposedAction":
        if (
            self.risk_level == "critical"
            and not self.requires_approval
        ):
            raise ValueError(
                "Critical actions must require approval"
            )

        return self


class AIAgentStructuredOutput(BaseModel):
    """
    Provider-neutral structured result produced by an AI employee.

    This contract deliberately excludes hidden chain-of-thought. The runtime
    stores only user-visible conclusions, recommendations, and proposed actions.
    """

    status: AIAgentExecutionStatus

    summary: str = Field(
        min_length=1,
        max_length=MAX_AGENT_SUMMARY_LENGTH,
    )

    recommendations: list[str] = Field(
        default_factory=list,
        max_length=MAX_AGENT_RECOMMENDATIONS,
    )

    proposed_actions: list[AIAgentProposedAction] = Field(
        default_factory=list,
        max_length=MAX_AGENT_ACTIONS,
    )

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator("summary", mode="before")
    @classmethod
    def trim_summary(cls, value: Any) -> Any:
        return _trim_string(value)

    @field_validator("recommendations")
    @classmethod
    def normalize_recommendations(
        cls,
        value: list[str],
    ) -> list[str]:
        return _normalize_string_list(
            value,
            field_name="recommendations",
        )

    @model_validator(mode="after")
    def validate_execution_status(
        self,
    ) -> "AIAgentStructuredOutput":
        approval_required = any(
            action.requires_approval
            for action in self.proposed_actions
        )

        if (
            approval_required
            and self.status == "completed"
        ):
            raise ValueError(
                "An output containing approval-required actions "
                "cannot have completed status"
            )

        if (
            self.status == "needs_approval"
            and not approval_required
        ):
            raise ValueError(
                "needs_approval status requires at least one "
                "approval-required action"
            )

        return self


class AIAgentExecutionResult(BaseModel):
    """
    Safe runtime result for one AI employee execution.

    Context content itself is not duplicated into this response. The context
    revision and counts provide traceability without unnecessarily copying
    authoritative business data.
    """

    business_id: UUID
    role: AIAgentRole

    context_revision: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )

    context_source_count: int = Field(
        ge=0,
    )

    business_brain_source_count: int = Field(
        ge=0,
    )

    memory_source_count: int = Field(
        ge=0,
    )

    output: AIAgentStructuredOutput

    model_config = ConfigDict(
        extra="forbid",
    )

    @model_validator(mode="after")
    def validate_context_counts(
        self,
    ) -> "AIAgentExecutionResult":
        if (
            self.business_brain_source_count
            + self.memory_source_count
            != self.context_source_count
        ):
            raise ValueError(
                "Context source counts are inconsistent"
            )

        return self