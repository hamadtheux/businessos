"""add approval requests

Revision ID: d4e8a1b7c930
Revises: c2c7d7752db5
Create Date: 2026-08-22 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e8a1b7c930"
down_revision: Union[str, Sequence[str], None] = "c2c7d7752db5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_ai_actions_id_business",
        "ai_actions",
        ["id", "business_id"],
    )

    op.create_table(
        "approval_requests",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decision_actor_id", sa.Uuid(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
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
            "status IN ('pending', 'approved', 'rejected', 'expired', 'canceled')",
            name=op.f("ck_approval_requests_valid_status"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(reason_code)) BETWEEN 1 AND 64",
            name=op.f("ck_approval_requests_valid_reason_code"),
        ),
        sa.CheckConstraint(
            "decision_note IS NULL OR "
            "char_length(btrim(decision_note)) BETWEEN 1 AND 2000",
            name=op.f("ck_approval_requests_valid_decision_note"),
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > requested_at",
            name=op.f("ck_approval_requests_valid_expiration"),
        ),
        sa.CheckConstraint(
            "decided_at IS NULL OR decided_at >= requested_at",
            name=op.f("ck_approval_requests_valid_decision_time"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL "
            "AND decided_by_user_id IS NULL AND decision_actor_id IS NULL) "
            "OR (status IN ('approved', 'rejected') AND decided_at IS NOT NULL "
            "AND decision_actor_id IS NOT NULL) "
            "OR (status IN ('expired', 'canceled') AND decided_at IS NOT NULL)",
            name=op.f("ck_approval_requests_consistent_lifecycle"),
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "business_id"],
            ["ai_actions.id", "ai_actions.business_id"],
            name="fk_approval_requests_action_business_ai_actions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_approval_requests_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_approval_requests_decided_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_approval_requests_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
    )

    op.create_index(
        "ix_approval_requests_one_pending_action",
        "approval_requests",
        ["action_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_approval_requests_business_status_created",
        "approval_requests",
        ["business_id", "status", "created_at", "id"],
    )
    op.create_index(
        "ix_approval_requests_business_action",
        "approval_requests",
        ["business_id", "action_id", "created_at"],
    )
    op.create_index(
        "ix_approval_requests_decider_decided",
        "approval_requests",
        ["decided_by_user_id", "decided_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_approval_requests_decider_decided",
        table_name="approval_requests",
    )
    op.drop_index(
        "ix_approval_requests_business_action",
        table_name="approval_requests",
    )
    op.drop_index(
        "ix_approval_requests_business_status_created",
        table_name="approval_requests",
    )
    op.drop_index(
        "ix_approval_requests_one_pending_action",
        table_name="approval_requests",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("approval_requests")
    op.drop_constraint(
        "uq_ai_actions_id_business",
        "ai_actions",
        type_="unique",
    )
