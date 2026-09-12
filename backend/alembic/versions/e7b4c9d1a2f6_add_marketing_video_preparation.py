"""add marketing video preparation

Revision ID: e7b4c9d1a2f6
Revises: c4a1b8d7e2f0
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e7b4c9d1a2f6"
down_revision: str | Sequence[str] | None = "c4a1b8d7e2f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BACKGROUND_JOB_TYPES_WITH_VIDEO_PREPARATION = (
    "job_type IN ("
    "'process_automation_event',"
    "'resume_workflow_run',"
    "'process_scheduled_workflow',"
    "'process_integration_event',"
    "'customer_agent_response',"
    "'dispatch_action_execution',"
    "'dispatch_conversation_message',"
    "'reconcile_uncertain_attempt',"
    "'mark_social_schedule_ready',"
    "'maintain_subscription',"
    "'discover_competitors',"
    "'generate_content_plan',"
    "'analyze_campaign_opportunities',"
    "'analyze_business_opportunity',"
    "'commerce_initial_sync',"
    "'commerce_incremental_sync',"
    "'commerce_webhook_reconcile',"
    "'google_merchant_status_sync',"
    "'meta_catalog_status_sync',"
    "'google_ads_performance_sync',"
    "'meta_ads_performance_sync',"
    "'generate_creative_asset',"
    "'prepare_marketing_video'"
    ")"
)

_BACKGROUND_JOB_TYPES_HISTORICAL = (
    "job_type IN ("
    "'process_automation_event',"
    "'resume_workflow_run',"
    "'process_scheduled_workflow',"
    "'process_integration_event',"
    "'customer_agent_response',"
    "'dispatch_action_execution',"
    "'dispatch_conversation_message',"
    "'reconcile_uncertain_attempt',"
    "'mark_social_schedule_ready',"
    "'maintain_subscription',"
    "'discover_competitors',"
    "'generate_content_plan',"
    "'analyze_campaign_opportunities',"
    "'analyze_business_opportunity',"
    "'commerce_initial_sync',"
    "'commerce_incremental_sync',"
    "'commerce_webhook_reconcile',"
    "'google_merchant_status_sync',"
    "'meta_catalog_status_sync',"
    "'google_ads_performance_sync',"
    "'meta_ads_performance_sync',"
    "'generate_creative_asset'"
    ")"
)

_CREATIVE_STATUS_WITH_PROCESSING = (
    "generation_status IN ("
    "'draft','brief_ready','strategy_ready','provider_required',"
    "'queued','generating','reviewing','repairing','processing',"
    "'ready','failed','archived'"
    ")"
)

_CREATIVE_STATUS_HISTORICAL = (
    "generation_status IN ("
    "'draft','brief_ready','strategy_ready','provider_required',"
    "'queued','generating','reviewing','repairing',"
    "'ready','failed','archived'"
    ")"
)


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        _BACKGROUND_JOB_TYPES_WITH_VIDEO_PREPARATION,
    )

    op.drop_constraint(
        op.f("ck_background_jobs_consistent_creative_asset_reference"),
        "background_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_consistent_creative_asset_reference"),
        "background_jobs",
        "("
        "job_type IN ('generate_creative_asset','prepare_marketing_video') "
        "AND creative_asset_id IS NOT NULL"
        ") OR ("
        "job_type NOT IN ('generate_creative_asset','prepare_marketing_video') "
        "AND creative_asset_id IS NULL"
        ")",
    )

    op.drop_constraint(
        op.f("ck_marketing_creative_assets_valid_generation_status"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_generation_status"),
        "marketing_creative_assets",
        _CREATIVE_STATUS_WITH_PROCESSING,
    )


def _assert_downgrade_safe() -> None:
    """Refuse rollback before DDL when new video-preparation state exists."""
    bind = op.get_bind()

    has_video_jobs = bool(
        bind.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM background_jobs
                    WHERE job_type = 'prepare_marketing_video'
                )
                """
            )
        ).scalar_one()
    )

    if has_video_jobs:
        raise RuntimeError(
            "Cannot downgrade migration e7b4c9d1a2f6: "
            "background_jobs contains prepare_marketing_video jobs that the "
            "historical schema cannot represent. No schema changes were applied."
        )

    has_processing_assets = bool(
        bind.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM marketing_creative_assets
                    WHERE generation_status = 'processing'
                )
                """
            )
        ).scalar_one()
    )

    if has_processing_assets:
        raise RuntimeError(
            "Cannot downgrade migration e7b4c9d1a2f6: "
            "marketing_creative_assets contains processing assets that the "
            "historical schema cannot represent. No schema changes were applied."
        )


def downgrade() -> None:
    _assert_downgrade_safe()

    op.drop_constraint(
        op.f("ck_marketing_creative_assets_valid_generation_status"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_generation_status"),
        "marketing_creative_assets",
        _CREATIVE_STATUS_HISTORICAL,
    )

    op.drop_constraint(
        op.f("ck_background_jobs_consistent_creative_asset_reference"),
        "background_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_consistent_creative_asset_reference"),
        "background_jobs",
        "(job_type = 'generate_creative_asset' "
        "AND creative_asset_id IS NOT NULL) OR "
        "(job_type <> 'generate_creative_asset' "
        "AND creative_asset_id IS NULL)",
    )

    op.drop_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        _BACKGROUND_JOB_TYPES_HISTORICAL,
    )
