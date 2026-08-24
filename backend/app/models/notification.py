from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("category ~ '^[a-z][a-z0-9_]{0,47}$'", name="valid_category"),
        CheckConstraint("priority IN ('low','medium','high')", name="valid_priority"),
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 180", name="valid_title"),
        CheckConstraint("char_length(message) BETWEEN 1 AND 1000", name="valid_message"),
        CheckConstraint("related_entity_type IS NULL OR related_entity_type ~ '^[a-z][a-z0-9_]{0,47}$'", name="valid_related_entity_type"),
        Index("ix_notifications_business_user_read_created", "business_id", "recipient_user_id", "read", "created_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    recipient_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium", server_default="medium")
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    related_entity_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    related_entity_id: Mapped[UUID | None] = mapped_column(nullable=True)
