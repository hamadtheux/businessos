"""add customer agent orchestration

Revision ID: c13d4e5f6a70
Revises: a024bca1972e
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c13d4e5f6a70"
down_revision: str | Sequence[str] | None = "a024bca1972e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("integration_connection_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_integration_business",
        "conversations",
        "integration_connections",
        ["integration_connection_id", "business_id"],
        ["id", "business_id"],
    )

    op.create_unique_constraint(
        "uq_action_execution_attempts_id_business",
        "action_execution_attempts",
        ["id", "business_id"],
    )
    op.add_column(
        "conversation_messages",
        sa.Column("action_execution_attempt_id", sa.Uuid(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_conversation_messages_id_business",
        "conversation_messages",
        ["id", "business_id"],
    )
    op.create_unique_constraint(
        "uq_conversation_messages_business_attempt",
        "conversation_messages",
        ["business_id", "action_execution_attempt_id"],
    )
    op.create_foreign_key(
        "fk_conversation_messages_attempt_business",
        "conversation_messages",
        "action_execution_attempts",
        ["action_execution_attempt_id", "business_id"],
        ["id", "business_id"],
    )
    op.drop_constraint(
        op.f("ck_conversation_messages_valid_delivery_status"),
        "conversation_messages",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_conversation_messages_valid_delivery_status"),
        "conversation_messages",
        "delivery_status IN ('received','recorded','submitted','sent','delivered','read','failed')",
    )

    op.create_table(
        "customer_agent_responses",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("inbound_message_id", sa.Uuid(), nullable=False),
        sa.Column("ai_execution_id", sa.Uuid(), nullable=True),
        sa.Column("ai_action_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status", sa.String(length=32), server_default="processing", nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('processing','reply_proposed','approval_required','reply_submitted','handoff_requested','blocked','provider_unavailable')",
            name=op.f("ck_customer_agent_responses_valid_status"),
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 100",
            name=op.f("ck_customer_agent_responses_valid_attempt_count"),
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR char_length(btrim(failure_code)) BETWEEN 1 AND 64",
            name=op.f("ck_customer_agent_responses_valid_failure_code"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_customer_agent_responses_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inbound_message_id", "business_id"],
            ["conversation_messages.id", "conversation_messages.business_id"],
            name="fk_customer_agent_responses_message_business",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ai_execution_id", "business_id"],
            ["ai_agent_executions.id", "ai_agent_executions.business_id"],
            name="fk_customer_agent_responses_execution_business",
        ),
        sa.ForeignKeyConstraint(
            ["ai_action_id", "business_id"],
            ["ai_actions.id", "ai_actions.business_id"],
            name="fk_customer_agent_responses_action_business",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_agent_responses")),
        sa.UniqueConstraint(
            "id", "business_id", name="uq_customer_agent_responses_id_business"
        ),
        sa.UniqueConstraint(
            "business_id",
            "inbound_message_id",
            name="uq_customer_agent_responses_business_message",
        ),
    )
    op.create_index(
        "ix_customer_agent_responses_business_status",
        "customer_agent_responses",
        ["business_id", "status", "updated_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_customer_agent_responses_business_status",
        table_name="customer_agent_responses",
    )
    op.drop_table("customer_agent_responses")
    op.drop_constraint(
        op.f("ck_conversation_messages_valid_delivery_status"),
        "conversation_messages",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_conversation_messages_valid_delivery_status"),
        "conversation_messages",
        "delivery_status IN ('received','recorded','failed')",
    )
    op.drop_constraint(
        "fk_conversation_messages_attempt_business",
        "conversation_messages",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_conversation_messages_business_attempt",
        "conversation_messages",
        type_="unique",
    )
    op.drop_constraint(
        "uq_conversation_messages_id_business",
        "conversation_messages",
        type_="unique",
    )
    op.drop_column("conversation_messages", "action_execution_attempt_id")
    op.drop_constraint(
        "uq_action_execution_attempts_id_business",
        "action_execution_attempts",
        type_="unique",
    )
    op.drop_constraint(
        "fk_conversations_integration_business",
        "conversations",
        type_="foreignkey",
    )
    op.drop_column("conversations", "integration_connection_id")
