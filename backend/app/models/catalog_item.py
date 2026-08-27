from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Integer,
    Boolean,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.business import Business


class CatalogItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "catalog_items"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_catalog_items_id_business",
        ),
        CheckConstraint(
            "item_type IN ('product', 'service')",
            name="valid_item_type",
        ),
        CheckConstraint(
            "status IN ('active', 'draft', 'archived')",
            name="valid_status",
        ),
        CheckConstraint(
            "price IS NULL OR (price >= 0 AND price <= 999999999999.99)",
            name="valid_price",
        ),
        CheckConstraint(
            "compare_at_price IS NULL OR (compare_at_price >= 0 AND compare_at_price <= 999999999999.99)",
            name="valid_compare_at_price",
        ),
        CheckConstraint(
            "cost IS NULL OR (cost >= 0 AND cost <= 999999999999.99)",
            name="valid_cost",
        ),
        CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="valid_currency",
        ),
        CheckConstraint(
            "inventory_quantity IS NULL OR inventory_quantity BETWEEN 0 AND 2147483647",
            name="valid_inventory_quantity",
        ),
        CheckConstraint(
            "availability IN ('unknown','in_stock','out_of_stock','preorder','backorder')",
            name="valid_availability",
        ),
        CheckConstraint(
            "condition IN ('new','refurbished','used')",
            name="valid_condition",
        ),
        CheckConstraint(
            "source ~ '^[a-z][a-z0-9_]{0,31}$'",
            name="valid_source",
        ),
        CheckConstraint(
            "sync_state IN ('manual','in_sync','pending','local_override','external_changed','error')",
            name="valid_sync_state",
        ),
        CheckConstraint("cardinality(tags) <= 100", name="valid_tag_count"),
        CheckConstraint(
            "jsonb_typeof(provider_metadata) = 'object' AND pg_column_size(provider_metadata) <= 32768",
            name="valid_provider_metadata",
        ),
        UniqueConstraint(
            "business_id",
            "sku",
            name="uq_catalog_items_business_sku",
        ),
        Index("ix_catalog_items_business_status", "business_id", "status"),
        Index("ix_catalog_items_business_item_type", "business_id", "item_type"),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    sku: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    price: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2),
        nullable=True,
    )
    compare_at_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    product_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    inventory_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    availability: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unknown", server_default="unknown"
    )
    brand: Mapped[str | None] = mapped_column(String(160), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(160), nullable=True)
    gtin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mpn: Mapped[str | None] = mapped_column(String(100), nullable=True)
    condition: Mapped[str] = mapped_column(
        String(16), nullable=False, default="new", server_default="new"
    )
    google_product_category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(80)), nullable=False, default=list, server_default="{}"
    )
    published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual", server_default="manual"
    )
    sync_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="manual", server_default="manual"
    )
    last_synchronized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
        server_default="active",
    )

    business: Mapped["Business"] = relationship(
        back_populates="catalog_items",
        lazy="select",
    )
