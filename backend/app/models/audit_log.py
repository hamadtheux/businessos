from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class AuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "business_audit_log"
    __table_args__ = (
        CheckConstraint("event_type ~ '^[a-z][a-z0-9_.]{0,79}$'", name="valid_event_type"),
        CheckConstraint("entity_type ~ '^[a-z][a-z0-9_]{0,47}$'", name="valid_entity_type"),
        CheckConstraint("actor_type IN ('user','ai','system')", name="valid_actor_type"),
        CheckConstraint("status IN ('completed','failed','pending')", name="valid_status"),
        CheckConstraint("char_length(summary) BETWEEN 1 AND 1000", name="valid_summary"),
        CheckConstraint("before_value IS NULL OR char_length(before_value) <= 500", name="valid_before_value"),
        CheckConstraint("after_value IS NULL OR char_length(after_value) <= 500", name="valid_after_value"),
        Index("ix_business_audit_log_business_created", "business_id", "created_at", "id"),
        Index("ix_business_audit_log_business_event", "business_id", "event_type", "created_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    before_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    after_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed", server_default="completed")
