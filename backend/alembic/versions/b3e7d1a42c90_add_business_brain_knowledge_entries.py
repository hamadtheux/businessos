"""add business brain knowledge entries

Revision ID: b3e7d1a42c90
Revises: 9a4f1c2d8e60
Create Date: 2026-08-20 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3e7d1a42c90"
down_revision: str | Sequence[str] | None = "9a4f1c2d8e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_knowledge_entries",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=250), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "source_type",
            sa.String(length=16),
            server_default="manual",
            nullable=False,
        ),
        sa.Column("source_reference", sa.String(length=1024), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ('general', 'faq', 'policy', 'procedure', 'brand', "
            "'sales', 'support', 'operations', 'marketing')",
            name=op.f("ck_business_knowledge_entries_valid_category"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(content)) BETWEEN 1 AND 50000",
            name=op.f("ck_business_knowledge_entries_valid_content"),
        ),
        sa.CheckConstraint(
            "source_type IN ('manual', 'system')",
            name=op.f("ck_business_knowledge_entries_valid_source_type"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'draft', 'archived')",
            name=op.f("ck_business_knowledge_entries_valid_status"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 250",
            name=op.f("ck_business_knowledge_entries_valid_title"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_business_knowledge_entries_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_business_knowledge_entries"),
        ),
    )
    op.create_index(
        "ix_business_knowledge_entries_business_category",
        "business_knowledge_entries",
        ["business_id", "category"],
        unique=False,
    )
    op.create_index(
        "ix_business_knowledge_entries_business_status",
        "business_knowledge_entries",
        ["business_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_business_knowledge_entries_business_status",
        table_name="business_knowledge_entries",
    )
    op.drop_index(
        "ix_business_knowledge_entries_business_category",
        table_name="business_knowledge_entries",
    )
    op.drop_table("business_knowledge_entries")
