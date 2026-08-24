from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


_ROLES_SQL = "('business_manager','cmo','sales','support','operations','analytics')"


class AIAgentConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_agent_configs"
    __table_args__ = (
        UniqueConstraint("business_id", "role", name="uq_ai_agent_configs_business_role"),
        CheckConstraint(f"role IN {_ROLES_SQL}", name="valid_role"),
        CheckConstraint("char_length(btrim(display_name)) BETWEEN 1 AND 100", name="valid_display_name"),
        CheckConstraint("autonomy_mode IN ('manual','supervised','autonomous')", name="valid_autonomy_mode"),
        CheckConstraint("custom_instructions IS NULL OR char_length(custom_instructions) <= 2000", name="valid_custom_instructions"),
        CheckConstraint("jsonb_typeof(capability_config) = 'array'", name="valid_capability_config"),
        Index("ix_ai_agent_configs_business_enabled", "business_id", "enabled", "role"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    autonomy_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", server_default="manual")
    custom_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    capability_config: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))


class AICommand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_commands"
    __table_args__ = (
        CheckConstraint(f"resolved_role IN {_ROLES_SQL}", name="valid_resolved_role"),
        CheckConstraint("status IN ('queued','running','completed','needs_approval','failed','canceled')", name="valid_status"),
        CheckConstraint("char_length(btrim(command_text)) BETWEEN 1 AND 4000", name="valid_command_text"),
        CheckConstraint("char_length(btrim(intent)) BETWEEN 1 AND 64", name="valid_intent"),
        CheckConstraint("jsonb_typeof(route_metadata) = 'object'", name="valid_route_metadata"),
        CheckConstraint("summary IS NULL OR char_length(btrim(summary)) BETWEEN 1 AND 12000", name="valid_summary"),
        CheckConstraint("failure_code IS NULL OR char_length(btrim(failure_code)) BETWEEN 1 AND 64", name="valid_failure_code"),
        CheckConstraint("completed_at IS NULL OR completed_at >= created_at", name="valid_completed_at"),
        Index("ix_ai_commands_business_created", "business_id", "created_at", "id"),
        Index("ix_ai_commands_business_status_created", "business_id", "status", "created_at", "id"),
        Index("ix_ai_commands_business_role_created", "business_id", "resolved_role", "created_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    requested_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    command_text: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_role: Mapped[str] = mapped_column(String(32), nullable=False)
    intent: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", server_default="queued")
    # Informational root-execution pointer. The database-enforced ownership
    # direction is AIAgentExecution.command_id, avoiding a circular FK graph.
    execution_id: Mapped[UUID | None] = mapped_column(nullable=True)
    route_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
