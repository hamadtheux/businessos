from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.business import Business


class BusinessMemory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One durable learned memory owned by exactly one business."""

    __tablename__ = "business_memories"
    __table_args__ = (
        CheckConstraint(
            "memory_type IN ("
            "'episodic', "
            "'semantic', "
            "'procedural', "
            "'decision', "
            "'customer', "
            "'ai_learning'"
            ")",
            name="valid_memory_type",
        ),
        CheckConstraint(
            "status IN ('active', 'superseded', 'archived')",
            name="valid_status",
        ),
        CheckConstraint(
            "importance BETWEEN 1 AND 5",
            name="valid_importance",
        ),
        CheckConstraint(
            "confidence BETWEEN 0.000 AND 1.000",
            name="valid_confidence",
        ),
        CheckConstraint(
            "source_type IN ('manual', 'system')",
            name="valid_source_type",
        ),
        CheckConstraint(
            "char_length(btrim(content)) BETWEEN 1 AND 10000",
            name="valid_content",
        ),
        CheckConstraint(
            "char_length(content_hash) = 64 "
            "AND content_hash = lower(content_hash) "
            "AND content_hash ~ '^[0-9a-f]{64}$'",
            name="valid_content_hash",
        ),
        CheckConstraint(
            "superseded_by_memory_id IS NULL "
            "OR superseded_by_memory_id <> id",
            name="not_self_superseded",
        ),
        Index(
            "ix_business_memories_business_status_created",
            "business_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_business_memories_business_type_created",
            "business_id",
            "memory_type",
            "created_at",
            "id",
        ),
        Index(
            "ix_business_memories_business_content_hash",
            "business_id",
            "content_hash",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_business_memories_id_business",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )

    memory_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
        server_default="active",
    )

    importance: Mapped[int] = mapped_column(
        nullable=False,
        default=3,
        server_default="3",
    )

    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3),
        nullable=False,
        default=Decimal("1.000"),
        server_default="1.000",
    )

    source_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="manual",
        server_default="manual",
    )

    source_reference: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )

    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_reinforced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    superseded_by_memory_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "business_memories.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    business: Mapped["Business"] = relationship(
        back_populates="memories",
        lazy="select",
    )

    superseded_by: Mapped["BusinessMemory | None"] = relationship(
        "BusinessMemory",
        remote_side="BusinessMemory.id",
        foreign_keys=[superseded_by_memory_id],
        lazy="select",
    )
