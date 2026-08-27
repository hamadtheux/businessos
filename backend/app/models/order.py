from decimal import Decimal
from uuid import UUID

from datetime import datetime

from sqlalchemy import ARRAY, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_orders_customer_business"),
        UniqueConstraint("id", "business_id", name="uq_orders_id_business"),
        UniqueConstraint("business_id", "order_number", name="uq_orders_business_number"),
        CheckConstraint("char_length(btrim(order_number)) BETWEEN 1 AND 40", name="valid_order_number"),
        CheckConstraint("status IN ('draft','confirmed','processing','completed','canceled')", name="valid_status"),
        CheckConstraint("source ~ '^[a-z][a-z0-9_]{0,31}$'", name="valid_source"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="valid_currency"),
        CheckConstraint("subtotal >= 0 AND subtotal <= 999999999999.99", name="valid_subtotal"),
        CheckConstraint("adjustment_amount >= 0 AND adjustment_amount <= 999999999999.99", name="valid_adjustment"),
        CheckConstraint("total >= 0 AND total <= 999999999999.99", name="valid_total"),
        CheckConstraint("discount_amount >= 0 AND tax_amount >= 0 AND shipping_amount >= 0 AND refunded_amount >= 0", name="valid_commerce_amounts"),
        CheckConstraint("payment_status IN ('unknown','pending','authorized','paid','partially_refunded','refunded','voided','failed')", name="valid_payment_status"),
        CheckConstraint("fulfillment_status IN ('unknown','unfulfilled','partial','fulfilled','canceled')", name="valid_fulfillment_status"),
        CheckConstraint("notes IS NULL OR char_length(notes) <= 4000", name="valid_notes"),
        Index("ix_orders_business_status_created", "business_id", "status", "created_at", "id"),
        Index("ix_orders_business_customer_created", "business_id", "customer_id", "created_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    customer_id: Mapped[UUID | None] = mapped_column(nullable=True)
    order_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual", server_default="manual")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    adjustment_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0")
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0")
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0")
    shipping_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0")
    refunded_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0")
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    payment_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown", server_default="unknown")
    fulfillment_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown", server_default="unknown")
    provider_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderLineItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "order_line_items"
    __table_args__ = (
        ForeignKeyConstraint(["order_id", "business_id"], ["orders.id", "orders.business_id"], name="fk_order_line_items_order_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_order_line_items_catalog_business"),
        CheckConstraint("char_length(btrim(description)) BETWEEN 1 AND 300", name="valid_description"),
        CheckConstraint("quantity BETWEEN 1 AND 100000", name="valid_quantity"),
        CheckConstraint("unit_price >= 0 AND unit_price <= 999999999999.99", name="valid_unit_price"),
        CheckConstraint("discount_amount >= 0 AND tax_amount >= 0", name="valid_commerce_amounts"),
        UniqueConstraint("business_id", "order_id", "external_object_id", name="uq_order_line_items_external_identity"),
        Index("ix_order_line_items_business_order", "business_id", "order_id", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    order_id: Mapped[UUID] = mapped_column(nullable=False)
    catalog_item_id: Mapped[UUID | None] = mapped_column(nullable=True)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    external_object_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_variant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0")
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0")


class OrderAddress(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "order_addresses"
    __table_args__ = (
        ForeignKeyConstraint(["order_id", "business_id"], ["orders.id", "orders.business_id"], name="fk_order_addresses_order_business", ondelete="CASCADE"),
        CheckConstraint("address_type IN ('billing','shipping')", name="valid_address_type"),
        UniqueConstraint("business_id", "order_id", "address_type", name="uq_order_addresses_order_type"),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    order_id: Mapped[UUID] = mapped_column(nullable=False)
    address_type: Mapped[str] = mapped_column(String(16), nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    company: Mapped[str | None] = mapped_column(String(160), nullable=True)
    address1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)


class OrderRefund(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "order_refunds"
    __table_args__ = (
        ForeignKeyConstraint(["order_id", "business_id"], ["orders.id", "orders.business_id"], name="fk_order_refunds_order_business", ondelete="CASCADE"),
        UniqueConstraint("id", "business_id", name="uq_order_refunds_id_business"),
        UniqueConstraint("business_id", "order_id", "external_object_id", name="uq_order_refunds_external_identity"),
        UniqueConstraint("business_id", "provider", "external_account_id", "external_object_id", name="uq_order_refunds_provider_identity"),
        CheckConstraint("amount >= 0", name="valid_amount"),
        CheckConstraint("provider ~ '^[a-z][a-z0-9_]{0,31}$'", name="valid_provider"),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    order_id: Mapped[UUID] = mapped_column(nullable=False)
    external_object_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class OrderRefundLine(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "order_refund_lines"
    __table_args__ = (
        ForeignKeyConstraint(["refund_id", "business_id"], ["order_refunds.id", "order_refunds.business_id"], name="fk_order_refund_lines_refund_business", ondelete="CASCADE"),
        CheckConstraint("quantity >= 1 AND amount >= 0", name="valid_values"),
        Index("ix_order_refund_lines_business_refund", "business_id", "refund_id", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    refund_id: Mapped[UUID] = mapped_column(nullable=False)
    external_order_line_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class OrderFulfillment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "order_fulfillments"
    __table_args__ = (
        ForeignKeyConstraint(["order_id", "business_id"], ["orders.id", "orders.business_id"], name="fk_order_fulfillments_order_business", ondelete="CASCADE"),
        UniqueConstraint("id", "business_id", name="uq_order_fulfillments_id_business"),
        UniqueConstraint("business_id", "order_id", "external_object_id", name="uq_order_fulfillments_external_identity"),
        UniqueConstraint("business_id", "provider", "external_account_id", "external_object_id", name="uq_order_fulfillments_provider_identity"),
        CheckConstraint("status IN ('pending','open','in_progress','fulfilled','canceled','failed')", name="valid_status"),
        CheckConstraint("cardinality(external_order_line_ids) <= 500", name="valid_line_count"),
        CheckConstraint("provider ~ '^[a-z][a-z0-9_]{0,31}$'", name="valid_provider"),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    order_id: Mapped[UUID] = mapped_column(nullable=False)
    external_object_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tracking_company: Mapped[str | None] = mapped_column(String(160), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tracking_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    external_order_line_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default="{}",
    )
