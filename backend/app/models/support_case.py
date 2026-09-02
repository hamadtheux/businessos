from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class SupportCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Operational support state layered on top of the canonical conversation."""

    __tablename__ = "support_cases"
    __table_args__ = (
        ForeignKeyConstraint(["conversation_id", "business_id"], ["conversations.id", "conversations.business_id"], name="fk_support_cases_conversation_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_support_cases_customer_business"),
        ForeignKeyConstraint(["integration_connection_id", "business_id"], ["integration_connections.id", "integration_connections.business_id"], name="fk_support_cases_integration_business"),
        ForeignKeyConstraint(["related_order_id", "business_id"], ["orders.id", "orders.business_id"], name="fk_support_cases_order_business"),
        ForeignKeyConstraint(["related_product_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_support_cases_product_business"),
        ForeignKeyConstraint(["related_lead_id", "business_id"], ["crm_leads.id", "crm_leads.business_id"], name="fk_support_cases_lead_business"),
        UniqueConstraint("id", "business_id", name="uq_support_cases_id_business"),
        UniqueConstraint("business_id", "case_number", name="uq_support_cases_business_number"),
        CheckConstraint("status IN ('new','open','ai_handling','waiting_for_customer','waiting_for_business','escalated','resolved','closed')", name="valid_status"),
        CheckConstraint("priority IN ('low','medium','high','urgent')", name="valid_priority"),
        CheckConstraint("category IN ('general','order','delivery','return','refund','product','account','appointment','technical','complaint','payment')", name="valid_category"),
        CheckConstraint("source ~ '^[a-z][a-z0-9_]{0,31}$'", name="valid_source"),
        CheckConstraint("char_length(btrim(case_number)) BETWEEN 1 AND 40", name="valid_case_number"),
        CheckConstraint("char_length(btrim(issue_summary)) BETWEEN 1 AND 500", name="valid_issue_summary"),
        CheckConstraint("escalation_reason IS NULL OR char_length(escalation_reason) <= 1000", name="valid_escalation_reason"),
        CheckConstraint("resolution_summary IS NULL OR char_length(resolution_summary) <= 2000", name="valid_resolution_summary"),
        CheckConstraint("assigned_ai_role IS NULL OR assigned_ai_role = 'support'", name="valid_assigned_ai_role"),
        CheckConstraint("resolved_at IS NULL OR resolved_at >= opened_at", name="valid_resolved_at"),
        CheckConstraint("closed_at IS NULL OR closed_at >= opened_at", name="valid_closed_at"),
        Index("ix_support_cases_business_status_activity", "business_id", "status", "last_activity_at", "id"),
        Index("ix_support_cases_business_priority_activity", "business_id", "priority", "last_activity_at", "id"),
        Index("ix_support_cases_business_customer", "business_id", "customer_id", "id"),
        Index(
            "uq_support_cases_active_conversation",
            "business_id",
            "conversation_id",
            unique=True,
            postgresql_where=text("status NOT IN ('resolved','closed')"),
        ),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    case_number: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_id: Mapped[UUID | None] = mapped_column(nullable=True)
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    integration_connection_id: Mapped[UUID | None] = mapped_column(nullable=True)
    assigned_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_ai_role: Mapped[str | None] = mapped_column(String(32), nullable=True, default="support", server_default="support")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new", server_default="new")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium", server_default="medium")
    category: Mapped[str] = mapped_column(String(24), nullable=False, default="general", server_default="general")
    issue_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    related_order_id: Mapped[UUID | None] = mapped_column(nullable=True)
    related_product_id: Mapped[UUID | None] = mapped_column(nullable=True)
    related_lead_id: Mapped[UUID | None] = mapped_column(nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
