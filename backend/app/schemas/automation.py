from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator


WorkflowStatus = Literal["draft", "active", "paused", "archived"]
NodeType = Literal["trigger", "condition", "branch", "action", "delay", "approval", "ai", "internal_operation", "end"]
RunStatus = Literal["queued", "running", "waiting", "succeeded", "failed", "canceled"]


class ScheduleDefinition(BaseModel):
    frequency: Literal["one_time", "daily", "weekday", "weekly", "monthly"]
    at_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    at: AwareDatetime | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_frequency(self) -> "ScheduleDefinition":
        if self.frequency == "one_time":
            if self.at is None or any(value is not None for value in (self.at_time, self.weekday, self.day_of_month)):
                raise ValueError("one_time schedules require only at")
        elif self.frequency in {"daily", "weekday"}:
            if self.at_time is None or any(value is not None for value in (self.at, self.weekday, self.day_of_month)):
                raise ValueError("daily schedules require only at_time")
        elif self.frequency == "weekly":
            if self.at_time is None or self.weekday is None or self.at is not None or self.day_of_month is not None:
                raise ValueError("weekly schedules require at_time and weekday")
        elif self.frequency == "monthly":
            if self.at_time is None or self.day_of_month is None or self.at is not None or self.weekday is not None:
                raise ValueError("monthly schedules require at_time and day_of_month")
        return self


class TriggerNodeConfig(BaseModel):
    kind: Literal["trigger"] = "trigger"
    trigger_type: str = Field(min_length=1, max_length=64)
    model_config = ConfigDict(extra="forbid")


class ConditionExpression(BaseModel):
    field: str = Field(min_length=1, max_length=80)
    operator: Literal["equals", "not_equals", "contains", "gt", "gte", "lt", "lte", "date_before", "date_after"]
    value: str | int | float | Decimal | bool | list[str]
    model_config = ConfigDict(extra="forbid")


class ConditionNodeConfig(BaseModel):
    kind: Literal["condition"] = "condition"
    condition: ConditionExpression
    model_config = ConfigDict(extra="forbid")


class BranchNodeConfig(BaseModel):
    kind: Literal["branch"] = "branch"
    condition: ConditionExpression
    true_label: str = Field(default="true", min_length=1, max_length=64)
    false_label: str = Field(default="false", min_length=1, max_length=64)
    model_config = ConfigDict(extra="forbid")


class ExternalActionNodeConfig(BaseModel):
    kind: Literal["action"] = "action"
    action_type: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    risk_level: Literal["low", "medium", "high", "critical"] = "medium"
    requires_approval: bool = True
    model_config = ConfigDict(extra="forbid")


class DelayNodeConfig(BaseModel):
    kind: Literal["delay"] = "delay"
    mode: Literal["duration", "until", "context_datetime"] = "duration"
    seconds: int | None = Field(default=None, ge=60, le=2_592_000)
    until: AwareDatetime | None = None
    context_field: Literal["appointment.starts_at"] | None = None
    offset_seconds: int = Field(default=0, ge=-2_592_000, le=2_592_000)
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_mode(self) -> "DelayNodeConfig":
        required = {"duration": self.seconds, "until": self.until, "context_datetime": self.context_field}[self.mode]
        if required is None:
            raise ValueError("delay mode is incomplete")
        return self


class ApprovalNodeConfig(BaseModel):
    kind: Literal["approval"] = "approval"
    reason_code: str = Field(default="workflow_review_required", min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    expires_in_seconds: int | None = Field(default=None, ge=300, le=2_592_000)
    model_config = ConfigDict(extra="forbid")


class AINodeConfig(BaseModel):
    kind: Literal["ai"] = "ai"
    role: Literal["business_manager", "cmo", "sales", "support", "operations", "analytics"]
    task: str = Field(min_length=1, max_length=2000)
    allow_action_proposals: bool = False
    model_config = ConfigDict(extra="forbid")


class InternalOperationNodeConfig(BaseModel):
    kind: Literal["internal_operation"] = "internal_operation"
    operation: Literal["create_notification", "create_opportunity", "update_lead_stage", "add_customer_tag", "generate_report", "set_campaign_status"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=1, ge=1, le=3)
    retry_delay_seconds: int = Field(default=60, ge=60, le=3600)
    model_config = ConfigDict(extra="forbid")


class EndNodeConfig(BaseModel):
    kind: Literal["end"] = "end"
    outcome: Literal["success", "failure"] = "success"
    model_config = ConfigDict(extra="forbid")


NodeConfiguration = Annotated[
    TriggerNodeConfig | ConditionNodeConfig | BranchNodeConfig | ExternalActionNodeConfig |
    DelayNodeConfig | ApprovalNodeConfig | AINodeConfig | InternalOperationNodeConfig | EndNodeConfig,
    Field(discriminator="kind"),
]
NODE_CONFIGURATION_ADAPTER = TypeAdapter(NodeConfiguration)


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=2000)
    trigger_type: str = Field(default="manual_test", min_length=1, max_length=64)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    schedule_definition: ScheduleDefinition | None = None
    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=2000)
    trigger_type: str | None = Field(default=None, min_length=1, max_length=64)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    schedule_definition: ScheduleDefinition | None = None
    model_config = ConfigDict(extra="forbid")


