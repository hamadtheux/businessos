from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


if TYPE_CHECKING:
    from app.models.user import User


class AuthSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint(
            "char_length(token_hash) = 64 "
            "AND token_hash ~ '^[0-9a-f]{64}$'",
            name="valid_token_hash",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    family_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    replaced_by_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        back_populates="sessions",
        lazy="select",
    )
