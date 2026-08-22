from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.business import Business


class BusinessKnowledgeEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One authoritative, curated knowledge entry owned by a business."""

    __tablename__ = "business_knowledge_entries"
    __table_args__ = (
        CheckConstraint(
            "category IN ('general', 'faq', 'policy', 'procedure', 'brand', "
            "'sales', 'support', 'operations', 'marketing')",
            name="valid_category",
        ),
        CheckConstraint(
            "status IN ('active', 'draft', 'archived')",
            name="valid_status",
        ),
        CheckConstraint(
            "source_type IN ('manual', 'system')",
            name="valid_source_type",
        ),
        CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 250",
            name="valid_title",
        ),
        CheckConstraint(
            "char_length(btrim(content)) BETWEEN 1 AND 50000",
            name="valid_content",
        ),
        Index(
            "ix_business_knowledge_entries_business_status",
            "business_id",
            "status",
        ),
        Index(
            "ix_business_knowledge_entries_business_category",
            "business_id",
            "category",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
        server_default="active",
    )
    source_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="manual",
        server_default="manual",
    )
    source_reference: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )

    business: Mapped["Business"] = relationship(
        back_populates="knowledge_entries",
        lazy="select",
    )
