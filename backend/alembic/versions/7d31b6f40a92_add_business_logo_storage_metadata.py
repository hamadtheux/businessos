"""add business logo storage metadata

Revision ID: 7d31b6f40a92
Revises: 2500084c0abd
Create Date: 2026-08-20 03:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7d31b6f40a92"
down_revision: Union[str, Sequence[str], None] = "2500084c0abd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "business_branding",
        sa.Column(
            "logo_storage_key",
            sa.String(length=1024),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("business_branding", "logo_storage_key")
