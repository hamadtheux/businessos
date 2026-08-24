from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CRMLead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "crm_leads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["customer_id", "business_id"],
            ["customers.id", "customers.business_id"],
            name="fk_crm_leads_customer_business",
        ),
        UniqueConstraint("id", "business_id", name="uq_crm_leads_id_business"),
        CheckConstraint("char_length(btrim(display_name)) BETWEEN 1 AND 160", name="valid_display_name"),
        CheckConstraint("stage IN ('new','qualified','contacted','viewing','proposal','won','lost')", name="valid_stage"),
        CheckConstraint("priority IN ('low','medium','high','urgent')", name="valid_priority"),
        CheckConstraint("qualification_state IN ('unqualified','qualifying','qualified','disqualified')", name="valid_qualification_state"),
        CheckConstraint("source ~ '^[a-z][a-z0-9_]{0,31}$'", name="valid_source"),
        CheckConstraint("estimated_value IS NULL OR (estimated_value >= 0 AND estimated_value <= 999999999999.99)", name="valid_estimated_value"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="valid_currency"),
        CheckConstraint("notes IS NULL OR char_length(notes) <= 4000", name="valid_notes"),
        CheckConstraint("email IS NULL OR char_length(btrim(email)) BETWEEN 3 AND 320", name="valid_email"),
        CheckConstraint("phone IS NULL OR char_length(btrim(phone)) BETWEEN 3 AND 32", name="valid_phone"),
        Index("ix_crm_leads_business_stage_updated", "business_id", "stage", "updated_at", "id"),
        Index("ix_crm_leads_business_follow_up", "business_id", "next_follow_up_at", "id"),
        Index("ix_crm_leads_business_owner", "business_id", "owner_user_id", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    customer_id: Mapped[UUID | None] = mapped_column(nullable=True)
    owner_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    company: Mapped[str | None] = mapped_column(String(160), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default="new", server_default="new")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual", server_default="manual")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium", server_default="medium")
    qualification_state: Mapped[str] = mapped_column(String(24), nullable=False, default="unqualified", server_default="unqualified")
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    expected_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
