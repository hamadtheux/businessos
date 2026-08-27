"""add automation-first commerce, events, and audience segments

Revision ID: e1c2a3b4d5f6
Revises: d9b7c4e2a106
Create Date: 2026-08-25 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e1c2a3b4d5f6"
down_revision: str | None = "d9b7c4e2a106"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("catalog_items", sa.Column("compare_at_price", sa.Numeric(14, 2)))
    op.add_column("catalog_items", sa.Column("currency", sa.String(3)))
    op.add_column("catalog_items", sa.Column("cost", sa.Numeric(14, 2)))
    op.add_column("catalog_items", sa.Column("product_url", sa.String(2048)))
    op.add_column("catalog_items", sa.Column("inventory_quantity", sa.Integer()))
    op.add_column("catalog_items", sa.Column("availability", sa.String(24), nullable=False, server_default="unknown"))
    op.add_column("catalog_items", sa.Column("brand", sa.String(160)))
    op.add_column("catalog_items", sa.Column("vendor", sa.String(160)))
    op.add_column("catalog_items", sa.Column("gtin", sa.String(32)))
    op.add_column("catalog_items", sa.Column("mpn", sa.String(100)))
    op.add_column("catalog_items", sa.Column("condition", sa.String(16), nullable=False, server_default="new"))
    op.add_column("catalog_items", sa.Column("google_product_category", sa.String(255)))
    op.add_column("catalog_items", sa.Column("tags", postgresql.ARRAY(sa.String(80)), nullable=False, server_default="{}"))
    op.add_column("catalog_items", sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("catalog_items", sa.Column("source", sa.String(32), nullable=False, server_default="manual"))
    op.add_column("catalog_items", sa.Column("sync_state", sa.String(24), nullable=False, server_default="manual"))
    op.add_column("catalog_items", sa.Column("last_synchronized_at", sa.DateTime(timezone=True)))
    op.add_column("catalog_items", sa.Column("provider_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_check_constraint(op.f("ck_catalog_items_valid_compare_at_price"), "catalog_items", "compare_at_price IS NULL OR (compare_at_price >= 0 AND compare_at_price <= 999999999999.99)")
    op.create_check_constraint(op.f("ck_catalog_items_valid_cost"), "catalog_items", "cost IS NULL OR (cost >= 0 AND cost <= 999999999999.99)")
    op.create_check_constraint(op.f("ck_catalog_items_valid_currency"), "catalog_items", "currency IS NULL OR currency ~ '^[A-Z]{3}$'")
    op.create_check_constraint(op.f("ck_catalog_items_valid_inventory_quantity"), "catalog_items", "inventory_quantity IS NULL OR inventory_quantity BETWEEN 0 AND 2147483647")
    op.create_check_constraint(op.f("ck_catalog_items_valid_availability"), "catalog_items", "availability IN ('unknown','in_stock','out_of_stock','preorder','backorder')")
    op.create_check_constraint(op.f("ck_catalog_items_valid_condition"), "catalog_items", "condition IN ('new','refurbished','used')")
    op.create_check_constraint(op.f("ck_catalog_items_valid_source"), "catalog_items", "source ~ '^[a-z][a-z0-9_]{0,31}$'")
    op.create_check_constraint(op.f("ck_catalog_items_valid_sync_state"), "catalog_items", "sync_state IN ('manual','in_sync','pending','local_override','external_changed','error')")
    op.create_check_constraint(op.f("ck_catalog_items_valid_tag_count"), "catalog_items", "cardinality(tags) <= 100")
    op.create_check_constraint(op.f("ck_catalog_items_valid_provider_metadata"), "catalog_items", "jsonb_typeof(provider_metadata) = 'object' AND pg_column_size(provider_metadata) <= 32768")

    op.create_table(
        "commerce_connections",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_connection_id", postgresql.UUID(as_uuid=True)),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("external_account_id", sa.String(255)),
        sa.Column("store_url", sa.String(2048)),
        sa.Column("status", sa.String(32), nullable=False, server_default="configuration_required"),
        sa.Column("health", sa.String(24), nullable=False, server_default="not_checked"),
        sa.Column("capabilities", postgresql.ARRAY(sa.String(64)), nullable=False, server_default="{}"),
        sa.Column("sync_cursor", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("safe_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_sync_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("connected_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_commerce_connections_business_id_businesses")),
        sa.ForeignKeyConstraint(["connected_by_user_id"], ["users.id"], ondelete="SET NULL", name=op.f("fk_commerce_connections_connected_by_user_id_users")),
        sa.ForeignKeyConstraint(["integration_connection_id", "business_id"], ["integration_connections.id", "integration_connections.business_id"], name="fk_commerce_connections_integration_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commerce_connections")),
        sa.UniqueConstraint("id", "business_id", name="uq_commerce_connections_id_business"),
        sa.UniqueConstraint("business_id", "provider", "external_account_id", name="uq_commerce_connections_business_provider_account"),
        sa.CheckConstraint("provider IN ('shopify','woocommerce','bigcommerce','magento','custom_api','csv','xml_feed','google_product_feed','website','manual')", name=op.f("ck_commerce_connections_valid_provider")),
        sa.CheckConstraint("status IN ('configuration_required','connection_required','connected','syncing','attention_required','disabled')", name=op.f("ck_commerce_connections_valid_status")),
        sa.CheckConstraint("health IN ('not_checked','healthy','degraded','reauth_required','disabled')", name=op.f("ck_commerce_connections_valid_health")),
        sa.CheckConstraint("cardinality(capabilities) <= 20", name=op.f("ck_commerce_connections_valid_capabilities")),
        sa.CheckConstraint("jsonb_typeof(sync_cursor) = 'object' AND pg_column_size(sync_cursor) <= 16384", name=op.f("ck_commerce_connections_valid_sync_cursor")),
        sa.CheckConstraint("jsonb_typeof(safe_metadata) = 'object' AND pg_column_size(safe_metadata) <= 32768", name=op.f("ck_commerce_connections_valid_safe_metadata")),
    )
    op.create_index("ix_commerce_connections_business_status", "commerce_connections", ["business_id", "status", "provider", "id"])

    op.create_table(
        "catalog_sources",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("commerce_connection_id", postgresql.UUID(as_uuid=True)),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_account_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("authoritative", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("last_synchronized_at", sa.DateTime(timezone=True)),
        sa.Column("safe_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_catalog_sources_business_id_businesses")),
        sa.ForeignKeyConstraint(["commerce_connection_id", "business_id"], ["commerce_connections.id", "commerce_connections.business_id"], name="fk_catalog_sources_connection_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_sources")),
        sa.UniqueConstraint("id", "business_id", name="uq_catalog_sources_id_business"),
        sa.UniqueConstraint("business_id", "provider", "external_account_id", name="uq_catalog_sources_business_provider_account"),
        sa.CheckConstraint("provider IN ('shopify','woocommerce','bigcommerce','magento','custom_api','csv','xml_feed','google_product_feed','website','manual')", name=op.f("ck_catalog_sources_valid_provider")),
        sa.CheckConstraint("status IN ('active','paused','attention_required','archived')", name=op.f("ck_catalog_sources_valid_status")),
        sa.CheckConstraint("jsonb_typeof(safe_metadata) = 'object' AND pg_column_size(safe_metadata) <= 32768", name=op.f("ck_catalog_sources_valid_safe_metadata")),
    )
    op.create_index("ix_catalog_sources_business_status", "catalog_sources", ["business_id", "status", "provider", "id"])

    op.create_table(
        "commerce_sync_runs",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("products_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_archived", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("variants_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_cursor", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("provider_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_commerce_sync_runs_business_id_businesses")),
        sa.ForeignKeyConstraint(["connection_id", "business_id"], ["commerce_connections.id", "commerce_connections.business_id"], name="fk_commerce_sync_runs_connection_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commerce_sync_runs")),
        sa.UniqueConstraint("id", "business_id", name="uq_commerce_sync_runs_id_business"),
        sa.UniqueConstraint("connection_id", "idempotency_key", name="uq_commerce_sync_runs_connection_key"),
        sa.CheckConstraint("mode IN ('initial','incremental','full','manual_retry')", name=op.f("ck_commerce_sync_runs_valid_mode")),
        sa.CheckConstraint("status IN ('queued','running','completed','completed_with_issues','failed','configuration_required')", name=op.f("ck_commerce_sync_runs_valid_status")),
        sa.CheckConstraint("products_created >= 0 AND products_updated >= 0 AND products_archived >= 0 AND variants_processed >= 0 AND failures >= 0", name=op.f("ck_commerce_sync_runs_valid_counts")),
        sa.CheckConstraint("jsonb_typeof(provider_metadata) = 'object' AND pg_column_size(provider_metadata) <= 32768", name=op.f("ck_commerce_sync_runs_valid_provider_metadata")),
    )
    op.create_index("ix_commerce_sync_runs_business_started", "commerce_sync_runs", ["business_id", "started_at", "id"])

    op.create_table(
        "external_product_mappings",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_account_id", sa.String(255), nullable=False),
        sa.Column("external_object_id", sa.String(255), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column("sync_state", sa.String(24), nullable=False, server_default="in_sync"),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True)),
        sa.Column("last_synchronized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authoritative_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("safe_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_external_product_mappings_business_id_businesses")),
        sa.ForeignKeyConstraint(["catalog_source_id", "business_id"], ["catalog_sources.id", "catalog_sources.business_id"], name="fk_external_product_mappings_source_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_external_product_mappings_item_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_product_mappings")),
        sa.UniqueConstraint("id", "business_id", name="uq_external_product_mappings_id_business"),
        sa.UniqueConstraint("business_id", "provider", "external_account_id", "external_object_id", name="uq_external_product_mappings_external_identity"),
        sa.CheckConstraint("provider IN ('shopify','woocommerce','bigcommerce','magento','custom_api','csv','xml_feed','google_product_feed','website','manual')", name=op.f("ck_external_product_mappings_valid_provider")),
        sa.CheckConstraint("sync_state IN ('in_sync','pending','local_override','external_changed','error','archived')", name=op.f("ck_external_product_mappings_valid_sync_state")),
        sa.CheckConstraint("jsonb_typeof(authoritative_snapshot) = 'object' AND pg_column_size(authoritative_snapshot) <= 65536", name=op.f("ck_external_product_mappings_valid_authoritative_snapshot")),
        sa.CheckConstraint("jsonb_typeof(safe_metadata) = 'object' AND pg_column_size(safe_metadata) <= 32768", name=op.f("ck_external_product_mappings_valid_safe_metadata")),
    )
    op.create_index("ix_external_product_mappings_business_item", "external_product_mappings", ["business_id", "catalog_item_id", "id"])

    op.create_table(
        "catalog_variants",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_account_id", sa.String(255), nullable=False),
        sa.Column("external_object_id", sa.String(255), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("sku", sa.String(100)), sa.Column("price", sa.Numeric(14, 2)),
        sa.Column("compare_at_price", sa.Numeric(14, 2)), sa.Column("currency", sa.String(3)),
        sa.Column("inventory_quantity", sa.Integer()),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("option_values", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_synchronized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_catalog_variants_business_id_businesses")),
        sa.ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_catalog_variants_item_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_variants")),
        sa.UniqueConstraint("id", "business_id", name="uq_catalog_variants_id_business"),
        sa.UniqueConstraint("business_id", "provider", "external_account_id", "external_object_id", name="uq_catalog_variants_external_identity"),
        sa.CheckConstraint("price IS NULL OR price >= 0", name=op.f("ck_catalog_variants_valid_price")),
        sa.CheckConstraint("compare_at_price IS NULL OR compare_at_price >= 0", name=op.f("ck_catalog_variants_valid_compare_at_price")),
        sa.CheckConstraint("inventory_quantity IS NULL OR inventory_quantity >= 0", name=op.f("ck_catalog_variants_valid_inventory_quantity")),
    )
    op.create_index("ix_catalog_variants_business_item", "catalog_variants", ["business_id", "catalog_item_id", "id"])

    op.create_table(
        "catalog_media",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_type", sa.String(16), nullable=False, server_default="image"),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("alt_text", sa.String(1000)),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("authoritative", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_catalog_media_business_id_businesses")),
        sa.ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_catalog_media_item_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_media")),
        sa.UniqueConstraint("id", "business_id", name="uq_catalog_media_id_business"),
        sa.UniqueConstraint("business_id", "catalog_item_id", "source_url", name="uq_catalog_media_item_url"),
        sa.CheckConstraint("media_type IN ('image','video','document')", name=op.f("ck_catalog_media_valid_media_type")),
    )
    op.create_index("ix_catalog_media_business_item", "catalog_media", ["business_id", "catalog_item_id", "position", "id"])

    op.create_table(
        "commerce_sync_issues",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sync_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_object_id", sa.String(255)),
        sa.Column("severity", sa.String(16), nullable=False), sa.Column("code", sa.String(64), nullable=False),
        sa.Column("message", sa.String(1000), nullable=False),
        sa.Column("safe_details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_commerce_sync_issues_business_id_businesses")),
        sa.ForeignKeyConstraint(["sync_run_id", "business_id"], ["commerce_sync_runs.id", "commerce_sync_runs.business_id"], name="fk_commerce_sync_issues_run_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commerce_sync_issues")),
        sa.CheckConstraint("severity IN ('warning','error')", name=op.f("ck_commerce_sync_issues_valid_severity")),
        sa.CheckConstraint("jsonb_typeof(safe_details) = 'object' AND pg_column_size(safe_details) <= 16384", name=op.f("ck_commerce_sync_issues_valid_safe_details")),
    )
    op.create_index("ix_commerce_sync_issues_business_run", "commerce_sync_issues", ["business_id", "sync_run_id", "severity", "id"])

    op.create_table(
        "commerce_events",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True)), sa.Column("catalog_item_id", postgresql.UUID(as_uuid=True)),
        sa.Column("order_id", postgresql.UUID(as_uuid=True)), sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("source", sa.String(32), nullable=False), sa.Column("external_event_id", sa.String(255), nullable=False),
        sa.Column("anonymous_session_hash", sa.String(64)), sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("safe_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_commerce_events_business_id_businesses")),
        sa.ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_commerce_events_customer_business"),
        sa.ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_commerce_events_catalog_business"),
        sa.ForeignKeyConstraint(["order_id", "business_id"], ["orders.id", "orders.business_id"], name="fk_commerce_events_order_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commerce_events")),
        sa.UniqueConstraint("id", "business_id", name="uq_commerce_events_id_business"),
        sa.UniqueConstraint("business_id", "source", "external_event_id", name="uq_commerce_events_source_external"),
        sa.CheckConstraint("event_type IN ('product_viewed','collection_viewed','search_performed','cart_created','cart_updated','checkout_started','checkout_abandoned','order_created','order_paid','order_fulfilled','order_refunded','coupon_used','chat_started','lead_captured','campaign_clicked')", name=op.f("ck_commerce_events_valid_event_type")),
        sa.CheckConstraint("source ~ '^[a-z][a-z0-9_]{0,31}$'", name=op.f("ck_commerce_events_valid_source")),
        sa.CheckConstraint("jsonb_typeof(safe_metadata) = 'object' AND pg_column_size(safe_metadata) <= 32768", name=op.f("ck_commerce_events_valid_safe_metadata")),
    )
    op.create_index("ix_commerce_events_business_occurred", "commerce_events", ["business_id", "occurred_at", "id"])
    op.create_index("ix_commerce_events_business_customer", "commerce_events", ["business_id", "customer_id", "occurred_at", "id"])
    op.create_index("ix_commerce_events_business_type", "commerce_events", ["business_id", "event_type", "occurred_at", "id"])

    op.create_table(
        "audience_segments",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(160), nullable=False), sa.Column("description", sa.Text()),
        sa.Column("natural_language_definition", sa.Text()), sa.Column("rule", postgresql.JSONB(), nullable=False),
        sa.Column("source_classification", sa.String(32), nullable=False, server_default="first_party_observed"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("matched_customer_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_audience_segments_business_id_businesses")),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL", name=op.f("fk_audience_segments_created_by_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audience_segments")),
        sa.UniqueConstraint("id", "business_id", name="uq_audience_segments_id_business"),
        sa.CheckConstraint("source_classification IN ('first_party_observed','platform_supplied','public_research','ai_inference')", name=op.f("ck_audience_segments_valid_source_classification")),
        sa.CheckConstraint("status IN ('draft','active','paused','archived')", name=op.f("ck_audience_segments_valid_status")),
        sa.CheckConstraint("jsonb_typeof(rule) = 'object' AND pg_column_size(rule) <= 32768", name=op.f("ck_audience_segments_valid_rule")),
        sa.CheckConstraint("matched_customer_count >= 0", name=op.f("ck_audience_segments_valid_matched_customer_count")),
    )
    op.create_index("ix_audience_segments_business_status", "audience_segments", ["business_id", "status", "updated_at", "id"])

    op.create_table(
        "audience_segment_members",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("segment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_summary", sa.String(500), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_audience_segment_members_business_id_businesses")),
        sa.ForeignKeyConstraint(["segment_id", "business_id"], ["audience_segments.id", "audience_segments.business_id"], name="fk_audience_segment_members_segment_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_audience_segment_members_customer_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audience_segment_members")),
        sa.UniqueConstraint("business_id", "segment_id", "customer_id", name="uq_audience_segment_members_segment_customer"),
    )
    op.create_index("ix_audience_segment_members_business_customer", "audience_segment_members", ["business_id", "customer_id", "segment_id"])

    op.create_table(
        "commerce_feed_destinations",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False), sa.Column("external_account_id", sa.String(255)),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="configuration_required"),
        sa.Column("synchronized_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eligible_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_synchronized_at", sa.DateTime(timezone=True)), sa.Column("failure_code", sa.String(64)),
        sa.Column("safe_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_commerce_feed_destinations_business_id_businesses")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commerce_feed_destinations")),
        sa.UniqueConstraint("id", "business_id", name="uq_commerce_feed_destinations_id_business"),
        sa.UniqueConstraint("business_id", "provider", "external_account_id", name="uq_commerce_feed_destinations_provider_account"),
        sa.CheckConstraint("provider IN ('google_merchant_center','meta_product_catalog')", name=op.f("ck_commerce_feed_destinations_valid_provider")),
        sa.CheckConstraint("status IN ('configuration_required','connection_required','connected','syncing','attention_required','disabled')", name=op.f("ck_commerce_feed_destinations_valid_status")),
        sa.CheckConstraint("eligible_count >= 0 AND warning_count >= 0 AND rejected_count >= 0 AND synchronized_count >= 0", name=op.f("ck_commerce_feed_destinations_valid_counts")),
        sa.CheckConstraint("jsonb_typeof(safe_metadata) = 'object' AND pg_column_size(safe_metadata) <= 32768", name=op.f("ck_commerce_feed_destinations_valid_safe_metadata")),
    )
    op.create_index("ix_commerce_feed_destinations_business_status", "commerce_feed_destinations", ["business_id", "status", "provider", "id"])

    op.create_table(
        "commerce_feed_product_statuses",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_product_id", sa.String(255)), sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("missing_attributes", postgresql.ARRAY(sa.String(100)), nullable=False, server_default="{}"),
        sa.Column("warnings", postgresql.ARRAY(sa.String(500)), nullable=False, server_default="{}"),
        sa.Column("provider_error_code", sa.String(100)), sa.Column("last_synchronized_at", sa.DateTime(timezone=True)),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_commerce_feed_product_statuses_business_id_businesses")),
        sa.ForeignKeyConstraint(["destination_id", "business_id"], ["commerce_feed_destinations.id", "commerce_feed_destinations.business_id"], name="fk_commerce_feed_product_statuses_destination_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_commerce_feed_product_statuses_item_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commerce_feed_product_statuses")),
        sa.UniqueConstraint("business_id", "destination_id", "catalog_item_id", name="uq_commerce_feed_product_statuses_destination_item"),
        sa.CheckConstraint("status IN ('pending','eligible','warning','rejected','removed')", name=op.f("ck_commerce_feed_product_statuses_valid_status")),
        sa.CheckConstraint("cardinality(missing_attributes) <= 50 AND cardinality(warnings) <= 50", name=op.f("ck_commerce_feed_product_statuses_valid_issue_counts")),
    )
    op.create_index("ix_commerce_feed_product_statuses_business_status", "commerce_feed_product_statuses", ["business_id", "destination_id", "status", "id"])

    op.create_table(
        "campaign_product_selections",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_reason", sa.String(500)),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_campaign_product_selections_business_id_businesses")),
        sa.ForeignKeyConstraint(["campaign_id", "business_id"], ["marketing_campaigns.id", "marketing_campaigns.business_id"], name="fk_campaign_product_selections_campaign_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_campaign_product_selections_item_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_product_selections")),
        sa.UniqueConstraint("business_id", "campaign_id", "catalog_item_id", name="uq_campaign_product_selections_campaign_item"),
        sa.CheckConstraint("selection_reason IS NULL OR char_length(selection_reason) <= 500", name=op.f("ck_campaign_product_selections_valid_selection_reason")),
    )
    op.create_index("ix_campaign_product_selections_business_item", "campaign_product_selections", ["business_id", "catalog_item_id", "campaign_id"])


def downgrade() -> None:
    for table in (
        "campaign_product_selections", "commerce_feed_product_statuses", "commerce_feed_destinations",
        "audience_segment_members", "audience_segments", "commerce_events",
        "commerce_sync_issues", "catalog_media", "catalog_variants",
        "external_product_mappings", "commerce_sync_runs", "catalog_sources",
        "commerce_connections",
    ):
        op.drop_table(table)
    for name in (
        "valid_provider_metadata", "valid_tag_count", "valid_sync_state", "valid_source",
        "valid_condition", "valid_availability", "valid_inventory_quantity", "valid_currency",
        "valid_cost", "valid_compare_at_price",
    ):
        op.drop_constraint(op.f(f"ck_catalog_items_{name}"), "catalog_items", type_="check")
    for column in (
        "provider_metadata", "last_synchronized_at", "sync_state", "source", "published",
        "tags", "google_product_category", "condition", "mpn", "gtin", "vendor", "brand",
        "availability", "inventory_quantity", "product_url", "cost", "currency", "compare_at_price",
    ):
        op.drop_column("catalog_items", column)
