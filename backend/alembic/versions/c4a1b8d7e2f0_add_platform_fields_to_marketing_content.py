"""add platform fields to marketing content

Revision ID: c4a1b8d7e2f0
Revises: b8f1d3a5c720
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c4a1b8d7e2f0"
down_revision: str | Sequence[str] | None = "b8f1d3a5c720"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "marketing_content",
        sa.Column(
            "platform_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_marketing_content_valid_platform_fields"),
        "marketing_content",
        "jsonb_typeof(platform_fields) = 'object' AND "
        "octet_length(platform_fields::text) <= 8192",
    )

    # Imported videos are provider-neutral assets, so their exact duration is
    # not restricted to the generated-video presets and they do not carry a
    # provider job identity.
    op.drop_constraint(
        op.f("ck_marketing_creative_assets_valid_duration_seconds"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_duration_seconds"),
        "marketing_creative_assets",
        "duration_seconds IS NULL OR duration_seconds BETWEEN 1 AND 3600",
    )
    op.drop_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_duration"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_duration"),
        "marketing_creative_assets",
        "(media_type = 'image' AND duration_seconds IS NULL) OR "
        "(media_type = 'video' AND duration_seconds BETWEEN 1 AND 3600)",
    )
    op.drop_constraint(
        op.f("ck_marketing_creative_assets_consistent_ready_video_state"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_ready_video_state"),
        "marketing_creative_assets",
        "NOT (media_type = 'video' AND generation_status = 'ready' "
        "AND source_type = 'future_provider') OR "
        "(provider_key IS NOT NULL AND provider_job_reference IS NOT NULL "
        "AND storage_reference IS NOT NULL)",
    )



def _assert_downgrade_safe() -> None:
    """Refuse rollback before DDL when current data cannot fit the old schema."""
    bind = op.get_bind()

    incompatible_video = bool(
        bind.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM marketing_creative_assets
                    WHERE media_type = 'video'
                      AND (
                          duration_seconds IS NULL
                          OR duration_seconds NOT IN (6, 8, 15, 30)
                          OR (
                              generation_status = 'ready'
                              AND (
                                  provider_key IS NULL
                                  OR provider_job_reference IS NULL
                                  OR storage_reference IS NULL
                              )
                          )
                      )
                )
                """
            )
        ).scalar_one()
    )

    if incompatible_video:
        raise RuntimeError(
            "Cannot downgrade migration c4a1b8d7e2f0: "
            "marketing_creative_assets contains video data that cannot be "
            "represented by the historical schema. The old schema accepts "
            "only 6, 8, 15, or 30 second videos and requires provider identity "
            "for every ready video. No schema changes were applied."
        )

    populated_platform_fields = bool(
        bind.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM marketing_content
                    WHERE platform_fields <> '{}'::jsonb
                )
                """
            )
        ).scalar_one()
    )

    if populated_platform_fields:
        raise RuntimeError(
            "Cannot downgrade migration c4a1b8d7e2f0: "
            "marketing_content contains platform-specific data that the "
            "historical schema cannot represent. Refusing to drop "
            "platform_fields and silently lose customer data. "
            "No schema changes were applied."
        )


def downgrade() -> None:
    _assert_downgrade_safe()
    op.drop_constraint(
        op.f("ck_marketing_creative_assets_consistent_ready_video_state"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_ready_video_state"),
        "marketing_creative_assets",
        "NOT (media_type = 'video' AND generation_status = 'ready') OR "
        "(provider_key IS NOT NULL AND provider_job_reference IS NOT NULL "
        "AND storage_reference IS NOT NULL)",
    )
    op.drop_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_duration"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_consistent_media_duration"),
        "(media_type = 'image' AND duration_seconds IS NULL) OR "
        "(media_type = 'video' AND duration_seconds IN (6,8,15,30))",
    )
    op.drop_constraint(
        op.f("ck_marketing_creative_assets_valid_duration_seconds"),
        "marketing_creative_assets",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_marketing_creative_assets_valid_duration_seconds"),
        "marketing_creative_assets",
        "duration_seconds IS NULL OR duration_seconds IN (6,8,15,30)",
    )
    op.drop_constraint(
        op.f("ck_marketing_content_valid_platform_fields"),
        "marketing_content",
        type_="check",
    )
    op.drop_column("marketing_content", "platform_fields")
