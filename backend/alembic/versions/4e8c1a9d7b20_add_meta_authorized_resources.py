"""persist tenant-scoped Meta authorized resources

Revision ID: 4e8c1a9d7b20
Revises: 1c9d4e7f2a6b
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "4e8c1a9d7b20"
down_revision: str | Sequence[str] | None = "1c9d4e7f2a6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_integration_connections_valid_selected_resources"),
        "integration_connections",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_integration_connections_valid_selected_resources"),
        "integration_connections",
        "jsonb_typeof(selected_resources) = 'array' AND "
        "jsonb_array_length(selected_resources) <= 100",
    )
    op.add_column(
        "integration_connections",
        sa.Column(
            "authorized_resources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_integration_connections_valid_authorized_resources"),
        "integration_connections",
        "jsonb_typeof(authorized_resources) = 'array' AND "
        "jsonb_array_length(authorized_resources) <= 100",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_integration_connections_valid_authorized_resources"),
        "integration_connections",
        type_="check",
    )
    op.drop_column("integration_connections", "authorized_resources")
    # Keep downgrade executable even when a connection discovered more than
    # the legacy limit of 20 resources.
    op.execute(
        """
        UPDATE integration_connections AS connection
        SET selected_resources = bounded.value
        FROM (
            SELECT id, COALESCE(jsonb_agg(resource.value ORDER BY resource.ordinality), '[]'::jsonb) AS value
            FROM integration_connections,
                 jsonb_array_elements(selected_resources) WITH ORDINALITY AS resource(value, ordinality)
            WHERE resource.ordinality <= 20
            GROUP BY id
        ) AS bounded
        WHERE connection.id = bounded.id
        """
    )
    op.drop_constraint(
        op.f("ck_integration_connections_valid_selected_resources"),
        "integration_connections",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_integration_connections_valid_selected_resources"),
        "integration_connections",
        "jsonb_typeof(selected_resources) = 'array' AND "
        "jsonb_array_length(selected_resources) <= 20",
    )
