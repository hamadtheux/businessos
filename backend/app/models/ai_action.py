from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
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
    from app.models.ai_agent_execution import AIAgentExecution
    from app.models.business import Business


class AIAction(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """
    Durable representation of one action proposed by an AI agent.

    An AIAction is NOT permission to execute anything.

    Lifecycle:

        proposed
            ↓
        policy evaluation
            ↓
        ┌───────────────┬────────────────────┬─────────────┐
        │               │                    │             │
       ready      pending_approval         blocked      canceled
        │               │
        │          approval decision
        │          ↓              ↓
        │        ready        rejected/expired
        │
        ↓
      queued
        ↓
      executing
        ↓
    succeeded / failed / uncertain

    `ready` means the action is permitted to enter the future Action
    Execution Engine. It may become ready either because:

    - policy explicitly allowed automatic execution, or
    - a required human approval was granted.

    This model stores only safe, structured action/audit information.

    It must never contain:
    - integration access tokens
    - OAuth refresh tokens
    - API keys
    - authorization headers
    - raw provider responses
    - rendered Business Brain/context
    - hidden chain-of-thought
    - unsanitized external-service errors

    No business side effect is performed by this model.
    """

    __tablename__ = "ai_actions"

    __table_args__ = (
        ForeignKeyConstraint(
            ["execution_id", "business_id"],
            ["ai_agent_executions.id", "ai_agent_executions.business_id"],
            name="fk_ai_actions_execution_business",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "execution_id",
            "proposal_index",
            name="uq_ai_actions_execution_proposal",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_ai_actions_id_business",
        ),
        CheckConstraint(
            "proposal_index >= 0 AND proposal_index < 20",
            name="valid_proposal_index",
        ),
        CheckConstraint(
            "char_length(btrim(action_type)) BETWEEN 1 AND 100",
            name="valid_action_type",
        ),
        CheckConstraint(
            "char_length(btrim(description)) BETWEEN 1 AND 2000",
            name="valid_description",
        ),
        CheckConstraint(
            "risk_level IN ("
            "'low', "
            "'medium', "
            "'high', "
            "'critical'"
            ")",
            name="valid_risk_level",
        ),
        CheckConstraint(
            "status IN ("
            "'proposed', "
            "'pending_approval', "
            "'ready', "
            "'queued', "
            "'blocked', "
            "'rejected', "
            "'expired', "
            "'executing', "
            "'succeeded', "
            "'failed', "
            "'uncertain', "
            "'canceled'"
            ")",
            name="valid_status",
        ),
        CheckConstraint(
            "jsonb_typeof(action_payload) = 'object'",
            name="valid_action_payload",
        ),
        CheckConstraint(
            "policy_decision IS NULL "
            "OR policy_decision IN ("
            "'allow', "
            "'require_approval', "
            "'block'"
            ")",
            name="valid_policy_decision",
        ),
        CheckConstraint(
            "("
            "policy_decision IS NULL "
            "AND policy_evaluated_at IS NULL"
            ") OR ("
            "policy_decision IS NOT NULL "
            "AND policy_evaluated_at IS NOT NULL"
            ")",
            name="consistent_policy_evaluation",
        ),
        CheckConstraint(
            "policy_reason_code IS NULL "
            "OR char_length(btrim(policy_reason_code)) BETWEEN 1 AND 64",
            name="valid_policy_reason_code",
        ),
        CheckConstraint(
            "authorized_payload_hash IS NULL OR "
            "(char_length(authorized_payload_hash) = 64 AND "
            "authorized_payload_hash ~ '^[0-9a-f]{64}$')",
            name="valid_authorized_payload_hash",
        ),
        CheckConstraint(
            "result_summary IS NULL "
            "OR char_length(btrim(result_summary)) BETWEEN 1 AND 2000",
            name="valid_result_summary",
        ),
        CheckConstraint(
            "failure_code IS NULL "
            "OR char_length(btrim(failure_code)) BETWEEN 1 AND 64",
            name="valid_failure_code",
        ),
        CheckConstraint(
            "external_reference_id IS NULL "
            "OR char_length(btrim(external_reference_id)) BETWEEN 1 AND 255",
            name="valid_external_reference_id",
        ),
        CheckConstraint(
            "execution_completed_at IS NULL "
            "OR execution_started_at IS NOT NULL",
            name="completion_requires_start",
        ),
        CheckConstraint(
            "execution_completed_at IS NULL "
            "OR execution_completed_at >= execution_started_at",
            name="valid_execution_completion_time",
        ),
        CheckConstraint(
            "("
            "status = 'executing' "
            "AND execution_started_at IS NOT NULL "
            "AND execution_completed_at IS NULL"
            ") OR ("
            "status IN ('succeeded', 'failed', 'uncertain') "
            "AND execution_started_at IS NOT NULL "
            "AND execution_completed_at IS NOT NULL"
            ") OR ("
            "status NOT IN ('executing', 'succeeded', 'failed', 'uncertain') "
            "AND execution_started_at IS NULL "
            "AND execution_completed_at IS NULL"
            ")",
            name="valid_execution_timing_state",
        ),
        CheckConstraint(
            "("
            "status IN ('failed', 'uncertain') "
            "AND failure_code IS NOT NULL"
            ") OR ("
            "status NOT IN ('failed', 'uncertain') "
            "AND failure_code IS NULL"
            ")",
            name="valid_failure_state",
        ),
        CheckConstraint(
            "status <> 'proposed' "
            "OR policy_decision IS NULL",
            name="proposed_has_no_policy_decision",
        ),
        CheckConstraint(
            "status <> 'pending_approval' "
            "OR policy_decision = 'require_approval'",
            name="pending_requires_approval_policy",
        ),
        CheckConstraint(
            "status <> 'blocked' "
            "OR policy_decision = 'block'",
            name="blocked_requires_block_policy",
        ),
        CheckConstraint(
            "status NOT IN ('rejected', 'expired') "
            "OR policy_decision = 'require_approval'",
            name="approval_terminal_requires_approval_policy",
        ),
        CheckConstraint(
            "status NOT IN ("
            "'ready', "
            "'queued', "
            "'executing', "
            "'succeeded', "
            "'failed', "
            "'uncertain'"
            ") "
            "OR policy_decision IN ('allow', 'require_approval')",
            name="executable_state_requires_allowing_policy",
        ),
        Index(
            "ix_ai_actions_business_created",
            "business_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_ai_actions_business_status_created",
            "business_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_ai_actions_business_type_created",
            "business_id",
            "action_type",
            "created_at",
            "id",
        ),
        Index(
            "ix_ai_actions_execution_created",
            "execution_id",
            "created_at",
            "id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "businesses.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    execution_id: Mapped[UUID] = mapped_column(nullable=False)

    # Stable position inside the originating execution's proposed_actions
    # array. Combined with execution_id, this prevents duplicate
    # materialization of the same AI proposal.
    proposal_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    action_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    risk_level: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    # This preserves what the AI proposed.
    # The Policy Engine remains authoritative and may require approval even
    # when the AI marked the proposal as not requiring it.
    proposed_requires_approval: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="proposed",
        server_default="proposed",
    )

    # Future action-specific services will populate only validated,
    # server-normalized execution parameters here.
    #
    # Never copy arbitrary provider payloads into this field.
    action_payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    # Authoritative outcome from the future Policy Engine.
    policy_decision: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    # Safe machine-readable reason only, e.g.
    # "critical_action", "budget_limit", "human_approval_required".
    policy_reason_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    authorized_payload_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    policy_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    execution_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    execution_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Safe user-visible result summary only.
    # Never store raw connector/provider responses here.
    result_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Safe fixed taxonomy only.
    # Never persist raw connector exception text.
    failure_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Optional safe external identifier such as a message ID, post ID,
    # campaign ID, or order ID.
    #
    # Never store access tokens or credentials here.
    external_reference_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    business: Mapped["Business"] = relationship(
        lazy="select",
    )

    execution: Mapped["AIAgentExecution"] = relationship(
        lazy="select",
        overlaps="business",
    )
