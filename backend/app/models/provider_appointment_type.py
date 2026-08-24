from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ProviderAppointmentType(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_appointment_types"
    __table_args__ = (
        ForeignKeyConstraint(
            ["provider_id", "business_id"],
            ["service_providers.id", "service_providers.business_id"],
            name="fk_provider_appointment_types_provider_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["appointment_type_id", "business_id"],
            ["appointment_types.id", "appointment_types.business_id"],
            name="fk_provider_appointment_types_type_business",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "business_id",
            "provider_id",
            "appointment_type_id",
            name="uq_provider_appointment_types_assignment",
        ),
        Index(
            "ix_provider_appointment_types_business_type",
            "business_id",
            "appointment_type_id",
            "provider_id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(nullable=False)
    provider_id: Mapped[UUID] = mapped_column(nullable=False)
    appointment_type_id: Mapped[UUID] = mapped_column(nullable=False)
