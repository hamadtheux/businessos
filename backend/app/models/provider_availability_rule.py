from __future__ import annotations

from datetime import date, time
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKeyConstraint, Index, SmallInteger, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ProviderAvailabilityRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_availability_rules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["provider_id", "business_id"],
            ["service_providers.id", "service_providers.business_id"],
            name="fk_provider_availability_rules_provider_business",
            ondelete="CASCADE",
        ),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="valid_weekday"),
        CheckConstraint("start_local_time < end_local_time", name="valid_time_window"),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from",
            name="valid_date_range",
        ),
        Index(
            "ix_provider_availability_rules_business_provider_weekday",
            "business_id",
            "provider_id",
            "weekday",
            "active",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    provider_id: Mapped[UUID] = mapped_column(nullable=False)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_local_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    end_local_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
