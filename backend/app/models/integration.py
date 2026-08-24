from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


CONNECTOR_SQL = "('whatsapp_business','gmail','google_calendar','google_ads','meta_ads','facebook','instagram','microsoft_outlook')"


class IntegrationConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint("id", "business_id", name="uq_integration_connections_id_business"),
        UniqueConstraint("business_id", "connector_type", name="uq_integration_connections_business_connector"),
        CheckConstraint(f"connector_type IN {CONNECTOR_SQL}", name="valid_connector_type"),
        CheckConstraint("status IN ('disconnected','pending','connected','degraded','reauth_required','disabled','revoked')", name="valid_status"),
        CheckConstraint("authentication_state IN ('not_authorized','authorization_pending','authorized','failed','revoked')", name="valid_authentication_state"),
        CheckConstraint("health IN ('not_checked','healthy','degraded','reauth_required','revoked')", name="valid_health"),
        CheckConstraint("char_length(btrim(display_name)) BETWEEN 1 AND 160", name="valid_display_name"),
        CheckConstraint("credential_reference IS NULL OR char_length(btrim(credential_reference)) BETWEEN 1 AND 255", name="valid_credential_reference"),
        CheckConstraint("external_account_reference IS NULL OR char_length(btrim(external_account_reference)) BETWEEN 1 AND 255", name="valid_external_account_reference"),
        CheckConstraint("jsonb_typeof(selected_resources) = 'array' AND jsonb_array_length(selected_resources) <= 20", name="valid_selected_resources"),
        CheckConstraint("cardinality(scopes_granted) <= 30", name="valid_scopes_granted"),
        CheckConstraint("failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$'", name="valid_failure_code"),
        Index("ix_integration_connections_business_status", "business_id", "status", "connector_type", "id"),
        Index("ix_integration_connections_health", "business_id", "health", "updated_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(48), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="disconnected", server_default="disconnected")
    authentication_state: Mapped[str] = mapped_column(String(32), nullable=False, default="not_authorized", server_default="not_authorized")
    health: Mapped[str] = mapped_column(String(24), nullable=False, default="not_checked", server_default="not_checked")
    credential_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_account_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_account_display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    selected_resources: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    scopes_granted: Mapped[list[str]] = mapped_column(ARRAY(String(255)), nullable=False, default=list, server_default=text("'{}'::varchar[]"))
    connected_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class IntegrationOAuthState(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "integration_oauth_states"
    __table_args__ = (
        CheckConstraint(f"connector_type IN {CONNECTOR_SQL}", name="valid_connector_type"),
        CheckConstraint("char_length(state_hash) = 64", name="valid_state_hash"),
        CheckConstraint("char_length(btrim(pkce_verifier_reference)) BETWEEN 1 AND 255", name="valid_pkce_verifier_reference"),
        CheckConstraint("redirect_target IN ('/integrations')", name="valid_redirect_target"),
        UniqueConstraint("state_hash", name="uq_integration_oauth_states_state_hash"),
        Index("ix_integration_oauth_states_expiry", "expires_at", "consumed_at", "id"),
        Index("ix_integration_oauth_states_business_user", "business_id", "user_id", "connector_type", "created_at"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(48), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pkce_verifier_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    redirect_target: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class IntegrationWebhookEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "integration_webhook_events"
    __table_args__ = (
        ForeignKeyConstraint(["integration_connection_id", "business_id"], ["integration_connections.id", "integration_connections.business_id"], name="fk_integration_webhook_events_connection_business", ondelete="CASCADE"),
        CheckConstraint(f"connector_type IN {CONNECTOR_SQL}", name="valid_connector_type"),
        CheckConstraint("event_type IN ('message_received','message_status_updated','email_received','calendar_event_changed','performance_data_available')", name="valid_event_type"),
        CheckConstraint("status IN ('received','processed','failed','duplicate')", name="valid_status"),
        CheckConstraint("char_length(btrim(external_event_id)) BETWEEN 1 AND 255", name="valid_external_event_id"),
        CheckConstraint("jsonb_typeof(normalized_payload) = 'object'", name="valid_normalized_payload"),
        CheckConstraint("failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$'", name="valid_failure_code"),
        UniqueConstraint("integration_connection_id", "external_event_id", name="uq_integration_webhook_events_connection_external"),
        Index("ix_integration_webhook_events_business_received", "business_id", "received_at", "id"),
        Index("ix_integration_webhook_events_connection_status", "integration_connection_id", "status", "received_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    integration_connection_id: Mapped[UUID] = mapped_column(nullable=False)
    connector_type: Mapped[str] = mapped_column(String(48), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="received", server_default="received")
    normalized_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class IntegrationEntityLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_entity_links"
    __table_args__ = (
        ForeignKeyConstraint(["integration_connection_id", "business_id"], ["integration_connections.id", "integration_connections.business_id"], name="fk_integration_entity_links_connection_business", ondelete="CASCADE"),
        CheckConstraint("internal_entity_type IN ('appointment','campaign','conversation')", name="valid_internal_entity_type"),
        CheckConstraint("sync_state IN ('linked','in_sync','internal_changed','external_changed','conflict','unlinked')", name="valid_sync_state"),
        CheckConstraint("char_length(btrim(external_resource_reference)) BETWEEN 1 AND 255", name="valid_external_resource_reference"),
        CheckConstraint("char_length(btrim(external_entity_id)) BETWEEN 1 AND 255", name="valid_external_entity_id"),
        UniqueConstraint("integration_connection_id", "internal_entity_type", "internal_entity_id", name="uq_integration_entity_links_connection_internal"),
        UniqueConstraint("integration_connection_id", "external_resource_reference", "external_entity_id", name="uq_integration_entity_links_connection_external"),
        Index("ix_integration_entity_links_business_internal", "business_id", "internal_entity_type", "internal_entity_id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    integration_connection_id: Mapped[UUID] = mapped_column(nullable=False)
    internal_entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    internal_entity_id: Mapped[UUID] = mapped_column(nullable=False)
    external_resource_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    external_entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sync_state: Mapped[str] = mapped_column(String(24), nullable=False, default="linked", server_default="linked")
    last_internal_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_external_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
