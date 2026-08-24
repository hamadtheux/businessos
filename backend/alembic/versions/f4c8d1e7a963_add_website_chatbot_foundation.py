"""add website chatbot foundation

Revision ID: f4c8d1e7a963
Revises: e3b7c9a4d612
Create Date: 2026-08-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f4c8d1e7a963"
down_revision: str | Sequence[str] | None = "e3b7c9a4d612"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chatbot_configs",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("widget_public_id", sa.String(96), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("welcome_message", sa.String(500), nullable=False),
        sa.Column("placeholder_text", sa.String(160), nullable=False),
        sa.Column("tone", sa.String(24), server_default="friendly", nullable=False),
        sa.Column("theme", sa.String(16), server_default="light", nullable=False),
        sa.Column("position", sa.String(24), server_default="bottom_right", nullable=False),
        sa.Column("launcher_style", sa.String(16), server_default="bubble", nullable=False),
        sa.Column("allowed_capabilities", postgresql.ARRAY(sa.String(64)), server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("allowed_domains", postgresql.ARRAY(sa.String(253)), server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("privacy_policy_url", sa.String(2048), nullable=True),
        sa.Column("consent_text", sa.Text(), nullable=True),
        sa.Column("require_lead_consent", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("default_locale", sa.String(16), server_default="en", nullable=False),
        sa.Column("border_radius", sa.Integer(), server_default="18", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("char_length(widget_public_id) BETWEEN 40 AND 96", name=op.f("ck_chatbot_configs_valid_widget_public_id")),
        sa.CheckConstraint("char_length(btrim(display_name)) BETWEEN 1 AND 80", name=op.f("ck_chatbot_configs_valid_display_name")),
        sa.CheckConstraint("char_length(btrim(welcome_message)) BETWEEN 1 AND 500", name=op.f("ck_chatbot_configs_valid_welcome_message")),
        sa.CheckConstraint("char_length(btrim(placeholder_text)) BETWEEN 1 AND 160", name=op.f("ck_chatbot_configs_valid_placeholder_text")),
        sa.CheckConstraint("tone IN ('friendly','professional','concise','warm')", name=op.f("ck_chatbot_configs_valid_tone")),
        sa.CheckConstraint("theme IN ('light','dark','auto')", name=op.f("ck_chatbot_configs_valid_theme")),
        sa.CheckConstraint("position IN ('bottom_right','bottom_left')", name=op.f("ck_chatbot_configs_valid_position")),
        sa.CheckConstraint("launcher_style IN ('bubble','pill')", name=op.f("ck_chatbot_configs_valid_launcher_style")),
        sa.CheckConstraint("cardinality(allowed_capabilities) <= 8", name=op.f("ck_chatbot_configs_valid_capability_count")),
        sa.CheckConstraint("cardinality(allowed_domains) <= 50", name=op.f("ck_chatbot_configs_valid_domain_count")),
        sa.CheckConstraint("default_locale ~ '^[a-z]{2,3}(-[A-Z]{2})?$'", name=op.f("ck_chatbot_configs_valid_default_locale")),
        sa.CheckConstraint("border_radius BETWEEN 0 AND 28", name=op.f("ck_chatbot_configs_valid_border_radius")),
        sa.CheckConstraint("privacy_policy_url IS NULL OR char_length(privacy_policy_url) <= 2048", name=op.f("ck_chatbot_configs_valid_privacy_policy_url")),
        sa.CheckConstraint("consent_text IS NULL OR char_length(consent_text) <= 1000", name=op.f("ck_chatbot_configs_valid_consent_text")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_chatbot_configs_business_id_businesses"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chatbot_configs")),
        sa.UniqueConstraint("id", "business_id", name="uq_chatbot_configs_id_business"),
        sa.UniqueConstraint("business_id", name="uq_chatbot_configs_business"),
        sa.UniqueConstraint("widget_public_id", name="uq_chatbot_configs_widget_public_id"),
    )
    op.create_index("ix_chatbot_configs_business_enabled", "chatbot_configs", ["business_id", "enabled", "id"])

    op.create_table(
        "chatbot_sessions",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("chatbot_config_id", sa.Uuid(), nullable=False),
        sa.Column("session_token_hash", sa.String(64), nullable=False),
        sa.Column("origin_host", sa.String(253), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(24), server_default="active", nullable=False),
        sa.Column("locale", sa.String(16), server_default="en", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handoff_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ai_response_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("response_duration_ms_total", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("order_lookup_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("booking_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("order_lookup_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("appointment_booked_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("product_recommendation_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ai_failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("char_length(session_token_hash) = 64", name=op.f("ck_chatbot_sessions_valid_session_token_hash")),
        sa.CheckConstraint("status IN ('active','handoff_requested','closed','expired')", name=op.f("ck_chatbot_sessions_valid_status")),
        sa.CheckConstraint("char_length(origin_host) BETWEEN 1 AND 253", name=op.f("ck_chatbot_sessions_valid_origin_host")),
        sa.CheckConstraint("locale ~ '^[a-z]{2,3}(-[A-Z]{2})?$'", name=op.f("ck_chatbot_sessions_valid_locale")),
        sa.CheckConstraint("expires_at > started_at", name=op.f("ck_chatbot_sessions_valid_expiry")),
        sa.CheckConstraint("last_activity_at >= started_at", name=op.f("ck_chatbot_sessions_valid_activity")),
        sa.CheckConstraint("message_count >= 0 AND ai_response_count >= 0", name=op.f("ck_chatbot_sessions_valid_message_counts")),
        sa.CheckConstraint("response_duration_ms_total >= 0", name=op.f("ck_chatbot_sessions_valid_response_duration")),
        sa.CheckConstraint("order_lookup_attempts BETWEEN 0 AND 10", name=op.f("ck_chatbot_sessions_valid_order_attempts")),
        sa.CheckConstraint("booking_attempts BETWEEN 0 AND 10", name=op.f("ck_chatbot_sessions_valid_booking_attempts")),
        sa.CheckConstraint("order_lookup_count >= 0 AND appointment_booked_count >= 0 AND ai_failure_count >= 0", name=op.f("ck_chatbot_sessions_valid_metric_counts")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_chatbot_sessions_business_id_businesses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chatbot_config_id", "business_id"], ["chatbot_configs.id", "chatbot_configs.business_id"], name="fk_chatbot_sessions_config_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_chatbot_sessions_customer_business"),
        sa.ForeignKeyConstraint(["conversation_id", "business_id"], ["conversations.id", "conversations.business_id"], name="fk_chatbot_sessions_conversation_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chatbot_sessions")),
        sa.UniqueConstraint("session_token_hash", name="uq_chatbot_sessions_token_hash"),
    )
    op.create_index("ix_chatbot_sessions_business_started", "chatbot_sessions", ["business_id", "started_at", "id"])
    op.create_index("ix_chatbot_sessions_config_activity", "chatbot_sessions", ["chatbot_config_id", "last_activity_at", "id"])
    op.create_index("ix_chatbot_sessions_expiry", "chatbot_sessions", ["status", "expires_at", "id"])

    op.drop_constraint(op.f("ck_ai_agent_executions_valid_trigger_type"), "ai_agent_executions", type_="check")
    op.create_check_constraint(
        op.f("ck_ai_agent_executions_valid_trigger_type"),
        "ai_agent_executions",
        "trigger_type IN ('api','automation','command','website_widget','system')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_ai_agent_executions_valid_trigger_type"), "ai_agent_executions", type_="check")
    op.create_check_constraint(
        op.f("ck_ai_agent_executions_valid_trigger_type"),
        "ai_agent_executions",
        "trigger_type IN ('api','automation','command','system')",
    )
    op.drop_index("ix_chatbot_sessions_expiry", table_name="chatbot_sessions")
    op.drop_index("ix_chatbot_sessions_config_activity", table_name="chatbot_sessions")
    op.drop_index("ix_chatbot_sessions_business_started", table_name="chatbot_sessions")
    op.drop_table("chatbot_sessions")
    op.drop_index("ix_chatbot_configs_business_enabled", table_name="chatbot_configs")
    op.drop_table("chatbot_configs")
