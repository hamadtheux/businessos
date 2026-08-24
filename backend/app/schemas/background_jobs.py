from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


JobStatus = Literal["queued", "processing", "succeeded", "failed", "dead_letter", "canceled"]
JobType = Literal[
    "process_automation_event",
    "resume_workflow_run",
    "process_scheduled_workflow",
    "process_integration_event",
    "dispatch_action_execution",
    "reconcile_uncertain_attempt",
    "mark_social_schedule_ready",
    "maintain_subscription",
    "discover_competitors",
    "generate_content_plan",
    "analyze_campaign_opportunities",
]


class BackgroundJobResponse(BaseModel):
    id: UUID
    business_id: UUID
    job_type: JobType
    status: JobStatus
    priority: int
    idempotency_key: str
    attempt_count: int
    max_attempts: int
    available_at: datetime
    claimed_at: datetime | None
    lease_expires_at: datetime | None
    completed_at: datetime | None
    failure_code: str | None
    automation_event_id: UUID | None
    workflow_id: UUID | None
    workflow_run_id: UUID | None
    node_run_id: UUID | None
    integration_event_id: UUID | None
    action_execution_attempt_id: UUID | None
    social_schedule_id: UUID | None
    subscription_id: UUID | None = None
    competitor_discovery_run_id: UUID | None = None
    marketing_automation_run_id: UUID | None = None
    scheduled_occurrence_at: datetime | None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BackgroundJobPageResponse(BaseModel):
    items: list[BackgroundJobResponse]
    page: int
    page_size: int
    total: int


class JobCounts(BaseModel):
    queued: int
    processing: int
    succeeded: int
    failed: int
    dead_letter: int
    canceled: int


class ProcessingHealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unavailable"]
    counts: JobCounts
    automation_event_backlog: int
    oldest_queued_job_age_seconds: float | None
    average_processing_latency_seconds: float | None
    worker_last_heartbeat_at: datetime | None
    scheduler_last_heartbeat_at: datetime | None
