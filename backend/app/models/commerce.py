from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


COMMERCE_PROVIDERS = (
    "shopify", "woocommerce", "bigcommerce", "magento", "custom_api",
    "csv", "xml_feed", "google_product_feed", "website", "manual",
)
PROVIDER_SQL = "(" + ",".join(f"'{value}'" for value in COMMERCE_PROVIDERS) + ")"


class CommerceConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "commerce_connections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["integration_connection_id", "business_id"],
            ["integration_connections.id", "integration_connections.business_id"],
            name="fk_commerce_connections_integration_business",
        ),
        CheckConstraint(f"provider IN {PROVIDER_SQL}", name="valid_provider"),
        CheckConstraint(
            "status IN ('configuration_required','connection_required','connected','syncing','attention_required','authentication_expired','rate_limited','failed','disabled')",
            name="valid_status",
        ),
        CheckConstraint(
            "health IN ('not_checked','healthy','degraded','reauth_required','rate_limited','failed','disabled')",
            name="valid_health",
        ),
        CheckConstraint("cardinality(capabilities) <= 20", name="valid_capabilities"),
        CheckConstraint("credential_reference IS NULL OR char_length(btrim(credential_reference)) BETWEEN 1 AND 255", name="valid_credential_reference"),
        CheckConstraint("consecutive_failures >= 0", name="valid_consecutive_failures"),
        CheckConstraint(
            "jsonb_typeof(sync_cursor) = 'object' AND pg_column_size(sync_cursor) <= 16384",
            name="valid_sync_cursor",
        ),
        CheckConstraint(
            "jsonb_typeof(safe_metadata) = 'object' AND pg_column_size(safe_metadata) <= 32768",
            name="valid_safe_metadata",
        ),
        UniqueConstraint("id", "business_id", name="uq_commerce_connections_id_business"),
        UniqueConstraint(
            "business_id", "provider", "external_account_id",
            name="uq_commerce_connections_business_provider_account",
        ),
        Index("ix_commerce_connections_business_status", "business_id", "status", "provider", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    integration_connection_id: Mapped[UUID | None] = mapped_column(nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    external_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    store_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    credential_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="configuration_required", server_default="configuration_required")
    health: Mapped[str] = mapped_column(String(24), nullable=False, default="not_checked", server_default="not_checked")
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, default=list, server_default="{}")
    sync_cursor: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    safe_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    last_sync_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    store_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    connected_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class CatalogSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "catalog_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["commerce_connection_id", "business_id"],
            ["commerce_connections.id", "commerce_connections.business_id"],
            name="fk_catalog_sources_connection_business",
            ondelete="CASCADE",
        ),
        CheckConstraint(f"provider IN {PROVIDER_SQL}", name="valid_provider"),
        CheckConstraint("status IN ('active','paused','attention_required','archived')", name="valid_status"),
        CheckConstraint("jsonb_typeof(safe_metadata) = 'object' AND pg_column_size(safe_metadata) <= 32768", name="valid_safe_metadata"),
        UniqueConstraint("id", "business_id", name="uq_catalog_sources_id_business"),
        UniqueConstraint("business_id", "provider", "external_account_id", name="uq_catalog_sources_business_provider_account"),
        Index("ix_catalog_sources_business_status", "business_id", "status", "provider", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    commerce_connection_id: Mapped[UUID | None] = mapped_column(nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", server_default="active")
    last_synchronized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    safe_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))


class CommerceSyncRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "commerce_sync_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["connection_id", "business_id"],
            ["commerce_connections.id", "commerce_connections.business_id"],
            name="fk_commerce_sync_runs_connection_business",
            ondelete="CASCADE",
        ),
        CheckConstraint("mode IN ('initial','incremental','full','manual_retry')", name="valid_mode"),
        CheckConstraint("status IN ('queued','running','completed','completed_with_issues','failed','configuration_required')", name="valid_status"),
        CheckConstraint("products_created >= 0 AND products_updated >= 0 AND products_archived >= 0 AND variants_processed >= 0 AND customers_created >= 0 AND customers_updated >= 0 AND orders_created >= 0 AND orders_updated >= 0 AND refunds_processed >= 0 AND fulfillments_processed >= 0 AND pages_processed >= 0 AND warnings >= 0 AND failures >= 0", name="valid_counts"),
        CheckConstraint("jsonb_typeof(next_cursor) = 'object' AND pg_column_size(next_cursor) <= 16384", name="valid_next_cursor"),
        CheckConstraint("jsonb_typeof(provider_metadata) = 'object' AND pg_column_size(provider_metadata) <= 32768", name="valid_provider_metadata"),
        UniqueConstraint("id", "business_id", name="uq_commerce_sync_runs_id_business"),
        UniqueConstraint("connection_id", "idempotency_key", name="uq_commerce_sync_runs_connection_key"),
        Index("ix_commerce_sync_runs_business_started", "business_id", "started_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", server_default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    products_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    products_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    products_archived: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    variants_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    customers_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    customers_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    orders_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    orders_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    refunds_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    fulfillments_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pages_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    warnings: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_cursor: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    provider_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ExternalProductMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "external_product_mappings"
    __table_args__ = (
        ForeignKeyConstraint(["catalog_source_id", "business_id"], ["catalog_sources.id", "catalog_sources.business_id"], name="fk_external_product_mappings_source_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_external_product_mappings_item_business", ondelete="CASCADE"),
        CheckConstraint(f"provider IN {PROVIDER_SQL}", name="valid_provider"),
        CheckConstraint("sync_state IN ('in_sync','pending','local_override','external_changed','error','archived')", name="valid_sync_state"),
        CheckConstraint("jsonb_typeof(authoritative_snapshot) = 'object' AND pg_column_size(authoritative_snapshot) <= 65536", name="valid_authoritative_snapshot"),
        CheckConstraint("jsonb_typeof(safe_metadata) = 'object' AND pg_column_size(safe_metadata) <= 32768", name="valid_safe_metadata"),
        UniqueConstraint("id", "business_id", name="uq_external_product_mappings_id_business"),
        UniqueConstraint("business_id", "provider", "external_account_id", "external_object_id", name="uq_external_product_mappings_external_identity"),
        Index("ix_external_product_mappings_business_item", "business_id", "catalog_item_id", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    catalog_source_id: Mapped[UUID] = mapped_column(nullable=False)
    catalog_item_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_object_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    sync_state: Mapped[str] = mapped_column(String(24), nullable=False, default="in_sync", server_default="in_sync")
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synchronized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authoritative_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    safe_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))


class CatalogVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "catalog_variants"
    __table_args__ = (
        ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_catalog_variants_item_business", ondelete="CASCADE"),
        CheckConstraint("price IS NULL OR price >= 0", name="valid_price"),
        CheckConstraint("compare_at_price IS NULL OR compare_at_price >= 0", name="valid_compare_at_price"),
        CheckConstraint("inventory_quantity IS NULL OR inventory_quantity >= 0", name="valid_inventory_quantity"),
        CheckConstraint("jsonb_typeof(option_values) = 'object' AND pg_column_size(option_values) <= 16384", name="valid_option_values"),
        UniqueConstraint("id", "business_id", name="uq_catalog_variants_id_business"),
        UniqueConstraint("business_id", "provider", "external_account_id", "external_object_id", name="uq_catalog_variants_external_identity"),
        Index("ix_catalog_variants_business_item", "business_id", "catalog_item_id", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    catalog_item_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_object_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    compare_at_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    inventory_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    option_values: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    last_synchronized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CatalogMedia(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "catalog_media"
    __table_args__ = (
        ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_catalog_media_item_business", ondelete="CASCADE"),
        CheckConstraint("media_type IN ('image','video','document')", name="valid_media_type"),
        UniqueConstraint("id", "business_id", name="uq_catalog_media_id_business"),
        UniqueConstraint("business_id", "catalog_item_id", "source_url", name="uq_catalog_media_item_url"),
        UniqueConstraint("business_id", "provider", "external_account_id", "external_object_id", name="uq_catalog_media_external_identity"),
        Index("ix_catalog_media_business_item", "business_id", "catalog_item_id", "position", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    catalog_item_id: Mapped[UUID] = mapped_column(nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False, default="image", server_default="image")
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_object_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    alt_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class ExternalCustomerMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "external_customer_mappings"
    __table_args__ = (
        ForeignKeyConstraint(["connection_id", "business_id"], ["commerce_connections.id", "commerce_connections.business_id"], name="fk_external_customer_mappings_connection_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_external_customer_mappings_customer_business", ondelete="CASCADE"),
        UniqueConstraint("id", "business_id", name="uq_external_customer_mappings_id_business"),
        UniqueConstraint("business_id", "provider", "external_account_id", "external_object_id", name="uq_external_customer_mappings_external_identity"),
        Index("ix_external_customer_mappings_business_customer", "business_id", "customer_id", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(nullable=False)
    customer_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_object_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synchronized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExternalOrderMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "external_order_mappings"
    __table_args__ = (
        ForeignKeyConstraint(["connection_id", "business_id"], ["commerce_connections.id", "commerce_connections.business_id"], name="fk_external_order_mappings_connection_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["order_id", "business_id"], ["orders.id", "orders.business_id"], name="fk_external_order_mappings_order_business", ondelete="CASCADE"),
        UniqueConstraint("id", "business_id", name="uq_external_order_mappings_id_business"),
        UniqueConstraint("business_id", "provider", "external_account_id", "external_object_id", name="uq_external_order_mappings_external_identity"),
        Index("ix_external_order_mappings_business_order", "business_id", "order_id", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(nullable=False)
    order_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_object_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synchronized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CommerceWebhookReceipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "commerce_webhook_receipts"
    __table_args__ = (
        ForeignKeyConstraint(["connection_id", "business_id"], ["commerce_connections.id", "commerce_connections.business_id"], name="fk_commerce_webhook_receipts_connection_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["sync_run_id", "business_id"], ["commerce_sync_runs.id", "commerce_sync_runs.business_id"], name="fk_commerce_webhook_receipts_run_business"),
        CheckConstraint("status IN ('received','queued','reconciled','failed','duplicate')", name="valid_status"),
        CheckConstraint("reconciliation_domain IN ('products','customers','orders','inventory')", name="valid_reconciliation_domain"),
        UniqueConstraint("id", "business_id", name="uq_commerce_webhook_receipts_id_business"),
        UniqueConstraint("connection_id", "external_event_id", name="uq_commerce_webhook_receipts_connection_event"),
        Index("ix_commerce_webhook_receipts_business_received", "business_id", "received_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(nullable=False)
    sync_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    reconciliation_domain: Mapped[str] = mapped_column(String(32), nullable=False)
    external_object_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="received", server_default="received")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CommerceSyncIssue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "commerce_sync_issues"
    __table_args__ = (
        ForeignKeyConstraint(["sync_run_id", "business_id"], ["commerce_sync_runs.id", "commerce_sync_runs.business_id"], name="fk_commerce_sync_issues_run_business", ondelete="CASCADE"),
        CheckConstraint("severity IN ('warning','error')", name="valid_severity"),
        CheckConstraint("jsonb_typeof(safe_details) = 'object' AND pg_column_size(safe_details) <= 16384", name="valid_safe_details"),
        Index("ix_commerce_sync_issues_business_run", "business_id", "sync_run_id", "severity", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    sync_run_id: Mapped[UUID] = mapped_column(nullable=False)
    external_object_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    safe_details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))


class CommerceEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "commerce_events"
    __table_args__ = (
        ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_commerce_events_customer_business"),
        ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_commerce_events_catalog_business"),
        ForeignKeyConstraint(["order_id", "business_id"], ["orders.id", "orders.business_id"], name="fk_commerce_events_order_business"),
        CheckConstraint("event_type IN ('product_viewed','collection_viewed','search_performed','cart_created','cart_updated','checkout_started','checkout_abandoned','order_created','order_paid','order_fulfilled','order_refunded','coupon_used','chat_started','lead_captured','campaign_clicked')", name="valid_event_type"),
        CheckConstraint("source ~ '^[a-z][a-z0-9_]{0,31}$'", name="valid_source"),
        CheckConstraint("jsonb_typeof(safe_metadata) = 'object' AND pg_column_size(safe_metadata) <= 32768", name="valid_safe_metadata"),
        UniqueConstraint("id", "business_id", name="uq_commerce_events_id_business"),
        UniqueConstraint("business_id", "source", "external_event_id", name="uq_commerce_events_source_external"),
        Index("ix_commerce_events_business_occurred", "business_id", "occurred_at", "id"),
        Index("ix_commerce_events_business_customer", "business_id", "customer_id", "occurred_at", "id"),
        Index("ix_commerce_events_business_type", "business_id", "event_type", "occurred_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    customer_id: Mapped[UUID | None] = mapped_column(nullable=True)
    catalog_item_id: Mapped[UUID | None] = mapped_column(nullable=True)
    order_id: Mapped[UUID | None] = mapped_column(nullable=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    anonymous_session_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    safe_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))


class AudienceSegment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audience_segments"
    __table_args__ = (
        CheckConstraint("source_classification IN ('first_party_observed','platform_supplied','public_research','ai_inference')", name="valid_source_classification"),
        CheckConstraint("status IN ('draft','active','paused','archived')", name="valid_status"),
        CheckConstraint("jsonb_typeof(rule) = 'object' AND pg_column_size(rule) <= 32768", name="valid_rule"),
        CheckConstraint("matched_customer_count >= 0", name="valid_matched_customer_count"),
        UniqueConstraint("id", "business_id", name="uq_audience_segments_id_business"),
        Index("ix_audience_segments_business_status", "business_id", "status", "updated_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    natural_language_definition: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    source_classification: Mapped[str] = mapped_column(String(32), nullable=False, default="first_party_observed", server_default="first_party_observed")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    matched_customer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AudienceSegmentMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audience_segment_members"
    __table_args__ = (
        ForeignKeyConstraint(["segment_id", "business_id"], ["audience_segments.id", "audience_segments.business_id"], name="fk_audience_segment_members_segment_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_audience_segment_members_customer_business", ondelete="CASCADE"),
        UniqueConstraint("business_id", "segment_id", "customer_id", name="uq_audience_segment_members_segment_customer"),
        Index("ix_audience_segment_members_business_customer", "business_id", "customer_id", "segment_id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    segment_id: Mapped[UUID] = mapped_column(nullable=False)
    customer_id: Mapped[UUID] = mapped_column(nullable=False)
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_summary: Mapped[str] = mapped_column(String(500), nullable=False)


class CommerceFeedDestination(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "commerce_feed_destinations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["integration_connection_id", "business_id"],
            ["integration_connections.id", "integration_connections.business_id"],
            name="fk_feed_destinations_integration_business",
        ),
        CheckConstraint("provider IN ('google_merchant_center','meta_product_catalog')", name="valid_provider"),
        CheckConstraint("status IN ('configuration_required','connection_required','connected','syncing','attention_required','disabled')", name="valid_status"),
        CheckConstraint("eligible_count >= 0 AND warning_count >= 0 AND rejected_count >= 0 AND synchronized_count >= 0 AND submitted_count >= 0 AND limited_count >= 0", name="valid_counts"),
        CheckConstraint("jsonb_typeof(safe_metadata) = 'object' AND pg_column_size(safe_metadata) <= 32768", name="valid_safe_metadata"),
        UniqueConstraint("id", "business_id", name="uq_commerce_feed_destinations_id_business"),
        UniqueConstraint("business_id", "provider", "external_account_id", name="uq_commerce_feed_destinations_provider_account"),
        Index("ix_commerce_feed_destinations_business_status", "business_id", "status", "provider", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    integration_connection_id: Mapped[UUID | None] = mapped_column(nullable=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    external_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_resource_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    managed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    content_language: Mapped[str] = mapped_column(String(16), nullable=False, default="en", server_default="en")
    feed_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="configuration_required", server_default="configuration_required")
    synchronized_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    submitted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    eligible_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    limited_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_synchronized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))


class CommerceFeedProductStatus(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "commerce_feed_product_statuses"
    __table_args__ = (
        ForeignKeyConstraint(["destination_id", "business_id"], ["commerce_feed_destinations.id", "commerce_feed_destinations.business_id"], name="fk_commerce_feed_product_statuses_destination_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_commerce_feed_product_statuses_item_business", ondelete="CASCADE"),
        CheckConstraint("status IN ('attention_required','pending','submitted','processing','eligible','limited','warning','ineligible','rejected','error','archived','removed')", name="valid_status"),
        CheckConstraint("cardinality(missing_attributes) <= 50 AND cardinality(warnings) <= 50", name="valid_issue_counts"),
        UniqueConstraint("business_id", "destination_id", "catalog_item_id", name="uq_commerce_feed_product_statuses_destination_item"),
        Index("ix_commerce_feed_product_statuses_business_status", "business_id", "destination_id", "status", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    destination_id: Mapped[UUID] = mapped_column(nullable=False)
    catalog_item_id: Mapped[UUID] = mapped_column(nullable=False)
    external_product_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    missing_attributes: Mapped[list[str]] = mapped_column(ARRAY(String(100)), nullable=False, default=list, server_default="{}")
    warnings: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False, default=list, server_default="{}")
    provider_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_issues: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    owned_by_aibos: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synchronized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProductGroup(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "commerce_product_groups"
    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 160", name="valid_name"),
        CheckConstraint("group_type IN ('manual','category','collection','brand','tag','price','margin','best_sellers','new_products','promotion','custom_rule')", name="valid_group_type"),
        CheckConstraint("status IN ('draft','active','archived')", name="valid_status"),
        CheckConstraint("jsonb_typeof(rule) = 'object' AND pg_column_size(rule) <= 16384", name="valid_rule"),
        UniqueConstraint("id", "business_id", name="uq_commerce_product_groups_id_business"),
        UniqueConstraint("business_id", "external_key", name="uq_commerce_product_groups_external_key"),
        Index("ix_commerce_product_groups_business_status", "business_id", "status", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    external_key: Mapped[str] = mapped_column(String(160), nullable=False)
    group_type: Mapped[str] = mapped_column(String(32), nullable=False)
    rule: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ProductGroupItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "commerce_product_group_items"
    __table_args__ = (
        ForeignKeyConstraint(["product_group_id", "business_id"], ["commerce_product_groups.id", "commerce_product_groups.business_id"], name="fk_product_group_items_group_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_product_group_items_item_business", ondelete="CASCADE"),
        UniqueConstraint("business_id", "product_group_id", "catalog_item_id", name="uq_product_group_items_group_item"),
        Index("ix_product_group_items_business_item", "business_id", "catalog_item_id", "product_group_id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    product_group_id: Mapped[UUID] = mapped_column(nullable=False)
    catalog_item_id: Mapped[UUID] = mapped_column(nullable=False)


class ProductGroupDestination(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "commerce_product_group_destinations"
    __table_args__ = (
        ForeignKeyConstraint(["product_group_id", "business_id"], ["commerce_product_groups.id", "commerce_product_groups.business_id"], name="fk_product_group_destinations_group_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["destination_id", "business_id"], ["commerce_feed_destinations.id", "commerce_feed_destinations.business_id"], name="fk_product_group_destinations_destination_business", ondelete="CASCADE"),
        CheckConstraint("status IN ('pending','submitted','ready','attention_required','archived')", name="valid_status"),
        UniqueConstraint("business_id", "product_group_id", "destination_id", name="uq_product_group_destinations_group_destination"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    product_group_id: Mapped[UUID] = mapped_column(nullable=False)
    destination_id: Mapped[UUID] = mapped_column(nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending")
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
