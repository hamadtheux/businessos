"""normalize customer channel constraint name

Revision ID: b8f1d3a5c720
Revises: a6e4c8d2f913
Create Date: 2026-09-08
"""

from collections.abc import Sequence

from alembic import op


revision: str = "b8f1d3a5c720"
down_revision: str | Sequence[str] | None = "a6e4c8d2f913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "customer_channel_identities"
_TRUNCATED_NAME = "ck_customer_channel_identities_valid_external_resource__d9c9"
_STABLE_NAME = "ck_customer_channel_identities_valid_external_resource_ref"
_EXPRESSION = (
    "char_length(btrim(external_resource_reference)) BETWEEN 1 AND 255"
)


def upgrade() -> None:
    # The original convention-expanded identifier exceeded PostgreSQL's
    # 63-byte limit and was hash-truncated by the dialect. Use an intentionally
    # short stable name so schema reflection and model metadata agree.
    op.drop_constraint(op.f(_TRUNCATED_NAME), _TABLE, type_="check")
    op.create_check_constraint(op.f(_STABLE_NAME), _TABLE, _EXPRESSION)


def downgrade() -> None:
    op.drop_constraint(op.f(_STABLE_NAME), _TABLE, type_="check")
    op.create_check_constraint(op.f(_TRUNCATED_NAME), _TABLE, _EXPRESSION)
