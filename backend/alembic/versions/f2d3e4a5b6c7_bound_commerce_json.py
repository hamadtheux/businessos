"""bound commerce cursor and variant option JSON

Revision ID: f2d3e4a5b6c7
Revises: e1c2a3b4d5f6
Create Date: 2026-08-26 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = "f2d3e4a5b6c7"
down_revision: str | None = "e1c2a3b4d5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_commerce_sync_runs_valid_next_cursor"),
        "commerce_sync_runs",
        "jsonb_typeof(next_cursor) = 'object' AND pg_column_size(next_cursor) <= 16384",
    )
    op.create_check_constraint(
        op.f("ck_catalog_variants_valid_option_values"),
        "catalog_variants",
        "jsonb_typeof(option_values) = 'object' AND pg_column_size(option_values) <= 16384",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_catalog_variants_valid_option_values"),
        "catalog_variants",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_commerce_sync_runs_valid_next_cursor"),
        "commerce_sync_runs",
        type_="check",
    )
