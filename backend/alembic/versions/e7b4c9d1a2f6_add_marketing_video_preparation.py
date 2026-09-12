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

_CREATIVE_ASSET_TYPES_WITH_VIDEO_SOURCE = (
    "asset_type IN ("
    "'social_square','story_reel','landscape_ad','display_banner',"
    "'creative_brief','other','video_source','video_vertical',"
    "'video_landscape','video_square'"
    ")"
)

_CREATIVE_ASSET_TYPES_HISTORICAL = (
    "asset_type IN ("
    "'social_square','story_reel','landscape_ad','display_banner',"
    "'creative_brief','other','video_vertical','video_landscape',"
    "'video_square'"
    ")"
)

_CONSISTENT_MEDIA_ASSET_TYPE_WITH_VIDEO_SOURCE = (
    "(media_type = 'image' AND asset_type IN ("
    "'social_square','story_reel','landscape_ad','display_banner',"
    "'creative_brief','other')) OR (media_type = 'video' AND asset_type IN ("
    "'video_source','video_vertical','video_landscape','video_square'))"
)

_CONSISTENT_MEDIA_ASSET_TYPE_HISTORICAL = (
    "(media_type = 'image' AND asset_type IN ("
    "'social_square','story_reel','landscape_ad','display_banner',"
    "'creative_brief','other')) OR (media_type = 'video' AND asset_type IN ("
    "'video_vertical','video_landscape','video_square'))"
)

_CONSISTENT_MEDIA_DURATION_WITH_PROCESSING = (
    "(media_type = 'image' AND duration_seconds IS NULL) OR "
    "(media_type = 'video' AND ((duration_seconds IS NOT NULL AND "
    "duration_seconds BETWEEN 1 AND 3600) OR (duration_seconds IS NULL AND "
    "source_type = 'import' AND asset_type = 'video_source' AND "
    "generation_status IN ('processing','failed'))))"
)

_CONSISTENT_MEDIA_DURATION_HISTORICAL = (
    "(media_type = 'image' AND duration_seconds IS NULL) OR "
    "(media_type = 'video' AND duration_seconds BETWEEN 1 AND 3600)"
)

_CONSISTENT_VIDEO_SOURCE_STATE = (
    "asset_type <> 'video_source' OR (media_type = 'video' AND "
    "source_type = 'import' AND generation_status IN ('processing','failed'))"
)

_CONSISTENT_PROCESSING_VIDEO_STATE = (
    "generation_status <> 'processing' OR (media_type = 'video' AND "
    "source_type = 'import' AND asset_type = 'video_source' AND "
    "storage_reference IS NOT NULL AND width IS NULL AND height IS NULL AND "
    "aspect_ratio IS NULL AND duration_seconds IS NULL AND provider_key IS NULL "
    "AND provider_job_reference IS NULL)"
)

_CONSISTENT_READY_VIDEO_METADATA = (
    "NOT (media_type = 'video' AND generation_status = 'ready' AND "
    "creative_metadata ? 'video_preparation') OR ("
    "COALESCE(creative_metadata #>> '{video_preparation,status}', '') "
    "= 'ready' AND "
    "asset_type IN ('video_vertical','video_landscape','video_square') AND "
    "storage_reference IS NOT NULL AND width BETWEEN 1 AND 20000 AND "
    "height BETWEEN 1 AND 20000 AND char_length(btrim(aspect_ratio)) "
    "BETWEEN 3 AND 16 AND duration_seconds BETWEEN 1 AND 3600)"
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

    op.drop_constraint(
        op.f("ck_marketing_creative_assets_valid_asset_type"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_asset_type"),
        "marketing_creative_assets",
        _CREATIVE_ASSET_TYPES_WITH_VIDEO_SOURCE,
    )

    op.drop_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_asset_type"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_asset_type"),
        "marketing_creative_assets",
        _CONSISTENT_MEDIA_ASSET_TYPE_WITH_VIDEO_SOURCE,
    )

    op.drop_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_duration"),
        "marketing_creative_assets",
        type_="check",
    )

    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_duration"),
        "marketing_creative_assets",
        _CONSISTENT_MEDIA_DURATION_WITH_PROCESSING,
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_video_source_state"),
        "marketing_creative_assets",
        _CONSISTENT_VIDEO_SOURCE_STATE,
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_processing_video_state"),
        "marketing_creative_assets",
        _CONSISTENT_PROCESSING_VIDEO_STATE,
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_ready_video_metadata"),
        "marketing_creative_assets",
        _CONSISTENT_READY_VIDEO_METADATA,
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

    has_incompatible_assets = bool(
        bind.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM marketing_creative_assets
                    WHERE generation_status = 'processing'
                       OR asset_type = 'video_source'
                       OR (
                           media_type = 'video'
                           AND duration_seconds IS NULL
                       )
                )
                """
            )
        ).scalar_one()
    )

    if has_incompatible_assets:
        raise RuntimeError(
            "Cannot downgrade migration e7b4c9d1a2f6: "
            "marketing_creative_assets contains asynchronous video state that the "
            "historical schema cannot represent. No schema changes were applied."
        )


def downgrade() -> None:
    _assert_downgrade_safe()

    for name in (
        "consistent_ready_video_metadata",
        "consistent_processing_video_state",
        "consistent_video_source_state",
    ):
        op.drop_constraint(
            op.f(f"ck_marketing_creative_assets_{name}"),
            "marketing_creative_assets",
            type_="check",
        )

    op.drop_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_duration"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_duration"),
        "marketing_creative_assets",
        _CONSISTENT_MEDIA_DURATION_HISTORICAL,
    )

    op.drop_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_asset_type"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_asset_type"),
        "marketing_creative_assets",
        _CONSISTENT_MEDIA_ASSET_TYPE_HISTORICAL,
    )

    op.drop_constraint(
        op.f("ck_marketing_creative_assets_valid_asset_type"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_asset_type"),
        "marketing_creative_assets",
        _CREATIVE_ASSET_TYPES_HISTORICAL,
    )

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
