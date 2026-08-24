from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ServiceProvider(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A generic person or resource that can deliver bookable services."""

    __tablename__ = "service_providers"
    __table_args__ = (
        UniqueConstraint("id", "business_id", name="uq_service_providers_id_business"),
        CheckConstraint(
            "char_length(btrim(display_name)) BETWEEN 1 AND 160",
            name="valid_display_name",
        ),
        CheckConstraint(
            "provider_type ~ '^[a-z][a-z0-9_]{0,31}$'",
            name="valid_provider_type",
        ),
        CheckConstraint(
            "title IS NULL OR char_length(btrim(title)) BETWEEN 1 AND 120",
            name="valid_title",
        ),
        CheckConstraint(
            "specialty IS NULL OR char_length(btrim(specialty)) BETWEEN 1 AND 160",
            name="valid_specialty",
        ),
        CheckConstraint(
            "char_length(btrim(timezone)) BETWEEN 1 AND 64",
            name="valid_timezone",
        ),
        CheckConstraint(
            "location_reference IS NULL OR "
            "char_length(btrim(location_reference)) BETWEEN 1 AND 100",
            name="valid_location_reference",
        ),
        Index("ix_service_providers_business_active", "business_id", "active", "id"),
        Index("ix_service_providers_business_type", "business_id", "provider_type", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    specialty: Mapped[str | None] = mapped_column(String(160), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    location_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