class WorkflowStatusUpdate(BaseModel):
    status: Literal["active", "paused", "archived"]
    model_config = ConfigDict(extra="forbid")


class WorkflowResponse(BaseModel):
    id: UUID
    business_id: UUID
    name: str
    description: str | None
    status: WorkflowStatus
    current_version: int
    trigger_type: str
    enabled: bool
    timezone: str
    schedule_definition: dict[str, Any]
    next_run_at: AwareDatetime | None
    created_by_user_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    last_run_status: RunStatus | None = None
    last_run_at: AwareDatetime | None = None
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class NodeCreate(BaseModel):
    node_key: UUID | None = None
    node_type: NodeType
    name: str = Field(min_length=1, max_length=180)
    configuration: dict[str, Any]
    position_x: int = Field(default=0, ge=-100000, le=100000)
    position_y: int = Field(default=0, ge=-100000, le=100000)
    order_index: int = Field(default=0, ge=0, le=10000)
    model_config = ConfigDict(extra="forbid")


class NodeUpdate(BaseModel):
    node_type: NodeType | None = None
    name: str | None = Field(default=None, min_length=1, max_length=180)
    configuration: dict[str, Any] | None = None
    position_x: int | None = Field(default=None, ge=-100000, le=100000)
    position_y: int | None = Field(default=None, ge=-100000, le=100000)
    order_index: int | None = Field(default=None, ge=0, le=10000)
    model_config = ConfigDict(extra="forbid")


class NodeResponse(BaseModel):
    id: UUID
    node_key: UUID
    node_type: NodeType
    name: str
    configuration: dict[str, Any]
    position_x: int
    position_y: int
    order_index: int
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class EdgeCreate(BaseModel):
    source_node_key: UUID
    target_node_key: UUID
    branch_label: str | None = Field(default=None, min_length=1, max_length=64)
    order_index: int = Field(default=0, ge=0, le=10000)
    model_config = ConfigDict(extra="forbid")


class EdgeUpdate(BaseModel):
    target_node_key: UUID | None = None
    branch_label: str | None = Field(default=None, min_length=1, max_length=64)
    order_index: int | None = Field(default=None, ge=0, le=10000)
    model_config = ConfigDict(extra="forbid")


class EdgeResponse(BaseModel):
    id: UUID
    edge_key: UUID
    source_node_key: UUID
    target_node_key: UUID
    branch_label: str | None
    order_index: int
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class WorkflowDetailResponse(WorkflowResponse):
    version_id: UUID
    nodes: list[NodeResponse]
    edges: list[EdgeResponse]


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    model_config = ConfigDict(extra="forbid")


class WorkflowPageResponse(PageMeta):
    items: list[WorkflowResponse]


class GraphValidationResponse(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


class EventTestContext(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    entity_type: str = Field(min_length=1, max_length=64)
    entity_id: UUID | None = None
    model_config = ConfigDict(extra="forbid")


class CustomerTestContext(BaseModel):
    status: str | None = Field(default=None, max_length=64)
    source: str | None = Field(default=None, max_length=64)
    tags: list[str] | None = Field(default=None, max_length=50)
    model_config = ConfigDict(extra="forbid")


class LeadTestContext(BaseModel):
    stage: str | None = Field(default=None, max_length=64)
    priority: str | None = Field(default=None, max_length=64)
    qualification_state: str | None = Field(default=None, max_length=64)
    estimated_value: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"))
    model_config = ConfigDict(extra="forbid")


class OrderTestContext(BaseModel):
    status: str | None = Field(default=None, max_length=64)
    total: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"))
    model_config = ConfigDict(extra="forbid")


class AppointmentTestContext(BaseModel):
    provider_id: UUID | None = None
    appointment_type_id: UUID | None = None
    status: str | None = Field(default=None, max_length=64)
    starts_at: AwareDatetime | None = None
    model_config = ConfigDict(extra="forbid")


class CampaignTestContext(BaseModel):
    status: str | None = Field(default=None, max_length=64)
    objective: str | None = Field(default=None, max_length=1000)
    model_config = ConfigDict(extra="forbid")


class ConversationTestContext(BaseModel):
    channel: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=64)
    model_config = ConfigDict(extra="forbid")


class OpportunityTestContext(BaseModel):
    category: str | None = Field(default=None, max_length=64)
    priority: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=64)
    model_config = ConfigDict(extra="forbid")


