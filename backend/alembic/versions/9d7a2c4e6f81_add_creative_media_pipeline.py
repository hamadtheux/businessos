"""add durable creative media pipeline

Revision ID: 9d7a2c4e6f81
Revises: 8c4f2a91d7e6
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "9d7a2c4e6f81"
down_revision: str | Sequence[str] | None = "8c4f2a91d7e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = "marketing_creative_assets"
    op.add_column(
        table,
        sa.Column("media_type", sa.String(length=16), server_default="image", nullable=False),
    )
    op.add_column(table, sa.Column("duration_seconds", sa.Integer(), nullable=True))
    op.add_column(table, sa.Column("provider_key", sa.String(length=64), nullable=True))
    op.add_column(
        table,
        sa.Column("provider_job_reference", sa.String(length=255), nullable=True),
    )
    op.add_column(
        table,
        sa.Column(
            "creative_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.drop_constraint(
        op.f("ck_marketing_creative_assets_valid_asset_type"),
        table,
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_asset_type"),
        table,
        "asset_type IN ('social_square','story_reel','landscape_ad',"
        "'display_banner','creative_brief','other','video_vertical',"
        "'video_landscape','video_square')",
    )
    op.drop_constraint(
        op.f("ck_marketing_creative_assets_valid_generation_status"),
        table,
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_generation_status"),
        table,
        "generation_status IN ('draft','brief_ready','strategy_ready',"
        "'provider_required','queued','generating','reviewing','repairing',"
        "'ready','failed','archived')",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_media_type"),
        table,
        "media_type IN ('image','video')",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_asset_type"),
        table,
        "(media_type = 'image' AND asset_type IN "
        "('social_square','story_reel','landscape_ad','display_banner',"
        "'creative_brief','other')) OR "
        "(media_type = 'video' AND asset_type IN "
        "('video_vertical','video_landscape','video_square'))",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_duration_seconds"),
        table,
        "duration_seconds IS NULL OR duration_seconds IN (6,8,15,30)",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_provider_key"),
        table,
        "provider_key IS NULL OR "
        "char_length(btrim(provider_key)) BETWEEN 1 AND 64",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_provider_job_reference"),
        table,
        "provider_job_reference IS NULL OR "
        "char_length(btrim(provider_job_reference)) BETWEEN 1 AND 255",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_creative_metadata"),
        table,
        "jsonb_typeof(creative_metadata) = 'object' "
        "AND octet_length(creative_metadata::text) <= 16384",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_duration"),
        table,
        "(media_type = 'image' AND duration_seconds IS NULL) OR "
        "(media_type = 'video' AND duration_seconds IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_provider_job_identity"),
        table,
        "provider_job_reference IS NULL OR provider_key IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_image_provider_identity"),
        table,
        "media_type <> 'image' OR "
        "(provider_key IS NULL AND provider_job_reference IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_provider_required_state"),
        table,
        "generation_status <> 'provider_required' OR "
        "(provider_key IS NULL AND provider_job_reference IS NULL "
        "AND storage_reference IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_video_strategy_state"),
        table,
        "generation_status <> 'strategy_ready' OR "
        "(media_type = 'video' AND provider_key IS NULL "
        "AND provider_job_reference IS NULL AND storage_reference IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_video_async_state"),
        table,
        "generation_status NOT IN "
        "('queued','generating','reviewing','repairing') OR "
        "(media_type = 'video' AND provider_key IS NOT NULL "
        "AND provider_job_reference IS NOT NULL AND storage_reference IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_ready_video_state"),
        table,
        "NOT (media_type = 'video' AND generation_status = 'ready') OR "
        "(provider_key IS NOT NULL AND provider_job_reference IS NOT NULL "
        "AND storage_reference IS NOT NULL)",
    )
    op.create_index(
        "uq_marketing_creative_assets_provider_job",
        table,
        ["provider_key", "provider_job_reference"],
        unique=True,
        postgresql_where=sa.text("provider_job_reference IS NOT NULL"),
    )


def downgrade() -> None:
    table = "marketing_creative_assets"
    op.drop_index(
        "uq_marketing_creative_assets_provider_job",
        table_name=table,
    )
    for name in (
        "consistent_ready_video_state",
        "consistent_video_async_state",
        "consistent_video_strategy_state",
        "consistent_provider_required_state",
        "consistent_image_provider_identity",
        "consistent_provider_job_identity",
        "consistent_media_duration",
        "consistent_media_asset_type",
        "valid_creative_metadata",
        "valid_provider_job_reference",
        "valid_provider_key",
        "valid_duration_seconds",
        "valid_media_type",
    ):
        op.drop_constraint(
            op.f(f"ck_marketing_creative_assets_{name}"),
            table,
            type_="check",
        )
    op.drop_constraint(
        op.f("ck_marketing_creative_assets_valid_generation_status"),
        table,
        type_="check",
    )
    # Keep every creative row representable by the prior schema before its
    # narrower constraints are restored. This downgrade intentionally discards
    # feature-specific video strategy and provider metadata when the new columns
    # are removed; the bounded legacy visual_direction field is not a safe lossless
    # destination for the canonical structured strategy.
    op.execute(
        sa.text(
            "UPDATE marketing_creative_assets "
            "SET generation_status = 'provider_required' "
            "WHERE generation_status IN "
            "('strategy_ready','queued','generating','reviewing','repairing')"
        )
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_generation_status"),
        table,
        "generation_status IN ('draft','brief_ready','provider_required',"
        "'ready','failed','archived')",
    )
    op.drop_constraint(
        op.f("ck_marketing_creative_assets_valid_asset_type"),
        table,
        type_="check",
    )
    op.execute(
        sa.text(
            "UPDATE marketing_creative_assets SET asset_type = 'other' "
            "WHERE asset_type IN "
            "('video_vertical','video_landscape','video_square')"
        )
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_asset_type"),
        table,
        "asset_type IN ('social_square','story_reel','landscape_ad',"
        "'display_banner','creative_brief','other')",
    )
    op.drop_column(table, "creative_metadata")
    op.drop_column(table, "provider_job_reference")
    op.drop_column(table, "provider_key")
    op.drop_column(table, "duration_seconds")
    op.drop_column(table, "media_type")
