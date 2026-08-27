"""add customer agent response job type

Revision ID: a024bca1972e
Revises: 11ec1b452f69
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a024bca1972e"
down_revision: str | Sequence[str] | None = "11ec1b452f69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_JOB_TYPES = (
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
    "commerce_initial_sync",
    "commerce_incremental_sync",
    "commerce_webhook_reconcile",
    "google_merchant_status_sync",
    "meta_catalog_status_sync",
    "google_ads_performance_sync",
    "meta_ads_performance_sync",
)

_NEW_JOB_TYPES = (
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


def _job_type_check(values: tuple[str, ...]) -> str:
    quoted = ",".join(f"'{value}'" for value in values)
    return f"job_type IN ({quoted})"


def upgrade() -> None:
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
    op.create_check_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        _job_type_check(_OLD_JOB_TYPES),
    )
