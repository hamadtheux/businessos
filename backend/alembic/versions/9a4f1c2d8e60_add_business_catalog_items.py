"""add business catalog items

Revision ID: 9a4f1c2d8e60
Revises: 7d31b6f40a92
Create Date: 2026-08-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9a4f1c2d8e60"
down_revision: str | Sequence[str] | None = "7d31b6f40a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_items",
        sa.Column("item_type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sku", sa.String(length=100), nullable=True),
        sa.Column("price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column("business_id", sa.Uuid(), nullable=False),
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
            "item_type IN ('product', 'service')",
            name=op.f("ck_catalog_items_valid_item_type"),
        ),
        sa.CheckConstraint(
            "price IS NULL OR (price >= 0 AND price <= 999999999999.99)",
            name=op.f("ck_catalog_items_valid_price"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'draft', 'archived')",
            name=op.f("ck_catalog_items_valid_status"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_catalog_items_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_items")),
        sa.UniqueConstraint(
            "business_id",
            "sku",
            name="uq_catalog_items_business_sku",
        ),
    )
    op.create_index(
        "ix_catalog_items_business_item_type",
        "catalog_items",
        ["business_id", "item_type"],
        unique=False,
    )
    op.create_index(
        "ix_catalog_items_business_status",
        "catalog_items",
        ["business_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_catalog_items_business_status",
        table_name="catalog_items",
    )
    op.drop_index(
        "ix_catalog_items_business_item_type",
        table_name="catalog_items",
    )
    op.drop_table("catalog_items")
