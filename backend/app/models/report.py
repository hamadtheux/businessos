from datetime import date, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class BusinessReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "business_reports"
    __table_args__ = (
        CheckConstraint("report_type IN ('daily_operations','sales','customer','scheduling','marketing')", name="valid_report_type"),
        CheckConstraint("status IN ('ready','failed')", name="valid_status"),
        CheckConstraint("period_end >= period_start", name="valid_period"),
        CheckConstraint("char_length(summary) BETWEEN 1 AND 2000", name="valid_summary"),
        CheckConstraint("jsonb_typeof(metrics) = 'object'", name="valid_metrics"),
        Index("ix_business_reports_business_type_period", "business_id", "report_type", "period_end", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
