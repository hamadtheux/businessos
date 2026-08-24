"""add action execution attempts

Revision ID: e6f9b2c8d041
Revises: d4e8a1b7c930
Create Date: 2026-08-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6f9b2c8d041"
down_revision: Union[str, Sequence[str], None] = "d4e8a1b7c930"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_ai_actions_valid_status"),
        "ai_actions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_actions_valid_execution_timing_state"),
        "ai_actions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_actions_valid_failure_state"),
        "ai_actions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_actions_executable_state_requires_allowing_policy"),
        "ai_actions",
        type_="check",
    )

    op.create_check_constraint(
        op.f("ck_ai_actions_valid_status"),
        "ai_actions",
        "status IN ('proposed', 'pending_approval', 'ready', 'queued', "
        "'blocked', 'rejected', 'expired', 'executing', 'succeeded', "
        "'failed', 'uncertain', 'canceled')",
    )
    op.create_check_constraint(
        op.f("ck_ai_actions_valid_execution_timing_state"),
        "ai_actions",
        "(status = 'executing' AND execution_started_at IS NOT NULL "
        "AND execution_completed_at IS NULL) OR "
        "(status IN ('succeeded', 'failed', 'uncertain') "
        "AND execution_started_at IS NOT NULL "
        "AND execution_completed_at IS NOT NULL) OR "
        "(status NOT IN ('executing', 'succeeded', 'failed', 'uncertain') "
        "AND execution_started_at IS NULL "
        "AND execution_completed_at IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_ai_actions_valid_failure_state"),
        "ai_actions",
        "(status IN ('failed', 'uncertain') AND failure_code IS NOT NULL) OR "
        "(status NOT IN ('failed', 'uncertain') AND failure_code IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_ai_actions_executable_state_requires_allowing_policy"),
        "ai_actions",
        "status NOT IN ('ready', 'queued', 'executing', 'succeeded', "
        "'failed', 'uncertain') OR "
        "policy_decision IN ('allow', 'require_approval')",
    )

    op.create_table(
        "action_execution_attempts",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("action_type", sa.String(length=100), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_reference_id", sa.String(length=255), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
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
            "attempt_number BETWEEN 1 AND 1000000",
            name=op.f("ck_action_execution_attempts_valid_attempt_number"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(idempotency_key)) BETWEEN 1 AND 200",
            name=op.f("ck_action_execution_attempts_valid_idempotency_key"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(action_type)) BETWEEN 1 AND 100",
            name=op.f("ck_action_execution_attempts_valid_action_type"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(capability)) BETWEEN 1 AND 128",
            name=op.f("ck_action_execution_attempts_valid_capability"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'dispatching', 'succeeded', "
            "'failed', 'uncertain', 'canceled')",
            name=op.f("ck_action_execution_attempts_valid_status"),
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR "
            "char_length(btrim(failure_code)) BETWEEN 1 AND 64",
            name=op.f("ck_action_execution_attempts_valid_failure_code"),
        ),
        sa.CheckConstraint(
            "external_reference_id IS NULL OR "
            "char_length(btrim(external_reference_id)) BETWEEN 1 AND 255",
            name=op.f("ck_action_execution_attempts_valid_external_reference_id"),
        ),
        sa.CheckConstraint(
            "dispatch_started_at IS NULL OR dispatch_started_at >= queued_at",
            name=op.f("ck_action_execution_attempts_valid_dispatch_start"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= queued_at",
            name=op.f("ck_action_execution_attempts_valid_completion"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR dispatch_started_at IS NULL "
            "OR completed_at >= dispatch_started_at",
            name=op.f("ck_action_execution_attempts_completion_after_dispatch"),
        ),
        sa.CheckConstraint(
            "(lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_acquired_at >= queued_at "
            "AND lease_expires_at > lease_acquired_at)",
            name=op.f("ck_action_execution_attempts_consistent_lease"),
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND dispatch_started_at IS NULL "
            "AND completed_at IS NULL AND lease_acquired_at IS NULL "
            "AND lease_expires_at IS NULL) OR "
            "(status = 'dispatching' AND dispatch_started_at IS NOT NULL "
            "AND completed_at IS NULL AND lease_acquired_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status IN ('succeeded', 'failed', 'uncertain') "
            "AND dispatch_started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND lease_acquired_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status = 'canceled' AND dispatch_started_at IS NULL "
            "AND completed_at IS NOT NULL AND lease_acquired_at IS NULL "
            "AND lease_expires_at IS NULL)",
            name=op.f("ck_action_execution_attempts_consistent_lifecycle"),
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND failure_code IS NULL) OR "
            "(status IN ('failed', 'uncertain') AND failure_code IS NOT NULL) OR "
            "(status NOT IN ('succeeded', 'failed', 'uncertain') "
            "AND failure_code IS NULL)",
            name=op.f("ck_action_execution_attempts_consistent_failure"),
        ),
        sa.CheckConstraint(
            "external_reference_id IS NULL OR status = 'succeeded'",
            name=op.f(
                "ck_action_execution_attempts_external_reference_only_on_success"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "business_id"],
            ["ai_actions.id", "ai_actions.business_id"],
            name="fk_action_execution_attempts_action_business_ai_actions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_action_execution_attempts_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_execution_attempts")),
        sa.UniqueConstraint(
            "action_id",
            "attempt_number",
            name="uq_action_execution_attempts_action_number",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_action_execution_attempts_idempotency_key",
        ),
    )

    op.create_index(
        "ix_action_execution_attempts_one_active_action",
        "action_execution_attempts",
        ["action_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'dispatching')"),
    )
    op.create_index(
        "ix_action_execution_attempts_business_status_queued",
        "action_execution_attempts",
        ["business_id", "status", "queued_at", "id"],
    )
    op.create_index(
        "ix_action_execution_attempts_business_action_number",
        "action_execution_attempts",
        ["business_id", "action_id", "attempt_number"],
    )
    op.create_index(
        "ix_action_execution_attempts_business_status_lease",
        "action_execution_attempts",
        ["business_id", "status", "lease_expires_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_action_execution_attempts_business_status_lease",
        table_name="action_execution_attempts",
    )
    op.drop_index(
        "ix_action_execution_attempts_business_action_number",
        table_name="action_execution_attempts",
    )
    op.drop_index(
        "ix_action_execution_attempts_business_status_queued",
        table_name="action_execution_attempts",
    )
    op.drop_index(
        "ix_action_execution_attempts_one_active_action",
        table_name="action_execution_attempts",
        postgresql_where=sa.text("status IN ('queued', 'dispatching')"),
    )
    op.drop_table("action_execution_attempts")

    op.drop_constraint(
        op.f("ck_ai_actions_executable_state_requires_allowing_policy"),
        "ai_actions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_actions_valid_failure_state"),
        "ai_actions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_actions_valid_execution_timing_state"),
        "ai_actions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_actions_valid_status"),
        "ai_actions",
        type_="check",
    )

    op.create_check_constraint(
        op.f("ck_ai_actions_valid_status"),
        "ai_actions",
        "status IN ('proposed', 'pending_approval', 'ready', 'blocked', "
        "'rejected', 'expired', 'executing', 'succeeded', 'failed', 'canceled')",
    )
    op.create_check_constraint(
        op.f("ck_ai_actions_valid_execution_timing_state"),
        "ai_actions",
        "(status = 'executing' AND execution_started_at IS NOT NULL "
        "AND execution_completed_at IS NULL) OR "
        "(status IN ('succeeded', 'failed') AND execution_started_at IS NOT NULL "
        "AND execution_completed_at IS NOT NULL) OR "
        "(status NOT IN ('executing', 'succeeded', 'failed') "
        "AND execution_started_at IS NULL "
        "AND execution_completed_at IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_ai_actions_valid_failure_state"),
        "ai_actions",
        "(status = 'failed' AND failure_code IS NOT NULL) OR "
        "(status <> 'failed' AND failure_code IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_ai_actions_executable_state_requires_allowing_policy"),
        "ai_actions",
        "status NOT IN ('ready', 'executing', 'succeeded', 'failed') OR "
        "policy_decision IN ('allow', 'require_approval')",
    )
