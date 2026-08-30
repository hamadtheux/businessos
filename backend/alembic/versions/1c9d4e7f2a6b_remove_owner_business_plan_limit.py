"""remove tenant-plan enforcement from account business creation

Revision ID: 1c9d4e7f2a6b
Revises: f0a7b6c5d4e3
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op


revision: str = "1c9d4e7f2a6b"
down_revision: str | None = "f0a7b6c5d4e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_business_memberships_owner_limit "
        "ON business_memberships"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_owner_business_entitlement()")


def downgrade() -> None:
    raise RuntimeError(
        "This migration is forward-only because restoring tenant-plan control "
        "over account-level business creation would violate the billing model."
    )
