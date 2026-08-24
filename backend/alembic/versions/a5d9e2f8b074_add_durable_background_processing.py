"""add durable background processing

Revision ID: a5d9e2f8b074
Revises: f4c8d1e7a963
Create Date: 2026-08-23 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a5d9e2f8b074"
down_revision: str | None = "f4c8d1e7a963"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_instances",
        sa.Column("worker_id", sa.String(length=96), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('worker','scheduler')", name=op.f("ck_worker_instances_valid_role")),
        sa.CheckConstraint("status IN ('running','stopping','stopped')", name=op.f("ck_worker_instances_valid_status")),
        sa.CheckConstraint("char_length(worker_id) BETWEEN 1 AND 96", name=op.f("ck_worker_instances_valid_worker_id")),
        sa.CheckConstraint("char_length(version) BETWEEN 1 AND 64", name=op.f("ck_worker_instances_valid_version")),
        sa.PrimaryKeyConstraint("worker_id", name=op.f("pk_worker_instances")),
    )
    op.create_index(
        "ix_worker_instances_role_heartbeat",
        "worker_instances",
        ["role", "last_heartbeat_at", "worker_id"],
    )

    op.create_table(
        "background_jobs",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="50", nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=96), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("automation_event_id", sa.Uuid(), nullable=True),
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("node_run_id", sa.Uuid(), nullable=True),
        sa.Column("integration_event_id", sa.Uuid(), nullable=True),
        sa.Column("action_execution_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("social_schedule_id", sa.Uuid(), nullable=True),
        sa.Column("scheduled_occurrence_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "job_type IN ('process_automation_event','resume_workflow_run','process_scheduled_workflow',"
            "'process_integration_event','reconcile_uncertain_attempt','mark_social_schedule_ready')",
            name=op.f("ck_background_jobs_valid_job_type"),
        ),
        sa.CheckConstraint(
            "status IN ('queued','processing','succeeded','failed','dead_letter','canceled')",
            name=op.f("ck_background_jobs_valid_status"),
        ),
        sa.CheckConstraint("priority BETWEEN 0 AND 100", name=op.f("ck_background_jobs_valid_priority")),
        sa.CheckConstraint("attempt_count BETWEEN 0 AND max_attempts", name=op.f("ck_background_jobs_valid_attempt_count")),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 10", name=op.f("ck_background_jobs_valid_max_attempts")),
        sa.CheckConstraint("char_length(btrim(idempotency_key)) BETWEEN 1 AND 200", name=op.f("ck_background_jobs_valid_idempotency_key")),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code IN ('dependency_unavailable','external_execution_disabled',"
            "'invalid_job_state','resource_not_found','retry_exhausted','uncertain_external_outcome',"
            "'workflow_execution_failed','workflow_invalid')",
            name=op.f("ck_background_jobs_valid_failure_code"),
        ),
        sa.CheckConstraint(
            "(claimed_at IS NULL AND lease_expires_at IS NULL AND worker_id IS NULL) OR "
            "(claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL AND worker_id IS NOT NULL "
            "AND lease_expires_at > claimed_at)",
            name=op.f("ck_background_jobs_consistent_lease"),
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND claimed_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'processing' AND claimed_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status = 'canceled' AND claimed_at IS NULL AND completed_at IS NOT NULL) OR "
            "(status IN ('succeeded','failed','dead_letter') AND claimed_at IS NOT NULL AND completed_at IS NOT NULL)",
            name=op.f("ck_background_jobs_consistent_lifecycle"),
        ),
        sa.CheckConstraint(
            "(status IN ('failed','dead_letter') AND failure_code IS NOT NULL) OR "
            "(status NOT IN ('failed','dead_letter') AND failure_code IS NULL)",
            name=op.f("ck_background_jobs_consistent_failure"),
        ),
        sa.CheckConstraint(
            "scheduled_occurrence_at IS NULL OR job_type = 'process_scheduled_workflow'",
            name=op.f("ck_background_jobs_occurrence_only_for_schedule"),
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_background_jobs_business_id_businesses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["automation_event_id"], ["automation_events.id"], name=op.f("fk_background_jobs_automation_event_id_automation_events"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["automation_workflows.id"], name=op.f("fk_background_jobs_workflow_id_automation_workflows"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["automation_workflow_runs.id"], name=op.f("fk_background_jobs_workflow_run_id_automation_workflow_runs"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_run_id"], ["automation_node_runs.id"], name=op.f("fk_background_jobs_node_run_id_automation_node_runs"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["integration_event_id"], ["integration_webhook_events.id"], name=op.f("fk_background_jobs_integration_event_id_integration_webhook_events"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["action_execution_attempt_id"], ["action_execution_attempts.id"], name=op.f("fk_background_jobs_action_execution_attempt_id_action_execution_attempts"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["social_schedule_id"], ["social_content_schedules.id"], name=op.f("fk_background_jobs_social_schedule_id_social_content_schedules"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_jobs")),
        sa.UniqueConstraint("idempotency_key", name="uq_background_jobs_idempotency_key"),
    )
    op.create_index("ix_background_jobs_claim", "background_jobs", ["status", "priority", "available_at", "id"])
    op.create_index("ix_background_jobs_expired_lease", "background_jobs", ["status", "lease_expires_at", "id"])
    op.create_index("ix_background_jobs_business_status_created", "background_jobs", ["business_id", "status", "created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_background_jobs_business_status_created", table_name="background_jobs")
    op.drop_index("ix_background_jobs_expired_lease", table_name="background_jobs")
    op.drop_index("ix_background_jobs_claim", table_name="background_jobs")
    op.drop_table("background_jobs")
    op.drop_index("ix_worker_instances_role_heartbeat", table_name="worker_instances")
    op.drop_table("worker_instances")
