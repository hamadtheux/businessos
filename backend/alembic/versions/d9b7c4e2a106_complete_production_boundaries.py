"""complete production profile and connector-dispatch boundaries

Revision ID: d9b7c4e2a106
Revises: d8f5a2c9e013
Create Date: 2026-08-24 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d9b7c4e2a106"
down_revision: str | None = "d8f5a2c9e013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_JOB_TYPES = (
    "'process_automation_event','resume_workflow_run',"
    "'process_scheduled_workflow','process_integration_event',"
    "'reconcile_uncertain_attempt','mark_social_schedule_ready',"
    "'maintain_subscription','discover_competitors',"
    "'generate_content_plan','analyze_campaign_opportunities'"
)


def upgrade() -> None:
    op.add_column("businesses", sa.Column("website_url", sa.String(2048)))
    op.add_column("businesses", sa.Column("location", sa.String(500)))
    op.add_column("businesses", sa.Column("description", sa.Text()))
    op.add_column("businesses", sa.Column("brand_voice", sa.Text()))
    op.add_column(
        "businesses",
        sa.Column(
            "avoid_keywords",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_businesses_valid_avoid_keywords"),
        "businesses",
        "jsonb_typeof(avoid_keywords) = 'array' AND "
        "jsonb_array_length(avoid_keywords) <= 100",
    )

    op.drop_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        f"job_type IN ({_OLD_JOB_TYPES},'dispatch_action_execution')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        type_="check",
    )
    op.execute(
        "DELETE FROM background_jobs WHERE job_type = 'dispatch_action_execution'"
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        f"job_type IN ({_OLD_JOB_TYPES})",
    )
    op.drop_constraint(
        op.f("ck_businesses_valid_avoid_keywords"),
        "businesses",
        type_="check",
    )
    op.drop_column("businesses", "avoid_keywords")
    op.drop_column("businesses", "brand_voice")
    op.drop_column("businesses", "description")
    op.drop_column("businesses", "location")
    op.drop_column("businesses", "website_url")
