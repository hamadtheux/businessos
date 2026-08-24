from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.business import Business
    from app.models.user import User


class AIAgentExecution(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """
    Durable audit record for one AI employee execution.

    This record intentionally stores only the information needed for
    traceability, approvals, analytics, debugging, and usage accounting.

    It must never contain:
    - OpenAI or other provider API keys
    - authorization headers
    - rendered Business Brain/context payloads
    - raw database records
    - hidden chain-of-thought
    - unsanitized provider error bodies
    """

    __tablename__ = "ai_agent_executions"

    __table_args__ = (
        UniqueConstraint(
            "id", "business_id", name="uq_ai_agent_executions_id_business"
        ),
        CheckConstraint(
            "role IN ("
            "'business_manager', "
            "'cmo', "
            "'sales', "
            "'support', "
            "'operations', "
            "'analytics'"
            ")",
            name="valid_role",
        ),
        CheckConstraint(
            "status IN ("
            "'running', "
            "'completed', "
            "'needs_approval', "
            "'blocked', "
            "'failed'"
            ")",
            name="valid_status",
        ),
        CheckConstraint(
            "trigger_type IN ("
            "'api', "
            "'automation', "
            "'command', "
            "'website_widget', "
            "'system'"
            ")",
            name="valid_trigger_type",
        ),
        CheckConstraint(
            "char_length(btrim(task)) BETWEEN 1 AND 4000",
            name="valid_task",
        ),
        CheckConstraint(
            "char_length(btrim(provider_name)) BETWEEN 1 AND 64",
            name="valid_provider_name",
        ),
        CheckConstraint(
            "char_length(btrim(model_name)) BETWEEN 1 AND 128",
            name="valid_model_name",
        ),
        CheckConstraint(
            "context_revision IS NULL OR ("
            "char_length(context_revision) = 64 "
            "AND context_revision = lower(context_revision) "
            "AND context_revision ~ '^[0-9a-f]{64}$'"
            ")",
            name="valid_context_revision",
        ),
        CheckConstraint(
            "context_source_count >= 0",
            name="valid_context_source_count",
        ),
        CheckConstraint(
            "business_brain_source_count >= 0",
            name="valid_business_brain_source_count",
        ),
        CheckConstraint(
            "memory_source_count >= 0",
            name="valid_memory_source_count",
        ),
        CheckConstraint(
            "business_brain_source_count + memory_source_count "
            "= context_source_count",
            name="consistent_context_source_counts",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="valid_duration_ms",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="valid_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="valid_output_tokens",
        ),
        CheckConstraint(
            "estimated_cost_usd IS NULL "
            "OR estimated_cost_usd >= 0",
            name="valid_estimated_cost_usd",
        ),
        CheckConstraint(
            "output_summary IS NULL "
            "OR char_length(btrim(output_summary)) BETWEEN 1 AND 12000",
            name="valid_output_summary",
        ),
        CheckConstraint(
            "failure_code IS NULL "
            "OR char_length(btrim(failure_code)) BETWEEN 1 AND 64",
            name="valid_failure_code",
        ),
        CheckConstraint(
            "provider_request_id IS NULL "
            "OR char_length(btrim(provider_request_id)) BETWEEN 1 AND 255",
            name="valid_provider_request_id",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name="valid_completed_at",
        ),
        CheckConstraint(
            "delegation_sequence BETWEEN 0 AND 3",
            name="valid_delegation_sequence",
        ),
        CheckConstraint(
            "delegation_depth BETWEEN 0 AND 1",
            name="valid_delegation_depth",
        ),
        CheckConstraint(
            "(delegation_depth = 0 AND parent_execution_id IS NULL) OR "
            "(delegation_depth = 1 AND parent_execution_id IS NOT NULL "
            "AND delegation_role IS NOT NULL)",
            name="valid_delegation_linkage",
        ),
        CheckConstraint(
            "(status = 'running' AND completed_at IS NULL) "
            "OR "
            "(status <> 'running' AND completed_at IS NOT NULL)",
            name="valid_completion_state",
        ),
        Index(
            "ix_ai_agent_executions_business_created",
            "business_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_ai_agent_executions_business_role_created",
            "business_id",
            "role",
            "created_at",
            "id",
        ),
        Index(
            "ix_ai_agent_executions_business_status_created",
            "business_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_ai_agent_executions_requester_created",
            "requested_by_user_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_ai_agent_executions_command_sequence",
            "business_id",
            "command_id",
            "delegation_sequence",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "businesses.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    command_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_commands.id", ondelete="SET NULL"), nullable=True
    )

    parent_execution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_agent_executions.id", ondelete="SET NULL"), nullable=True
    )

    delegation_role: Mapped[str | None] = mapped_column(String(32), nullable=True)

    delegation_sequence: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )

    delegation_depth: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )

    role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    trigger_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="api",
        server_default="api",
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="running",
        server_default="running",
    )

    task: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    provider_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    model_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    # Nullable while an execution is still running.
    # It is populated after trusted AI context has been assembled.
    context_revision: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    context_source_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )

    business_brain_source_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )

    memory_source_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )

    output_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    recommendations: Mapped[list[object]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    proposed_actions: Mapped[list[object]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    failure_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    provider_request_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    duration_ms: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    input_tokens: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    output_tokens: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 6),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    business: Mapped["Business"] = relationship(
        lazy="select",
    )

    requested_by_user: Mapped["User | None"] = relationship(
        lazy="select",
    )
