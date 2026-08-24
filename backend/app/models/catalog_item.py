from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
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
