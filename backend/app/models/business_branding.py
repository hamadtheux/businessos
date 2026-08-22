from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


if TYPE_CHECKING:
    from app.models.business import Business


class BusinessBranding(TimestampMixin, Base):
    __tablename__ = "business_branding"
    __table_args__ = (
        CheckConstraint(
            "primary_color IS NULL OR primary_color ~ '^#[0-9A-Fa-f]{6}$'",
            name="valid_primary_color",
        ),
        CheckConstraint(
            "secondary_color IS NULL OR secondary_color ~ '^#[0-9A-Fa-f]{6}$'",
            name="valid_secondary_color",
        ),
        CheckConstraint(
            "accent_color IS NULL OR accent_color ~ '^#[0-9A-Fa-f]{6}$'",
            name="valid_accent_color",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"),
        primary_key=True,
    )

    logo_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    logo_storage_key: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )

    primary_color: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
    )

    secondary_color: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
    )

    accent_color: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
    )

    business: Mapped["Business"] = relationship(
        back_populates="branding",
        lazy="select",
    )
