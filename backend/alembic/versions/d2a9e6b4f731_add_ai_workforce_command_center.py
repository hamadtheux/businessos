"""add AI workforce and command center

Revision ID: d2a9e6b4f731
Revises: c1f8a7e4d263
Create Date: 2026-08-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d2a9e6b4f731"
down_revision: Union[str, Sequence[str], None] = "c1f8a7e4d263"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_configs",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("autonomy_mode", sa.String(length=16), server_default="manual", nullable=False),
        sa.Column("custom_instructions", sa.Text(), nullable=True),
        sa.Column("capability_config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('business_manager','cmo','sales','support','operations','analytics')", name=op.f("ck_ai_agent_configs_valid_role")),
        sa.CheckConstraint("char_length(btrim(display_name)) BETWEEN 1 AND 100", name=op.f("ck_ai_agent_configs_valid_display_name")),
        sa.CheckConstraint("autonomy_mode IN ('manual','supervised','autonomous')", name=op.f("ck_ai_agent_configs_valid_autonomy_mode")),
        sa.CheckConstraint("custom_instructions IS NULL OR char_length(custom_instructions) <= 2000", name=op.f("ck_ai_agent_configs_valid_custom_instructions")),
        sa.CheckConstraint("jsonb_typeof(capability_config) = 'array'", name=op.f("ck_ai_agent_configs_valid_capability_config")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_ai_agent_configs_business_id_businesses"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_agent_configs")),
        sa.UniqueConstraint("business_id", "role", name="uq_ai_agent_configs_business_role"),
    )
    op.create_index("ix_ai_agent_configs_business_enabled", "ai_agent_configs", ["business_id", "enabled", "role"], unique=False)

    op.create_table(
        "ai_commands",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("command_text", sa.Text(), nullable=False),
        sa.Column("resolved_role", sa.String(length=32), nullable=False),
        sa.Column("intent", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=True),
        sa.Column("route_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("resolved_role IN ('business_manager','cmo','sales','support','operations','analytics')", name=op.f("ck_ai_commands_valid_resolved_role")),
        sa.CheckConstraint("status IN ('queued','running','completed','needs_approval','failed','canceled')", name=op.f("ck_ai_commands_valid_status")),
        sa.CheckConstraint("char_length(btrim(command_text)) BETWEEN 1 AND 4000", name=op.f("ck_ai_commands_valid_command_text")),
        sa.CheckConstraint("char_length(btrim(intent)) BETWEEN 1 AND 64", name=op.f("ck_ai_commands_valid_intent")),
        sa.CheckConstraint("jsonb_typeof(route_metadata) = 'object'", name=op.f("ck_ai_commands_valid_route_metadata")),
        sa.CheckConstraint("summary IS NULL OR char_length(btrim(summary)) BETWEEN 1 AND 12000", name=op.f("ck_ai_commands_valid_summary")),
        sa.CheckConstraint("failure_code IS NULL OR char_length(btrim(failure_code)) BETWEEN 1 AND 64", name=op.f("ck_ai_commands_valid_failure_code")),
        sa.CheckConstraint("completed_at IS NULL OR completed_at >= created_at", name=op.f("ck_ai_commands_valid_completed_at")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_ai_commands_business_id_businesses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], name=op.f("fk_ai_commands_requested_by_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_commands")),
    )
    op.create_index("ix_ai_commands_business_created", "ai_commands", ["business_id", "created_at", "id"], unique=False)
    op.create_index("ix_ai_commands_business_status_created", "ai_commands", ["business_id", "status", "created_at", "id"], unique=False)
    op.create_index("ix_ai_commands_business_role_created", "ai_commands", ["business_id", "resolved_role", "created_at", "id"], unique=False)

    op.add_column("ai_agent_executions", sa.Column("command_id", sa.Uuid(), nullable=True))
    op.add_column("ai_agent_executions", sa.Column("parent_execution_id", sa.Uuid(), nullable=True))
    op.add_column("ai_agent_executions", sa.Column("delegation_role", sa.String(length=32), nullable=True))
    op.add_column("ai_agent_executions", sa.Column("delegation_sequence", sa.Integer(), server_default="0", nullable=False))
    op.add_column("ai_agent_executions", sa.Column("delegation_depth", sa.Integer(), server_default="0", nullable=False))
    op.create_foreign_key(op.f("fk_ai_agent_executions_command_id_ai_commands"), "ai_agent_executions", "ai_commands", ["command_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(op.f("fk_ai_agent_executions_parent_execution_id_ai_agent_executions"), "ai_agent_executions", "ai_agent_executions", ["parent_execution_id"], ["id"], ondelete="SET NULL")
    op.drop_constraint(op.f("ck_ai_agent_executions_valid_trigger_type"), "ai_agent_executions", type_="check")
    op.create_check_constraint(op.f("ck_ai_agent_executions_valid_trigger_type"), "ai_agent_executions", "trigger_type IN ('api','automation','command','system')")
    op.create_check_constraint(op.f("ck_ai_agent_executions_valid_delegation_sequence"), "ai_agent_executions", "delegation_sequence BETWEEN 0 AND 3")
    op.create_check_constraint(op.f("ck_ai_agent_executions_valid_delegation_depth"), "ai_agent_executions", "delegation_depth BETWEEN 0 AND 1")
    op.create_check_constraint(op.f("ck_ai_agent_executions_valid_delegation_linkage"), "ai_agent_executions", "(delegation_depth = 0 AND parent_execution_id IS NULL) OR (delegation_depth = 1 AND parent_execution_id IS NOT NULL AND delegation_role IS NOT NULL)")
    op.create_index("ix_ai_agent_executions_command_sequence", "ai_agent_executions", ["business_id", "command_id", "delegation_sequence"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ai_agent_executions_command_sequence", table_name="ai_agent_executions")
    op.drop_constraint(op.f("ck_ai_agent_executions_valid_delegation_linkage"), "ai_agent_executions", type_="check")
    op.drop_constraint(op.f("ck_ai_agent_executions_valid_delegation_depth"), "ai_agent_executions", type_="check")
    op.drop_constraint(op.f("ck_ai_agent_executions_valid_delegation_sequence"), "ai_agent_executions", type_="check")
    op.drop_constraint(op.f("ck_ai_agent_executions_valid_trigger_type"), "ai_agent_executions", type_="check")
    op.create_check_constraint(op.f("ck_ai_agent_executions_valid_trigger_type"), "ai_agent_executions", "trigger_type IN ('api','automation','system')")
    op.drop_constraint(op.f("fk_ai_agent_executions_parent_execution_id_ai_agent_executions"), "ai_agent_executions", type_="foreignkey")
    op.drop_constraint(op.f("fk_ai_agent_executions_command_id_ai_commands"), "ai_agent_executions", type_="foreignkey")
    op.drop_column("ai_agent_executions", "delegation_depth")
    op.drop_column("ai_agent_executions", "delegation_sequence")
    op.drop_column("ai_agent_executions", "delegation_role")
    op.drop_column("ai_agent_executions", "parent_execution_id")
    op.drop_column("ai_agent_executions", "command_id")
    op.drop_index("ix_ai_commands_business_role_created", table_name="ai_commands")
    op.drop_index("ix_ai_commands_business_status_created", table_name="ai_commands")
    op.drop_index("ix_ai_commands_business_created", table_name="ai_commands")
    op.drop_table("ai_commands")
    op.drop_index("ix_ai_agent_configs_business_enabled", table_name="ai_agent_configs")
    op.drop_table("ai_agent_configs")
