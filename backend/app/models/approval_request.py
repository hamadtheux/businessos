from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ApprovalRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One durable tenant-scoped decision for an AI action or workflow gate."""

    __tablename__ = "approval_requests"

    __table_args__ = (
        UniqueConstraint(
            "id", "business_id", name="uq_approval_requests_id_business"
        ),
        ForeignKeyConstraint(
            ["action_id", "business_id"],
            ["ai_actions.id", "ai_actions.business_id"],
            name="fk_approval_requests_action_business_ai_actions",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workflow_node_run_id", "business_id"],
            ["automation_node_runs.id", "automation_node_runs.business_id"],
            name="fk_approval_requests_workflow_node_run_business",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "(action_id IS NOT NULL)::integer + "
            "(workflow_node_run_id IS NOT NULL)::integer = 1",
            name="exactly_one_target",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'canceled')",
            name="valid_status",
        ),
        CheckConstraint(
            "char_length(btrim(reason_code)) BETWEEN 1 AND 64",
            name="valid_reason_code",
        ),
        CheckConstraint(
            "decision_note IS NULL OR "
            "char_length(btrim(decision_note)) BETWEEN 1 AND 2000",
            name="valid_decision_note",
        ),
        CheckConstraint(
            "action_type_snapshot IS NULL OR "
            "char_length(btrim(action_type_snapshot)) BETWEEN 1 AND 100",
            name="valid_action_type_snapshot",
        ),
        CheckConstraint(
            "authorized_payload_hash_snapshot IS NULL OR ("
            "char_length(authorized_payload_hash_snapshot) = 64 AND "
            "authorized_payload_hash_snapshot ~ '^[0-9a-f]{64}$'"
            ")",
            name="valid_authorized_payload_hash_snapshot",
        ),
        CheckConstraint(
            "(action_id IS NULL AND action_type_snapshot IS NULL AND "
            "authorized_payload_hash_snapshot IS NULL) OR "
            "(action_id IS NOT NULL AND action_type_snapshot IS NOT NULL AND "
            "authorized_payload_hash_snapshot IS NOT NULL)",
            name="consistent_action_authorization_snapshot",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > requested_at",
            name="valid_expiration",
        ),
        CheckConstraint(
            "decided_at IS NULL OR decided_at >= requested_at",
            name="valid_decision_time",
        ),
        CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL "
            "AND decided_by_user_id IS NULL AND decision_actor_id IS NULL) "
            "OR (status IN ('approved', 'rejected') AND decided_at IS NOT NULL "
            "AND decision_actor_id IS NOT NULL) "
            "OR (status IN ('expired', 'canceled') AND decided_at IS NOT NULL)",
            name="consistent_lifecycle",
        ),
        Index(
            "ix_approval_requests_one_pending_action",
            "action_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_approval_requests_one_pending_workflow_node",
            "workflow_node_run_id",
            unique=True,
            postgresql_where=text("status = 'pending' AND workflow_node_run_id IS NOT NULL"),
        ),
        Index(
            "ix_approval_requests_business_status_created",
            "business_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_approval_requests_business_action",
            "business_id",
            "action_id",
            "created_at",
        ),
        Index(
            "ix_approval_requests_decider_decided",
            "decided_by_user_id",
            "decided_at",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )

    action_id: Mapped[UUID | None] = mapped_column(nullable=True)

    workflow_node_run_id: Mapped[UUID | None] = mapped_column(nullable=True)

    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)

    # Immutable authorization snapshot for an AIAction approval. Workflow
    # approvals leave both fields NULL. The dispatch boundary compares these
    # values with the current, registry-validated action before any attempt is
    # allowed to reach a connector.
    action_type_snapshot: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    authorized_payload_hash_snapshot: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Immutable actor snapshot preserves audit consistency if the User row is
    # later deleted and the live FK is set to NULL.
    decision_actor_id: Mapped[UUID | None] = mapped_column(nullable=True)

    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