class WorkflowTestPayload(BaseModel):
    event: EventTestContext | None = None
    customer: CustomerTestContext | None = None
    lead: LeadTestContext | None = None
    order: OrderTestContext | None = None
    appointment: AppointmentTestContext | None = None
    campaign: CampaignTestContext | None = None
    conversation: ConversationTestContext | None = None
    opportunity: OpportunityTestContext | None = None
    model_config = ConfigDict(extra="forbid")


class SimulationRequest(BaseModel):
    payload: WorkflowTestPayload = Field(default_factory=WorkflowTestPayload)
    run_ai: bool = False
    forced_failure_node_key: UUID | None = None
    model_config = ConfigDict(extra="forbid")

    @field_validator("payload")
    @classmethod
    def bounded_payload(cls, value: WorkflowTestPayload) -> WorkflowTestPayload:
        if len(str(value)) > 20_000:
            raise ValueError("test payload is too large")
        return value


class SimulationTraceItem(BaseModel):
    node_key: UUID
    node_type: NodeType
    name: str
    status: Literal["succeeded", "planned", "waiting", "failed"]
    branch_outcome: str | None = None
    summary: str
    model_config = ConfigDict(extra="forbid")


class SimulationResponse(BaseModel):
    valid: bool
    completed: bool
    trace: list[SimulationTraceItem]
    approvals: list[dict[str, Any]]
    delays: list[dict[str, Any]]
    planned_actions: list[dict[str, Any]]
    errors: list[str]
    model_config = ConfigDict(extra="forbid")


class ManualRunRequest(BaseModel):
    payload: WorkflowTestPayload = Field(default_factory=WorkflowTestPayload)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    model_config = ConfigDict(extra="forbid")


class WorkflowRunResponse(BaseModel):
    id: UUID
    business_id: UUID
    workflow_id: UUID
    workflow_version_id: UUID
    trigger_event_id: UUID | None
    trigger_type: Literal["event", "schedule", "manual"]
    status: RunStatus
    context_payload: dict[str, Any]
    current_node_key: UUID | None
    waiting_reason: str | None
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    failure_code: str | None
    requested_by_user_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    workflow_name: str | None = None
    version: int | None = None
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class NodeRunResponse(BaseModel):
    id: UUID
    workflow_run_id: UUID
    node_key: UUID
    status: Literal["running", "succeeded", "waiting", "failed", "skipped", "canceled"]
    attempt: int
    started_at: AwareDatetime
    completed_at: AwareDatetime | None
    branch_outcome: str | None
    result_summary: str | None
    failure_code: str | None
    resume_at: AwareDatetime | None
    action_id: UUID | None
    node_name: str | None = None
    node_type: NodeType | None = None
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class WorkflowRunPageResponse(PageMeta):
    items: list[WorkflowRunResponse]


class NodeRunPageResponse(PageMeta):
    items: list[NodeRunResponse]


class WorkflowRunDetailResponse(WorkflowRunResponse):
    node_runs: list[NodeRunResponse]


class AutomationEventResponse(BaseModel):
    id: UUID
    business_id: UUID
    event_type: str
    entity_type: str
    entity_id: UUID | None
    payload: dict[str, Any]
    occurred_at: AwareDatetime
    status: Literal["pending", "processing", "processed", "failed"]
    processed_at: AwareDatetime | None
    failure_code: str | None
    created_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AutomationEventPageResponse(PageMeta):
    items: list[AutomationEventResponse]


class EventProcessResponse(BaseModel):
    event: AutomationEventResponse
    created_run_ids: list[UUID]
    model_config = ConfigDict(extra="forbid")


class AutomationAnalyticsResponse(BaseModel):
    total_workflows: int
    active_workflows: int
    total_runs: int
    succeeded_runs: int
    failed_runs: int
    success_rate: float
    waiting_approvals: int
    average_run_duration_seconds: float | None
    node_failures: dict[str, int]
    model_config = ConfigDict(extra="forbid")
