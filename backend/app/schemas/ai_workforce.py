from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.ai_workforce import CommandIntent
from app.schemas.ai_agent import AIAgentRole
from app.services.ai_capabilities import CapabilityCategory


class CapabilityResponse(BaseModel):
    key: str
    category: CapabilityCategory
    description: str
    model_config = ConfigDict(extra="forbid")


class AgentMetricsResponse(BaseModel):
    execution_count: int = 0
    completed_count: int = 0
    needs_approval_count: int = 0
    failed_count: int = 0
    average_duration_ms: int | None = None
    proposed_action_count: int = 0
    pending_approval_count: int = 0
    approval_rate: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    model_config = ConfigDict(extra="forbid")


class AgentConfigResponse(BaseModel):
    id: UUID
    business_id: UUID
    role: AIAgentRole
    display_name: str
    enabled: bool
    status: Literal["active", "disabled"]
    health: Literal["ready", "not_configured"]
    autonomy_mode: Literal["manual", "supervised", "autonomous"]
    autonomy_description: str
    custom_instructions: str | None
    capabilities: list[CapabilityResponse]
    default_capabilities: list[str]
    role_description: str
    metrics: AgentMetricsResponse = Field(default_factory=AgentMetricsResponse)
    last_activity_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(extra="forbid")


class AgentConfigUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    autonomy_mode: Literal["manual", "supervised", "autonomous"] | None = None
    custom_instructions: str | None = Field(default=None, max_length=2_000)
    capabilities: list[str] | None = Field(default=None, max_length=40)
    model_config = ConfigDict(extra="forbid")

    @field_validator("display_name", "custom_instructions", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
        return value or None

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value if isinstance(item, str)]
        if len(normalized) != len(value) or any(not item for item in normalized):
            raise ValueError("Capabilities must contain non-blank strings")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Capabilities cannot contain duplicates")
        return normalized

    @model_validator(mode="after")
    def require_change(self) -> "AgentConfigUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one configuration field is required")
        return self


class CommandContextReference(BaseModel):
    type: Literal[
        "customer", "lead", "order", "conversation", "appointment_type",
        "provider", "campaign", "workflow", "report", "integration_connection",
    ]
    id: UUID
    model_config = ConfigDict(extra="forbid")


class CommandCreateRequest(BaseModel):
    command: str = Field(min_length=1, max_length=4_000)
    context_references: list[CommandContextReference] = Field(default_factory=list, max_length=10)
    trigger_source: Literal["command_center", "dashboard", "agent_detail"] = "command_center"
    model_config = ConfigDict(extra="forbid")

    @field_validator("command", mode="before")
    @classmethod
    def trim_command(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("context_references")
    @classmethod
    def unique_references(cls, value: list[CommandContextReference]) -> list[CommandContextReference]:
        keys = [(item.type, item.id) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("Context references cannot contain duplicates")
        return value


class CommandRouteResponse(BaseModel):
    primary_role: AIAgentRole
    intent: CommandIntent
    required_capabilities: list[str]
    relevant_modules: list[str]
    delegation_roles: list[AIAgentRole]
    clarification_required: bool = False
    model_config = ConfigDict(extra="forbid")


class ApprovalLinkResponse(BaseModel):
    id: UUID
    status: str
    reason_code: str
    model_config = ConfigDict(extra="forbid")


class ProposedActionResponse(BaseModel):
    id: UUID
    execution_id: UUID
    action_type: str
    description: str
    risk_level: str
    status: str
    policy_decision: str | None
    requires_approval: bool
    approval: ApprovalLinkResponse | None = None
    model_config = ConfigDict(extra="forbid")


class AgentActivityResponse(BaseModel):
    id: UUID
    business_id: UUID
    command_id: UUID | None
    parent_execution_id: UUID | None
    role: AIAgentRole
    trigger: str
    status: str
    task_summary: str
    summary: str | None
    failure_code: str | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: Decimal | None
    delegation_sequence: int
    delegation_depth: int
    proposed_actions: list[ProposedActionResponse]
    created_at: datetime
    completed_at: datetime | None
    model_config = ConfigDict(extra="forbid")


class AgentActivityPage(BaseModel):
    items: list[AgentActivityResponse]
    page: int
    page_size: int
    total: int
    model_config = ConfigDict(extra="forbid")


class CommandResponse(BaseModel):
    id: UUID
    business_id: UUID
    requested_by_user_id: UUID | None
    command: str
    status: str
    route: CommandRouteResponse
    execution_id: UUID | None
    summary: str | None
    failure_code: str | None
    executions: list[AgentActivityResponse] = Field(default_factory=list)
    proposed_actions: list[ProposedActionResponse] = Field(default_factory=list)
    created_at: datetime
    completed_at: datetime | None
    model_config = ConfigDict(extra="forbid")


class CommandPage(BaseModel):
    items: list[CommandResponse]
    page: int
    page_size: int
    total: int
    model_config = ConfigDict(extra="forbid")


class SuggestedCommandResponse(BaseModel):
    command: str
    reason: str
    role: AIAgentRole
    model_config = ConfigDict(extra="forbid")


class DailyBriefSection(BaseModel):
    key: str
    title: str
    facts: list[str]
    model_config = ConfigDict(extra="forbid")


class DailyBriefResponse(BaseModel):
    generated_at: datetime
    sections: list[DailyBriefSection]
    recommended_priorities: list[str]
    model_config = ConfigDict(extra="forbid")
