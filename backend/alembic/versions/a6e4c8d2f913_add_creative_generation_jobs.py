"""add durable creative generation jobs

Revision ID: a6e4c8d2f913
Revises: 9d7a2c4e6f81
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a6e4c8d2f913"
down_revision: str | Sequence[str] | None = "9d7a2c4e6f81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_JOB_TYPES = (
    "process_automation_event",
    "resume_workflow_run",
    "process_scheduled_workflow",
    "process_integration_event",
    "customer_agent_response",
    "dispatch_action_execution",
    "dispatch_conversation_message",
    "reconcile_uncertain_attempt",
    "mark_social_schedule_ready",
    "maintain_subscription",
    "discover_competitors",
    "generate_content_plan",
    "analyze_campaign_opportunities",
    "analyze_business_opportunity",
    "commerce_initial_sync",
    "commerce_incremental_sync",
    "commerce_webhook_reconcile",
    "google_merchant_status_sync",
    "meta_catalog_status_sync",
    "google_ads_performance_sync",
    "meta_ads_performance_sync",
)


def _job_type_check(*, include_creative: bool) -> str:
    values = _JOB_TYPES + (("generate_creative_asset",) if include_creative else ())
    return "job_type IN (" + ",".join(repr(value) for value in values) + ")"


def upgrade() -> None:
    op.add_column(
        "background_jobs",
        sa.Column(
            "creative_asset_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_jobs_creative_asset_business",
        "background_jobs",
        "marketing_creative_assets",
        ["creative_asset_id", "business_id"],
        ["id", "business_id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_consistent_creative_asset_reference"),
        "background_jobs",
        "(job_type = 'generate_creative_asset' AND creative_asset_id IS NOT NULL) OR "
        "(job_type <> 'generate_creative_asset' AND creative_asset_id IS NULL)",
    )
    op.drop_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        _job_type_check(include_creative=True),
    )

    # Image jobs also use these truthful async states. Provider job identity is
    # still mandatory whenever a video occupies one of them.
    op.drop_constraint(
        op.f("ck_marketing_creative_assets_consistent_video_async_state"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_video_async_state"),
        "marketing_creative_assets",
        "NOT (media_type = 'video' AND generation_status IN "
        "('queued','generating','reviewing','repairing')) OR "
        "(provider_key IS NOT NULL AND provider_job_reference IS NOT NULL "
        "AND storage_reference IS NULL)",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE marketing_creative_assets SET generation_status = 'failed' "
            "WHERE media_type = 'image' AND generation_status IN "
            "('queued','generating','reviewing','repairing')"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM background_jobs "
            "WHERE job_type = 'generate_creative_asset'"
        )
    )
    op.drop_constraint(
        op.f("ck_marketing_creative_assets_consistent_video_async_state"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_video_async_state"),
        "marketing_creative_assets",
        "generation_status NOT IN ('queued','generating','reviewing','repairing') OR "
        "(media_type = 'video' AND provider_key IS NOT NULL "
        "AND provider_job_reference IS NOT NULL AND storage_reference IS NULL)",
    )

    op.drop_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        _job_type_check(include_creative=False),
    )
    op.drop_constraint(
        op.f("ck_background_jobs_consistent_creative_asset_reference"),
        "background_jobs",
        type_="check",
    )
    op.drop_constraint(
        "fk_jobs_creative_asset_business",
        "background_jobs",
        type_="foreignkey",
    )
    op.drop_column("background_jobs", "creative_asset_id")
