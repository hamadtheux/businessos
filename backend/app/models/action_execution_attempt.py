from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ActionExecutionAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable dispatch intent and outcome for one governed AI action."""

    __tablename__ = "action_execution_attempts"

    __table_args__ = (
        ForeignKeyConstraint(
            ["action_id", "business_id"],
            ["ai_actions.id", "ai_actions.business_id"],
            name=(
                "fk_action_execution_attempts_action_business_ai_actions"
            ),
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "action_id",
            "attempt_number",
            name="uq_action_execution_attempts_action_number",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_action_execution_attempts_idempotency_key",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_action_execution_attempts_id_business",
        ),
        CheckConstraint(
            "attempt_number BETWEEN 1 AND 1000000",
            name="valid_attempt_number",
        ),
        CheckConstraint(
            "char_length(btrim(idempotency_key)) BETWEEN 1 AND 200",
            name="valid_idempotency_key",
        ),
        CheckConstraint(
            "char_length(btrim(action_type)) BETWEEN 1 AND 100",
            name="valid_action_type",
        ),
        CheckConstraint(
            "char_length(btrim(capability)) BETWEEN 1 AND 128",
            name="valid_capability",
        ),
        CheckConstraint(
            "status IN ("
            "'queued', 'dispatching', 'succeeded', "
            "'failed', 'uncertain', 'canceled'"
            ")",
            name="valid_status",
        ),
        CheckConstraint(
            "failure_code IS NULL OR "
            "char_length(btrim(failure_code)) BETWEEN 1 AND 64",
            name="valid_failure_code",
        ),
        CheckConstraint(
            "external_reference_id IS NULL OR "
            "char_length(btrim(external_reference_id)) BETWEEN 1 AND 255",
            name="valid_external_reference_id",
        ),
        CheckConstraint(
            "dispatch_started_at IS NULL OR dispatch_started_at >= queued_at",
            name="valid_dispatch_start",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= queued_at",
            name="valid_completion",
        ),
        CheckConstraint(
            "completed_at IS NULL OR dispatch_started_at IS NULL "
            "OR completed_at >= dispatch_started_at",
            name="completion_after_dispatch",
        ),
        CheckConstraint(
            "(lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_acquired_at >= queued_at "
            "AND lease_expires_at > lease_acquired_at)",
            name="consistent_lease",
        ),
        CheckConstraint(
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
            name="consistent_lifecycle",
        ),
        CheckConstraint(
            "(status = 'succeeded' AND failure_code IS NULL) OR "
            "(status IN ('failed', 'uncertain') AND failure_code IS NOT NULL) OR "
            "(status NOT IN ('succeeded', 'failed', 'uncertain') "
            "AND failure_code IS NULL)",
            name="consistent_failure",
        ),
        CheckConstraint(
            "external_reference_id IS NULL OR status = 'succeeded'",
            name="external_reference_only_on_success",
        ),
        Index(
            "ix_action_execution_attempts_one_active_action",
            "action_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'dispatching')"),
        ),
        Index(
            "ix_action_execution_attempts_business_status_queued",
            "business_id",
            "status",
            "queued_at",
            "id",
        ),
        Index(
            "ix_action_execution_attempts_business_action_number",
            "business_id",
            "action_id",
            "attempt_number",
        ),
        Index(
            "ix_action_execution_attempts_business_status_lease",
            "business_id",
            "status",
            "lease_expires_at",
            "id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )

    action_id: Mapped[UUID] = mapped_column(nullable=False)

    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)

    action_type: Mapped[str] = mapped_column(String(100), nullable=False)

    capability: Mapped[str] = mapped_column(String(128), nullable=False)

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="queued",
        server_default="queued",
    )

    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    dispatch_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    lease_acquired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    external_reference_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    failure_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
