from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class BusinessMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "business_memberships"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "user_id",
            name="uq_business_memberships_business_user",
        ),
        CheckConstraint(
            "status IN ('invited', 'active', 'suspended')",
            name="valid_status",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="member",
        server_default="member",
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        server_default="active",
        index=True,
    )
