"""add provider write acceptance evidence

Revision ID: c8e4f1a2b730
Revises: b6f2c8d4e901
Create Date: 2026-08-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8e4f1a2b730"
down_revision: str | None = "b6f2c8d4e901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_write_acceptances",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("integration_connection_id", sa.Uuid(), nullable=False),
        sa.Column("action_execution_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("connector_type", sa.String(length=48), nullable=False),
        sa.Column("action_type", sa.String(length=100), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
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
            "char_length(btrim(action_type)) BETWEEN 1 AND 100",
            name="valid_action_type",
        ),
        sa.CheckConstraint(
            "char_length(btrim(connector_type)) BETWEEN 1 AND 48",
            name="valid_connector_type",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_connection_id", "business_id"],
            [
                "integration_connections.id",
                "integration_connections.business_id",
            ],
            name=(
                "fk_provider_write_acceptance_connection"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["action_execution_attempt_id", "business_id"],
            [
                "action_execution_attempts.id",
                "action_execution_attempts.business_id",
            ],
            name=(
                "fk_provider_write_acceptance_attempt"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_provider_write_acceptances",
        ),
        sa.UniqueConstraint(
            "action_execution_attempt_id",
            name="uq_provider_write_acceptances_attempt",
        ),
        sa.UniqueConstraint(
            "id",
            "business_id",
            name="uq_provider_write_acceptances_id_business",
        ),
    )

    op.create_index(
        "ix_provider_write_acceptances_business_connection_accepted",
        "provider_write_acceptances",
        [
            "business_id",
            "integration_connection_id",
            "accepted_at",
            "id",
        ],
        unique=False,
    )

    op.create_index(
        "ix_provider_write_acceptances_business_connector_accepted",
        "provider_write_acceptances",
        [
            "business_id",
            "connector_type",
            "accepted_at",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_write_acceptances_business_connector_accepted",
        table_name="provider_write_acceptances",
    )
    op.drop_index(
        "ix_provider_write_acceptances_business_connection_accepted",
        table_name="provider_write_acceptances",
    )
    op.drop_table("provider_write_acceptances")
