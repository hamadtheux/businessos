"""add durable Opportunity analysis jobs

Revision ID: 8f3a2c1d4e90
Revises: 6b6dc1e42c48
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "8f3a2c1d4e90"
down_revision: str | Sequence[str] | None = "6b6dc1e42c48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_JOB_TYPES = (
    "process_automation_event",
    "resume_workflow_run",
    "process_scheduled_workflow",
    "process_integration_event",
    "customer_agent_response",
    "dispatch_action_execution",
    "reconcile_uncertain_attempt",
    "mark_social_schedule_ready",
    "maintain_subscription",
    "discover_competitors",
    "generate_content_plan",
    "analyze_campaign_opportunities",
    "commerce_initial_sync",
    "commerce_incremental_sync",
    "commerce_webhook_reconcile",
    "google_merchant_status_sync",
    "meta_catalog_status_sync",
    "google_ads_performance_sync",
    "meta_ads_performance_sync",
)
_NEW_JOB_TYPES = (
    *_OLD_JOB_TYPES[:12],
    "analyze_business_opportunity",
    *_OLD_JOB_TYPES[12:],
)


def _job_type_check(values: tuple[str, ...]) -> str:
    return "job_type IN (" + ",".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.add_column(
        "background_jobs",
        sa.Column("opportunity_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_jobs_opportunity_business",
        "background_jobs",
        "opportunities",
        ["opportunity_id", "business_id"],
        ["id", "business_id"],
    )
    op.create_index(
        "ix_background_jobs_business_opportunity_status_created",
        "background_jobs",
        ["business_id", "opportunity_id", "status", "created_at", "id"],
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_consistent_opportunity_reference"),
        "background_jobs",
        "(job_type = 'analyze_business_opportunity' AND opportunity_id IS NOT NULL) OR "
        "(job_type <> 'analyze_business_opportunity' AND opportunity_id IS NULL)",
    )
    op.drop_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        _job_type_check(_NEW_JOB_TYPES),
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        type_="check",
    )
    op.execute(
        "DELETE FROM background_jobs "
        "WHERE job_type = 'analyze_business_opportunity'"
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        _job_type_check(_OLD_JOB_TYPES),
    )
    op.drop_constraint(
        op.f("ck_background_jobs_consistent_opportunity_reference"),
        "background_jobs",
        type_="check",
    )
    op.drop_index(
        "ix_background_jobs_business_opportunity_status_created",
        table_name="background_jobs",
    )
    op.drop_constraint(
        "fk_jobs_opportunity_business",
        "background_jobs",
        type_="foreignkey",
    )
    op.drop_column("background_jobs", "opportunity_id")
