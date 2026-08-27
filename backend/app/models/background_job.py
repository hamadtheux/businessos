from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class BackgroundJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["competitor_discovery_run_id", "business_id"],
            ["competitor_discovery_runs.id", "competitor_discovery_runs.business_id"],
            name="fk_jobs_competitor_discovery_run_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["marketing_automation_run_id", "business_id"],
            ["marketing_automation_runs.id", "marketing_automation_runs.business_id"],
            name="fk_jobs_marketing_automation_run_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["commerce_sync_run_id", "business_id"],
            ["commerce_sync_runs.id", "commerce_sync_runs.business_id"],
            name="fk_jobs_commerce_sync_run_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["commerce_webhook_receipt_id", "business_id"],
            ["commerce_webhook_receipts.id", "commerce_webhook_receipts.business_id"],
            name="fk_jobs_commerce_webhook_receipt_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["commerce_feed_destination_id", "business_id"],
            ["commerce_feed_destinations.id", "commerce_feed_destinations.business_id"],
            name="fk_jobs_commerce_feed_destination_business", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["marketing_campaign_id", "business_id"],
            ["marketing_campaigns.id", "marketing_campaigns.business_id"],
            name="fk_jobs_marketing_campaign_business", ondelete="CASCADE",
        ),
        UniqueConstraint("idempotency_key", name="uq_background_jobs_idempotency_key"),
        CheckConstraint(
            "job_type IN ('process_automation_event','resume_workflow_run',"
            "'process_scheduled_workflow','process_integration_event','customer_agent_response',"
            "'dispatch_action_execution',"
            "'reconcile_uncertain_attempt','mark_social_schedule_ready',"
            "'maintain_subscription','discover_competitors',"
            "'generate_content_plan','analyze_campaign_opportunities',"
            "'commerce_initial_sync','commerce_incremental_sync','commerce_webhook_reconcile',"
            "'google_merchant_status_sync','meta_catalog_status_sync',"
            "'google_ads_performance_sync','meta_ads_performance_sync')",
            name="valid_job_type",
        ),
        CheckConstraint(
            "status IN ('queued','processing','succeeded','failed','dead_letter','canceled')",
            name="valid_status",
        ),
        CheckConstraint("priority BETWEEN 0 AND 100", name="valid_priority"),
        CheckConstraint("attempt_count BETWEEN 0 AND max_attempts", name="valid_attempt_count"),
        CheckConstraint("max_attempts BETWEEN 1 AND 10", name="valid_max_attempts"),
        CheckConstraint(
            "char_length(btrim(idempotency_key)) BETWEEN 1 AND 200",
            name="valid_idempotency_key",
        ),
        CheckConstraint(
            "failure_code IS NULL OR failure_code IN ("
            "'dependency_unavailable','external_execution_disabled','invalid_job_state',"
            "'resource_not_found','retry_exhausted','uncertain_external_outcome',"
            "'workflow_execution_failed','workflow_invalid','provider_unavailable',"
            "'feature_not_entitled')",
            name="valid_failure_code",
        ),
        CheckConstraint(
            "(claimed_at IS NULL AND lease_expires_at IS NULL AND worker_id IS NULL) OR "
            "(claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL AND worker_id IS NOT NULL "
            "AND lease_expires_at > claimed_at)",
            name="consistent_lease",
        ),
        CheckConstraint(
            "(status = 'queued' AND claimed_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'processing' AND claimed_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status = 'canceled' AND claimed_at IS NULL AND completed_at IS NOT NULL) OR "
            "(status IN ('succeeded','failed','dead_letter') "
            "AND claimed_at IS NOT NULL AND completed_at IS NOT NULL)",
            name="consistent_lifecycle",
        ),
        CheckConstraint(
            "(status IN ('failed','dead_letter') AND failure_code IS NOT NULL) OR "
            "(status NOT IN ('failed','dead_letter') AND failure_code IS NULL)",
            name="consistent_failure",
        ),
        CheckConstraint(
            "scheduled_occurrence_at IS NULL OR job_type = 'process_scheduled_workflow'",
            name="occurrence_only_for_schedule",
        ),
        Index(
            "ix_background_jobs_claim",
            "status", "priority", "available_at", "id",
        ),
        Index(
            "ix_background_jobs_expired_lease",
            "status", "lease_expires_at", "id",
        ),
        Index(
            "ix_background_jobs_business_status_created",
            "business_id", "status", "created_at", "id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="queued", server_default="queued",
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50, server_default="50")
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    automation_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("automation_events.id", ondelete="CASCADE"), nullable=True,
    )
    workflow_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("automation_workflows.id", ondelete="CASCADE"), nullable=True,
    )
    workflow_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("automation_workflow_runs.id", ondelete="CASCADE"), nullable=True,
    )
    node_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("automation_node_runs.id", ondelete="CASCADE"), nullable=True,
    )
    integration_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("integration_webhook_events.id", ondelete="CASCADE"), nullable=True,
    )
    action_execution_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("action_execution_attempts.id", ondelete="CASCADE"), nullable=True,
    )
    social_schedule_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("social_content_schedules.id", ondelete="CASCADE"), nullable=True,
    )
    subscription_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("business_subscriptions.id", ondelete="CASCADE"), nullable=True,
    )
    competitor_discovery_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    marketing_automation_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    commerce_sync_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    commerce_webhook_receipt_id: Mapped[UUID | None] = mapped_column(nullable=True)
    commerce_feed_destination_id: Mapped[UUID | None] = mapped_column(nullable=True)
    marketing_campaign_id: Mapped[UUID | None] = mapped_column(nullable=True)
    scheduled_occurrence_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class WorkerInstance(Base):
    __tablename__ = "worker_instances"
    __table_args__ = (
        CheckConstraint("role IN ('worker','scheduler')", name="valid_role"),
        CheckConstraint("status IN ('running','stopping','stopped')", name="valid_status"),
        CheckConstraint("char_length(worker_id) BETWEEN 1 AND 96", name="valid_worker_id"),
        CheckConstraint("char_length(version) BETWEEN 1 AND 64", name="valid_version"),
        Index("ix_worker_instances_role_heartbeat", "role", "last_heartbeat_at", "worker_id"),
    )

    worker_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
