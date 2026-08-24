from __future__ import annotations

from datetime import date, time
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKeyConstraint, Index, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ProviderAvailabilityException(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_availability_exceptions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["provider_id", "business_id"],
            ["service_providers.id", "service_providers.business_id"],
            name="fk_provider_availability_exceptions_provider_business",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "exception_kind IN ('unavailable', 'available_override')",
            name="valid_exception_kind",
        ),
        CheckConstraint(
            "(whole_day AND start_local_time IS NULL AND end_local_time IS NULL) OR "
            "(NOT whole_day AND start_local_time IS NOT NULL "
            "AND end_local_time IS NOT NULL AND start_local_time < end_local_time)",
            name="valid_exception_window",
        ),
        Index(
            "ix_provider_availability_exceptions_business_provider_date",
            "business_id",
            "provider_id",
            "exception_date",
            "active",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    provider_id: Mapped[UUID] = mapped_column(nullable=False)
    exception_date: Mapped[date] = mapped_column(Date, nullable=False)
    exception_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    whole_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    start_local_time: Mapped[time | None] = mapped_column(Time(timezone=False), nullable=True)
    end_local_time: Mapped[time | None] = mapped_column(Time(timezone=False), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
