from __future__ import annotations

from uuid import UUID

from sqlalchemy import ARRAY, Boolean, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Customer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Minimal tenant-owned customer identity; intentionally contains no clinical data."""

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("id", "business_id", name="uq_customers_id_business"),
        CheckConstraint(
            "char_length(btrim(display_name)) BETWEEN 1 AND 160",
            name="valid_display_name",
        ),
        CheckConstraint(
            "first_name IS NULL OR char_length(btrim(first_name)) BETWEEN 1 AND 80",
            name="valid_first_name",
        ),
        CheckConstraint(
            "last_name IS NULL OR char_length(btrim(last_name)) BETWEEN 1 AND 80",
            name="valid_last_name",
        ),
        CheckConstraint(
            "email IS NULL OR char_length(btrim(email)) BETWEEN 3 AND 320",
            name="valid_email",
        ),
        CheckConstraint(
            "phone IS NULL OR char_length(btrim(phone)) BETWEEN 3 AND 32",
            name="valid_phone",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive', 'archived')",
            name="valid_status",
        ),
        CheckConstraint(
            "source ~ '^[a-z][a-z0-9_]{0,31}$'",
            name="valid_source",
        ),
        CheckConstraint(
            "company IS NULL OR char_length(btrim(company)) BETWEEN 1 AND 160",
            name="valid_company",
        ),
        CheckConstraint(
            "notes IS NULL OR char_length(notes) <= 4000",
            name="valid_notes",
        ),
        CheckConstraint(
            "cardinality(tags) <= 20",
            name="valid_tag_count",
        ),
        Index("ix_customers_business_active", "business_id", "active", "id"),
        Index("ix_customers_business_status_updated", "business_id", "status", "updated_at", "id"),
        Index("ix_customers_business_email", "business_id", "email"),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", server_default="active"
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual", server_default="manual"
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), nullable=False, default=list, server_default=text("'{}'::varchar[]")
    )
    company: Mapped[str | None] = mapped_column(String(160), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
