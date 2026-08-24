from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, String, Text, UniqueConstraint
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
        CheckConstraint("total = subtotal + adjustment_amount", name="valid_total"),
        CheckConstraint("notes IS NULL OR char_length(notes) <= 4000", name="valid_notes"),
        Index("ix_orders_business_status_created", "business_id", "status", "created_at", "id"),
        Index("ix_orders_business_customer_created", "business_id", "customer_id", "created_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    customer_id: Mapped[UUID] = mapped_column(nullable=False)
    order_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual", server_default="manual")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    adjustment_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0")
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderLineItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "order_line_items"
    __table_args__ = (
        ForeignKeyConstraint(["order_id", "business_id"], ["orders.id", "orders.business_id"], name="fk_order_line_items_order_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_order_line_items_catalog_business"),
        CheckConstraint("char_length(btrim(description)) BETWEEN 1 AND 300", name="valid_description"),
        CheckConstraint("quantity BETWEEN 1 AND 100000", name="valid_quantity"),
        CheckConstraint("unit_price >= 0 AND unit_price <= 999999999999.99", name="valid_unit_price"),
        Index("ix_order_line_items_business_order", "business_id", "order_id", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    order_id: Mapped[UUID] = mapped_column(nullable=False)
    catalog_item_id: Mapped[UUID | None] = mapped_column(nullable=True)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
