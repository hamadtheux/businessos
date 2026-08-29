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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ProviderWriteAcceptance(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """
    Immutable evidence that one tenant-owned provider connection successfully
    accepted a governed external write.

    This is acceptance evidence, not delivery/business-outcome evidence.

    Examples:
    - Gmail accepted an outbound message.
    - WhatsApp Business accepted an outbound message.
    - Meta/Google accepted a governed campaign mutation.

    A row must only be created after the provider adapter returns a successful
    response and the corresponding ActionExecutionAttempt has been recorded as
    succeeded.

    It must never contain:
    - OAuth access/refresh tokens
    - API keys
    - authorization headers
    - raw provider responses
    - message bodies
    - customer contact details
    - unsanitized provider errors
    """

    __tablename__ = "provider_write_acceptances"

    __table_args__ = (
        # Proves the referenced integration connection belongs to the same
        # tenant as this acceptance evidence.
        ForeignKeyConstraint(
            ["integration_connection_id", "business_id"],
            [
                "integration_connections.id",
                "integration_connections.business_id",
            ],
            name=(
                "fk_provider_write_acceptance_connection"
            ),
            ondelete="CASCADE",
        ),
        # Proves the governed execution attempt belongs to the same tenant.
        ForeignKeyConstraint(
            ["action_execution_attempt_id", "business_id"],
            [
                "action_execution_attempts.id",
                "action_execution_attempts.business_id",
            ],
            name=(
                "fk_provider_write_acceptance_attempt"
            ),
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "action_execution_attempt_id",
            name="uq_provider_write_acceptances_attempt",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_provider_write_acceptances_id_business",
        ),
        CheckConstraint(
            "char_length(btrim(connector_type)) BETWEEN 1 AND 48",
            name="valid_connector_type",
        ),
        CheckConstraint(
            "char_length(btrim(action_type)) BETWEEN 1 AND 100",
            name="valid_action_type",
        ),
        Index(
            "ix_provider_write_acceptances_business_connection_accepted",
            "business_id",
            "integration_connection_id",
            "accepted_at",
            "id",
        ),
        Index(
            "ix_provider_write_acceptances_business_connector_accepted",
            "business_id",
            "connector_type",
            "accepted_at",
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

    integration_connection_id: Mapped[UUID] = mapped_column(
        nullable=False,
    )

    action_execution_attempt_id: Mapped[UUID] = mapped_column(
        nullable=False,
    )

    # Safe snapshots used for evidence queries and audit display.
    # The service creating this row must verify these values against the
    # execution context and tenant-owned IntegrationConnection.
    connector_type: Mapped[str] = mapped_column(
        String(48),
        nullable=False,
    )

    action_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
