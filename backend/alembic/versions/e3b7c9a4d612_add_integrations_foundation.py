"""add integrations foundation

Revision ID: e3b7c9a4d612
Revises: d2a9e6b4f731
Create Date: 2026-08-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e3b7c9a4d612"
down_revision: str | Sequence[str] | None = "d2a9e6b4f731"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CONNECTOR_SQL = "('whatsapp_business','gmail','google_calendar','google_ads','meta_ads','facebook','instagram','microsoft_outlook')"


def upgrade() -> None:
    op.create_table(
        "integration_connections",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("connector_type", sa.String(48), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), server_default="disconnected", nullable=False),
        sa.Column("authentication_state", sa.String(32), server_default="not_authorized", nullable=False),
        sa.Column("health", sa.String(24), server_default="not_checked", nullable=False),
        sa.Column("credential_reference", sa.String(255), nullable=True),
        sa.Column("external_account_reference", sa.String(255), nullable=True),
        sa.Column("external_account_display_name", sa.String(160), nullable=True),
        sa.Column("selected_resources", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("scopes_granted", postgresql.ARRAY(sa.String(255)), server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("connected_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"connector_type IN {CONNECTOR_SQL}", name=op.f("ck_integration_connections_valid_connector_type")),
        sa.CheckConstraint("status IN ('disconnected','pending','connected','degraded','reauth_required','disabled','revoked')", name=op.f("ck_integration_connections_valid_status")),
        sa.CheckConstraint("authentication_state IN ('not_authorized','authorization_pending','authorized','failed','revoked')", name=op.f("ck_integration_connections_valid_authentication_state")),
        sa.CheckConstraint("health IN ('not_checked','healthy','degraded','reauth_required','revoked')", name=op.f("ck_integration_connections_valid_health")),
        sa.CheckConstraint("char_length(btrim(display_name)) BETWEEN 1 AND 160", name=op.f("ck_integration_connections_valid_display_name")),
        sa.CheckConstraint("credential_reference IS NULL OR char_length(btrim(credential_reference)) BETWEEN 1 AND 255", name=op.f("ck_integration_connections_valid_credential_reference")),
        sa.CheckConstraint("external_account_reference IS NULL OR char_length(btrim(external_account_reference)) BETWEEN 1 AND 255", name=op.f("ck_integration_connections_valid_external_account_reference")),
        sa.CheckConstraint("jsonb_typeof(selected_resources) = 'array' AND jsonb_array_length(selected_resources) <= 20", name=op.f("ck_integration_connections_valid_selected_resources")),
        sa.CheckConstraint("cardinality(scopes_granted) <= 30", name=op.f("ck_integration_connections_valid_scopes_granted")),
        sa.CheckConstraint("failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$'", name=op.f("ck_integration_connections_valid_failure_code")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_integration_connections_business_id_businesses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connected_by_user_id"], ["users.id"], name=op.f("fk_integration_connections_connected_by_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_connections")),
        sa.UniqueConstraint("id", "business_id", name="uq_integration_connections_id_business"),
        sa.UniqueConstraint("business_id", "connector_type", name="uq_integration_connections_business_connector"),
    )
    op.create_index("ix_integration_connections_business_status", "integration_connections", ["business_id", "status", "connector_type", "id"])
    op.create_index("ix_integration_connections_health", "integration_connections", ["business_id", "health", "updated_at", "id"])

    op.create_table(
        "integration_oauth_states",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("connector_type", sa.String(48), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("pkce_verifier_reference", sa.String(255), nullable=False),
        sa.Column("redirect_target", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"connector_type IN {CONNECTOR_SQL}", name=op.f("ck_integration_oauth_states_valid_connector_type")),
        sa.CheckConstraint("char_length(state_hash) = 64", name=op.f("ck_integration_oauth_states_valid_state_hash")),
        sa.CheckConstraint("char_length(btrim(pkce_verifier_reference)) BETWEEN 1 AND 255", name=op.f("ck_integration_oauth_states_valid_pkce_verifier_reference")),
        sa.CheckConstraint("redirect_target IN ('/integrations')", name=op.f("ck_integration_oauth_states_valid_redirect_target")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_integration_oauth_states_business_id_businesses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_integration_oauth_states_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_oauth_states")),
        sa.UniqueConstraint("state_hash", name="uq_integration_oauth_states_state_hash"),
    )
    op.create_index("ix_integration_oauth_states_expiry", "integration_oauth_states", ["expires_at", "consumed_at", "id"])
    op.create_index("ix_integration_oauth_states_business_user", "integration_oauth_states", ["business_id", "user_id", "connector_type", "created_at"])

    op.create_table(
        "integration_webhook_events",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("integration_connection_id", sa.Uuid(), nullable=False),
        sa.Column("connector_type", sa.String(48), nullable=False),
        sa.Column("external_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("status", sa.String(24), server_default="received", nullable=False),
        sa.Column("normalized_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"connector_type IN {CONNECTOR_SQL}", name=op.f("ck_integration_webhook_events_valid_connector_type")),
        sa.CheckConstraint("event_type IN ('message_received','message_status_updated','email_received','calendar_event_changed','performance_data_available')", name=op.f("ck_integration_webhook_events_valid_event_type")),
        sa.CheckConstraint("status IN ('received','processed','failed','duplicate')", name=op.f("ck_integration_webhook_events_valid_status")),
        sa.CheckConstraint("char_length(btrim(external_event_id)) BETWEEN 1 AND 255", name=op.f("ck_integration_webhook_events_valid_external_event_id")),
        sa.CheckConstraint("jsonb_typeof(normalized_payload) = 'object'", name=op.f("ck_integration_webhook_events_valid_normalized_payload")),
        sa.CheckConstraint("failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$'", name=op.f("ck_integration_webhook_events_valid_failure_code")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_integration_webhook_events_business_id_businesses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["integration_connection_id", "business_id"], ["integration_connections.id", "integration_connections.business_id"], name="fk_integration_webhook_events_connection_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_webhook_events")),
        sa.UniqueConstraint("integration_connection_id", "external_event_id", name="uq_integration_webhook_events_connection_external"),
    )
    op.create_index("ix_integration_webhook_events_business_received", "integration_webhook_events", ["business_id", "received_at", "id"])
    op.create_index("ix_integration_webhook_events_connection_status", "integration_webhook_events", ["integration_connection_id", "status", "received_at", "id"])

    op.create_table(
        "integration_entity_links",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("integration_connection_id", sa.Uuid(), nullable=False),
        sa.Column("internal_entity_type", sa.String(32), nullable=False),
        sa.Column("internal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("external_resource_reference", sa.String(255), nullable=False),
        sa.Column("external_entity_id", sa.String(255), nullable=False),
        sa.Column("sync_state", sa.String(24), server_default="linked", nullable=False),
        sa.Column("last_internal_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_external_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("internal_entity_type IN ('appointment','campaign','conversation')", name=op.f("ck_integration_entity_links_valid_internal_entity_type")),
        sa.CheckConstraint("sync_state IN ('linked','in_sync','internal_changed','external_changed','conflict','unlinked')", name=op.f("ck_integration_entity_links_valid_sync_state")),
        sa.CheckConstraint("char_length(btrim(external_resource_reference)) BETWEEN 1 AND 255", name=op.f("ck_integration_entity_links_valid_external_resource_reference")),
        sa.CheckConstraint("char_length(btrim(external_entity_id)) BETWEEN 1 AND 255", name=op.f("ck_integration_entity_links_valid_external_entity_id")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_integration_entity_links_business_id_businesses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["integration_connection_id", "business_id"], ["integration_connections.id", "integration_connections.business_id"], name="fk_integration_entity_links_connection_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_entity_links")),
        sa.UniqueConstraint("integration_connection_id", "internal_entity_type", "internal_entity_id", name="uq_integration_entity_links_connection_internal"),
        sa.UniqueConstraint("integration_connection_id", "external_resource_reference", "external_entity_id", name="uq_integration_entity_links_connection_external"),
    )
    op.create_index("ix_integration_entity_links_business_internal", "integration_entity_links", ["business_id", "internal_entity_type", "internal_entity_id"])


def downgrade() -> None:
    op.drop_index("ix_integration_entity_links_business_internal", table_name="integration_entity_links")
    op.drop_table("integration_entity_links")
    op.drop_index("ix_integration_webhook_events_connection_status", table_name="integration_webhook_events")
    op.drop_index("ix_integration_webhook_events_business_received", table_name="integration_webhook_events")
    op.drop_table("integration_webhook_events")
    op.drop_index("ix_integration_oauth_states_business_user", table_name="integration_oauth_states")
    op.drop_index("ix_integration_oauth_states_expiry", table_name="integration_oauth_states")
    op.drop_table("integration_oauth_states")
    op.drop_index("ix_integration_connections_health", table_name="integration_connections")
    op.drop_index("ix_integration_connections_business_status", table_name="integration_connections")
    op.drop_table("integration_connections")
