from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_conversations_customer_business"),
        UniqueConstraint("id", "business_id", name="uq_conversations_id_business"),
        UniqueConstraint("business_id", "channel", "external_reference", name="uq_conversations_business_channel_external"),
        CheckConstraint("channel IN ('website','whatsapp','email','facebook','instagram','manual','other')", name="valid_channel"),
        CheckConstraint("status IN ('open','escalated','resolved')", name="valid_status"),
        CheckConstraint("external_reference IS NULL OR char_length(btrim(external_reference)) BETWEEN 1 AND 255", name="valid_external_reference"),
        Index("ix_conversations_business_status_activity", "business_id", "status", "last_activity_at", "id"),
        Index("ix_conversations_business_customer", "business_id", "customer_id", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    customer_id: Mapped[UUID | None] = mapped_column(nullable=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", server_default="open")
    assigned_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class ConversationMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        ForeignKeyConstraint(["conversation_id", "business_id"], ["conversations.id", "conversations.business_id"], name="fk_conversation_messages_conversation_business", ondelete="CASCADE"),
        CheckConstraint("direction IN ('inbound','outbound','internal')", name="valid_direction"),
        CheckConstraint("sender_type IN ('customer','user','ai','system')", name="valid_sender_type"),
        CheckConstraint("delivery_status IN ('received','recorded','failed')", name="valid_delivery_status"),
        CheckConstraint("char_length(content) BETWEEN 1 AND 10000", name="valid_content"),
        CheckConstraint("external_reference IS NULL OR char_length(btrim(external_reference)) BETWEEN 1 AND 255", name="valid_external_reference"),
        Index("ix_conversation_messages_business_conversation_sent", "business_id", "conversation_id", "sent_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(16), nullable=False, default="recorded", server_default="recorded")
