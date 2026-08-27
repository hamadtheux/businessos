from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.business_branding import BusinessBranding
    from app.models.business_knowledge_entry import BusinessKnowledgeEntry
    from app.models.business_memory import BusinessMemory
    from app.models.catalog_item import CatalogItem


class Business(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "businesses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'inactive', 'suspended')",
            name="valid_status",
        ),
        CheckConstraint(
            "jsonb_typeof(avoid_keywords) = 'array' AND jsonb_array_length(avoid_keywords) <= 100",
            name="valid_avoid_keywords",
        ),
    )

    name: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )

    slug: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        unique=True,
        index=True,
    )

    business_type: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        server_default="active",
        index=True,
    )

    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="UTC",
        server_default="UTC",
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
        server_default="USD",
    )

    locale: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="en",
        server_default="en",
    )

    website_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    location: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    brand_voice: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    avoid_keywords: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )

    branding: Mapped["BusinessBranding | None"] = relationship(
        back_populates="business",
        cascade="all, delete-orphan",
        passive_deletes=True,
        single_parent=True,
        uselist=False,
        lazy="select",
    )

    catalog_items: Mapped[list["CatalogItem"]] = relationship(
        back_populates="business",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="select",
    )

    knowledge_entries: Mapped[list["BusinessKnowledgeEntry"]] = relationship(
        back_populates="business",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="select",
    )

    memories: Mapped[list["BusinessMemory"]] = relationship(
        back_populates="business",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="select",
    )
