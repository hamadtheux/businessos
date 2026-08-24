from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


ApprovalStatus = Literal[
    "pending",
    "approved",
    "rejected",
    "expired",
    "canceled",
]

MAX_APPROVAL_DECISION_NOTE_LENGTH = 2_000


class ApprovalDecisionRequest(BaseModel):
    decision_note: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_APPROVAL_DECISION_NOTE_LENGTH,
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("decision_note", mode="before")
    @classmethod
    def trim_note(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ApprovalRequestResponse(BaseModel):
    id: UUID
    business_id: UUID
    action_id: UUID | None
    workflow_node_run_id: UUID | None
    requested_by_user_id: UUID | None
    status: ApprovalStatus
    reason_code: str
    requested_at: AwareDatetime
    expires_at: AwareDatetime | None
    decided_at: AwareDatetime | None
    decided_by_user_id: UUID | None
    decision_actor_id: UUID | None
    decision_note: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    target_type: Literal["ai_action", "workflow_node"] | None = None
    action: "ApprovalActionContext | None" = None
    workflow: "WorkflowApprovalContext | None" = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ApprovalRequestPageResponse(BaseModel):
    items: list[ApprovalRequestResponse]

    model_config = ConfigDict(extra="forbid")


class ApprovalActionContext(BaseModel):
    id: UUID
    action_type: str
    description: str
    risk_level: str
    status: str
    policy_decision: str | None = None
    policy_reason_code: str | None = None
    provider_channel: str
    affected_entity: str
    audience_or_recipient: str | None = None
    budget_summary: str | None = None
    payload_summary: dict[str, str | int | bool] = Field(default_factory=dict)
    model_config = ConfigDict(extra="forbid")


class WorkflowApprovalContext(BaseModel):
    node_run_id: UUID
    run_id: UUID
    workflow_id: UUID
    workflow_name: str
    node_key: UUID
    node_name: str
    run_status: str
    model_config = ConfigDict(extra="forbid")


def ensure_aware_datetime(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
