from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class AppointmentType(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-owned bookable service and its deterministic booking policy."""

    __tablename__ = "appointment_types"
    __table_args__ = (
        UniqueConstraint("id", "business_id", name="uq_appointment_types_id_business"),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 160", name="valid_name"),
        CheckConstraint(
            "description IS NULL OR char_length(description) <= 2000",
            name="valid_description",
        ),
        CheckConstraint("duration_minutes BETWEEN 5 AND 1440", name="valid_duration"),
        CheckConstraint(
            "buffer_before_minutes BETWEEN 0 AND 720", name="valid_buffer_before"
        ),
        CheckConstraint(
            "buffer_after_minutes BETWEEN 0 AND 720", name="valid_buffer_after"
        ),
        CheckConstraint(
            "slot_interval_minutes BETWEEN 5 AND 1440", name="valid_slot_interval"
        ),
        CheckConstraint(
            "minimum_notice_minutes BETWEEN 0 AND 525600", name="valid_minimum_notice"
        ),
        CheckConstraint(
            "maximum_future_days BETWEEN 1 AND 730", name="valid_future_horizon"
        ),
        CheckConstraint(
            "cancellation_cutoff_minutes BETWEEN 0 AND 525600",
            name="valid_cancellation_cutoff",
        ),
        CheckConstraint(
            "reschedule_cutoff_minutes BETWEEN 0 AND 525600",
            name="valid_reschedule_cutoff",
        ),
        Index("ix_appointment_types_business_active", "business_id", "active", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    buffer_before_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    buffer_after_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    slot_interval_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=15, server_default="15"
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    minimum_notice_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    maximum_future_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=365, server_default="365"
    )
    allow_same_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    cancellation_cutoff_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reschedule_cutoff_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
