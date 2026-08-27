from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_conversations_customer_business"),
        ForeignKeyConstraint(["integration_connection_id", "business_id"], ["integration_connections.id", "integration_connections.business_id"], name="fk_conversations_integration_business"),
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
    integration_connection_id: Mapped[UUID | None] = mapped_column(nullable=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", server_default="open")
    assigned_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class ConversationMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        ForeignKeyConstraint(["conversation_id", "business_id"], ["conversations.id", "conversations.business_id"], name="fk_conversation_messages_conversation_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["action_execution_attempt_id", "business_id"], ["action_execution_attempts.id", "action_execution_attempts.business_id"], name="fk_conversation_messages_attempt_business"),
        CheckConstraint("direction IN ('inbound','outbound','internal')", name="valid_direction"),
        CheckConstraint("sender_type IN ('customer','user','ai','system')", name="valid_sender_type"),
        CheckConstraint("delivery_status IN ('received','recorded','submitted','sent','delivered','read','failed')", name="valid_delivery_status"),
        CheckConstraint("char_length(content) BETWEEN 1 AND 10000", name="valid_content"),
        CheckConstraint("external_reference IS NULL OR char_length(btrim(external_reference)) BETWEEN 1 AND 255", name="valid_external_reference"),
        UniqueConstraint("id", "business_id", name="uq_conversation_messages_id_business"),
        UniqueConstraint("business_id", "action_execution_attempt_id", name="uq_conversation_messages_business_attempt"),
        Index("ix_conversation_messages_business_conversation_sent", "business_id", "conversation_id", "sent_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    action_execution_attempt_id: Mapped[UUID | None] = mapped_column(nullable=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(16), nullable=False, default="recorded", server_default="recorded")


class CustomerAgentResponse(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Idempotent orchestration state for one verified inbound message."""

    __tablename__ = "customer_agent_responses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["inbound_message_id", "business_id"],
            ["conversation_messages.id", "conversation_messages.business_id"],
            name="fk_customer_agent_responses_message_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["ai_execution_id", "business_id"],
            ["ai_agent_executions.id", "ai_agent_executions.business_id"],
            name="fk_customer_agent_responses_execution_business",
        ),
        ForeignKeyConstraint(
            ["ai_action_id", "business_id"],
            ["ai_actions.id", "ai_actions.business_id"],
            name="fk_customer_agent_responses_action_business",
        ),
        CheckConstraint(
            "status IN ('processing','reply_proposed','approval_required','reply_submitted','handoff_requested','blocked','provider_unavailable')",
            name="valid_status",
        ),
        CheckConstraint("attempt_count BETWEEN 0 AND 100", name="valid_attempt_count"),
        CheckConstraint(
            "failure_code IS NULL OR char_length(btrim(failure_code)) BETWEEN 1 AND 64",
            name="valid_failure_code",
        ),
        UniqueConstraint("id", "business_id", name="uq_customer_agent_responses_id_business"),
        UniqueConstraint("business_id", "inbound_message_id", name="uq_customer_agent_responses_business_message"),
        Index("ix_customer_agent_responses_business_status", "business_id", "status", "updated_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    inbound_message_id: Mapped[UUID] = mapped_column(nullable=False)
    ai_execution_id: Mapped[UUID | None] = mapped_column(nullable=True)
    ai_action_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing", server_default="processing")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
