"""bind approvals to exact AI action authorization

Revision ID: a4d9e7c2b610
Revises: 8f3a2c1d4e90
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a4d9e7c2b610"
down_revision: str | Sequence[str] | None = "8f3a2c1d4e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column("action_type_snapshot", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "approval_requests",
        sa.Column(
            "authorized_payload_hash_snapshot",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE approval_requests AS approval "
        "SET action_type_snapshot = action.action_type, "
        "authorized_payload_hash_snapshot = action.authorized_payload_hash "
        "FROM ai_actions AS action "
        "WHERE approval.action_id = action.id "
        "AND approval.business_id = action.business_id"
    )
    op.create_check_constraint(
        op.f("ck_approval_requests_valid_action_type_snapshot"),
        "approval_requests",
        "action_type_snapshot IS NULL OR "
        "char_length(btrim(action_type_snapshot)) BETWEEN 1 AND 100",
    )
    op.create_check_constraint(
        op.f("ck_approval_requests_valid_authorized_payload_hash_snapshot"),
        "approval_requests",
        "authorized_payload_hash_snapshot IS NULL OR ("
        "char_length(authorized_payload_hash_snapshot) = 64 AND "
        "authorized_payload_hash_snapshot ~ '^[0-9a-f]{64}$'"
        ")",
    )
    op.create_check_constraint(
        op.f("ck_approval_requests_consistent_action_authorization_snapshot"),
        "approval_requests",
        "(action_id IS NULL AND action_type_snapshot IS NULL AND "
        "authorized_payload_hash_snapshot IS NULL) OR "
        "(action_id IS NOT NULL AND action_type_snapshot IS NOT NULL AND "
        "authorized_payload_hash_snapshot IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_approval_requests_consistent_action_authorization_snapshot"),
        "approval_requests",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_approval_requests_valid_authorized_payload_hash_snapshot"),
        "approval_requests",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_approval_requests_valid_action_type_snapshot"),
        "approval_requests",
        type_="check",
    )
    op.drop_column("approval_requests", "authorized_payload_hash_snapshot")
    op.drop_column("approval_requests", "action_type_snapshot")
