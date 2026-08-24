from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Appointment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Administrative booking record; never stores diagnosis or clinical notes."""

    __tablename__ = "appointments"

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[UUID] = mapped_column(nullable=False)
    appointment_type_id: Mapped[UUID] = mapped_column(nullable=False)
    customer_id: Mapped[UUID | None] = mapped_column(nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="confirmed", server_default="confirmed"
    )
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancellation_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["provider_id", "business_id"],
            ["service_providers.id", "service_providers.business_id"],
            name="fk_appointments_provider_business",
        ),
        ForeignKeyConstraint(
            ["appointment_type_id", "business_id"],
            ["appointment_types.id", "appointment_types.business_id"],
            name="fk_appointments_type_business",
        ),
        ForeignKeyConstraint(
            ["customer_id", "business_id"],
            ["customers.id", "customers.business_id"],
            name="fk_appointments_customer_business",
        ),
        UniqueConstraint("id", "business_id", name="uq_appointments_id_business"),
        CheckConstraint("ends_at > starts_at", name="valid_time_range"),
        CheckConstraint(
            "status IN ('confirmed', 'canceled', 'completed', 'no_show')",
            name="valid_status",
        ),
        CheckConstraint(
            "source IN ('manual', 'api', 'ai', 'website', 'whatsapp', 'import')",
            name="valid_source",
        ),
        CheckConstraint(
            "(status = 'canceled' AND cancellation_reason_code IS NOT NULL) OR "
            "(status <> 'canceled' AND cancellation_reason_code IS NULL)",
            name="consistent_cancellation",
        ),
        CheckConstraint(
            "cancellation_reason_code IS NULL OR "
            "cancellation_reason_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="valid_cancellation_reason_code",
        ),
        ExcludeConstraint(
            ("provider_id", "="),
            (func.tstzrange(starts_at, ends_at, "[)"), "&&"),
            where=text("status = 'confirmed'"),
            using="gist",
            name="ex_appointments_provider_time_overlap",
        ),
        Index(
            "ix_appointments_business_provider_start",
            "business_id",
            "provider_id",
            "starts_at",
            "id",
        ),
        Index(
            "ix_appointments_business_customer_start",
            "business_id",
            "customer_id",
            "starts_at",
            "id",
        ),
        Index(
            "ix_appointments_business_status_start",
            "business_id",
            "status",
            "starts_at",
            "id",
        ),
    )
