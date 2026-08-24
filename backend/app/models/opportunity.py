from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Opportunity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_opportunities_customer_business"),
        ForeignKeyConstraint(["lead_id", "business_id"], ["crm_leads.id", "crm_leads.business_id"], name="fk_opportunities_lead_business"),
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 180", name="valid_title"),
        CheckConstraint("char_length(description) BETWEEN 1 AND 3000", name="valid_description"),
        CheckConstraint("category ~ '^[a-z][a-z0-9_]{0,47}$'", name="valid_category"),
        CheckConstraint("source ~ '^[a-z][a-z0-9_]{0,31}$'", name="valid_source"),
        CheckConstraint("priority IN ('low','medium','high','urgent')", name="valid_priority"),
        CheckConstraint("status IN ('open','in_progress','won','lost','dismissed')", name="valid_status"),
        CheckConstraint("estimated_value IS NULL OR (estimated_value >= 0 AND estimated_value <= 999999999999.99)", name="valid_estimated_value"),
        CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="valid_currency"),
        UniqueConstraint("id", "business_id", name="uq_opportunities_id_business"),
        Index("ix_opportunities_business_status_updated", "business_id", "status", "updated_at", "id"),
        Index("ix_opportunities_business_priority", "business_id", "priority", "id"),
        UniqueConstraint("business_id", "dedupe_key", name="uq_opportunities_business_dedupe_key"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium", server_default="medium")
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", server_default="open")
    customer_id: Mapped[UUID | None] = mapped_column(nullable=True)
    lead_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source_entity_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    source_entity_id: Mapped[UUID | None] = mapped_column(nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provenance: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
