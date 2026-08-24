from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ChatbotConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chatbot_configs"
    __table_args__ = (
        UniqueConstraint("id", "business_id", name="uq_chatbot_configs_id_business"),
        UniqueConstraint("business_id", name="uq_chatbot_configs_business"),
        UniqueConstraint("widget_public_id", name="uq_chatbot_configs_widget_public_id"),
        CheckConstraint("char_length(widget_public_id) BETWEEN 40 AND 96", name="valid_widget_public_id"),
        CheckConstraint("char_length(btrim(display_name)) BETWEEN 1 AND 80", name="valid_display_name"),
        CheckConstraint("char_length(btrim(welcome_message)) BETWEEN 1 AND 500", name="valid_welcome_message"),
        CheckConstraint("char_length(btrim(placeholder_text)) BETWEEN 1 AND 160", name="valid_placeholder_text"),
        CheckConstraint("tone IN ('friendly','professional','concise','warm')", name="valid_tone"),
        CheckConstraint("theme IN ('light','dark','auto')", name="valid_theme"),
        CheckConstraint("position IN ('bottom_right','bottom_left')", name="valid_position"),
        CheckConstraint("launcher_style IN ('bubble','pill')", name="valid_launcher_style"),
        CheckConstraint("cardinality(allowed_capabilities) <= 8", name="valid_capability_count"),
        CheckConstraint("cardinality(allowed_domains) <= 50", name="valid_domain_count"),
        CheckConstraint("default_locale ~ '^[a-z]{2,3}(-[A-Z]{2})?$'", name="valid_default_locale"),
        CheckConstraint("border_radius BETWEEN 0 AND 28", name="valid_border_radius"),
        CheckConstraint("privacy_policy_url IS NULL OR char_length(privacy_policy_url) <= 2048", name="valid_privacy_policy_url"),
        CheckConstraint("consent_text IS NULL OR char_length(consent_text) <= 1000", name="valid_consent_text"),
        Index("ix_chatbot_configs_business_enabled", "business_id", "enabled", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    widget_public_id: Mapped[str] = mapped_column(String(96), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    welcome_message: Mapped[str] = mapped_column(String(500), nullable=False)
    placeholder_text: Mapped[str] = mapped_column(String(160), nullable=False)
    tone: Mapped[str] = mapped_column(String(24), nullable=False, default="friendly", server_default="friendly")
    theme: Mapped[str] = mapped_column(String(16), nullable=False, default="light", server_default="light")
    position: Mapped[str] = mapped_column(String(24), nullable=False, default="bottom_right", server_default="bottom_right")
    launcher_style: Mapped[str] = mapped_column(String(16), nullable=False, default="bubble", server_default="bubble")
    allowed_capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    allowed_domains: Mapped[list[str]] = mapped_column(
        ARRAY(String(253)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    privacy_policy_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    consent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    require_lead_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    default_locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en", server_default="en")
    border_radius: Mapped[int] = mapped_column(Integer, nullable=False, default=18, server_default="18")


class ChatbotSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chatbot_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chatbot_config_id", "business_id"],
            ["chatbot_configs.id", "chatbot_configs.business_id"],
            name="fk_chatbot_sessions_config_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["customer_id", "business_id"],
            ["customers.id", "customers.business_id"],
            name="fk_chatbot_sessions_customer_business",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "business_id"],
            ["conversations.id", "conversations.business_id"],
            name="fk_chatbot_sessions_conversation_business",
        ),
        UniqueConstraint("session_token_hash", name="uq_chatbot_sessions_token_hash"),
        CheckConstraint("char_length(session_token_hash) = 64", name="valid_session_token_hash"),
        CheckConstraint("status IN ('active','handoff_requested','closed','expired')", name="valid_status"),
        CheckConstraint("char_length(origin_host) BETWEEN 1 AND 253", name="valid_origin_host"),
        CheckConstraint("locale ~ '^[a-z]{2,3}(-[A-Z]{2})?$'", name="valid_locale"),
        CheckConstraint("expires_at > started_at", name="valid_expiry"),
        CheckConstraint("last_activity_at >= started_at", name="valid_activity"),
        CheckConstraint("message_count >= 0 AND ai_response_count >= 0", name="valid_message_counts"),
        CheckConstraint("response_duration_ms_total >= 0", name="valid_response_duration"),
        CheckConstraint("order_lookup_attempts BETWEEN 0 AND 10", name="valid_order_attempts"),
        CheckConstraint("booking_attempts BETWEEN 0 AND 10", name="valid_booking_attempts"),
        CheckConstraint("order_lookup_count >= 0 AND appointment_booked_count >= 0 AND ai_failure_count >= 0", name="valid_metric_counts"),
        Index("ix_chatbot_sessions_business_started", "business_id", "started_at", "id"),
        Index("ix_chatbot_sessions_config_activity", "chatbot_config_id", "last_activity_at", "id"),
        Index("ix_chatbot_sessions_expiry", "status", "expires_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    chatbot_config_id: Mapped[UUID] = mapped_column(nullable=False)
    session_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    origin_host: Mapped[str] = mapped_column(String(253), nullable=False)
    customer_id: Mapped[UUID | None] = mapped_column(nullable=True)
    conversation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", server_default="active")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en", server_default="en")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lead_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    handoff_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ai_response_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    response_duration_ms_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    order_lookup_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    booking_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    order_lookup_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    appointment_booked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    product_recommendation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ai_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
