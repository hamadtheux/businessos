"""add business branding

Revision ID: 4670d54b3fab
Revises: 6a95480ead1d
Create Date: 2026-08-19 21:45:56.520816

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4670d54b3fab"
down_revision: Union[str, Sequence[str], None] = "6a95480ead1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "business_branding",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("logo_url", sa.String(length=2048), nullable=True),
        sa.Column("primary_color", sa.String(length=7), nullable=True),
        sa.Column("secondary_color", sa.String(length=7), nullable=True),
        sa.Column("accent_color", sa.String(length=7), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "accent_color IS NULL "
            "OR accent_color ~ '^#[0-9A-Fa-f]{6}$'",
            name=op.f("ck_business_branding_valid_accent_color"),
        ),
        sa.CheckConstraint(
            "primary_color IS NULL "
            "OR primary_color ~ '^#[0-9A-Fa-f]{6}$'",
            name=op.f("ck_business_branding_valid_primary_color"),
        ),
        sa.CheckConstraint(
            "secondary_color IS NULL "
            "OR secondary_color ~ '^#[0-9A-Fa-f]{6}$'",
            name=op.f("ck_business_branding_valid_secondary_color"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_business_branding_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "business_id",
            name=op.f("pk_business_branding"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("business_branding")
