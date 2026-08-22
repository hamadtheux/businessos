"""add authentication sessions

Revision ID: 2500084c0abd
Revises: 4670d54b3fab
Create Date: 2026-08-19 23:27:04.571775

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2500084c0abd"
down_revision: Union[str, Sequence[str], None] = "4670d54b3fab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "auth_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("replaced_by_session_id", sa.Uuid(), nullable=True),
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
            "char_length(token_hash) = 64 "
            "AND token_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_auth_sessions_valid_token_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_session_id"],
            ["auth_sessions.id"],
            name=op.f(
                "fk_auth_sessions_replaced_by_session_id_auth_sessions"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )
    op.create_index(
        op.f("ix_auth_sessions_expires_at"),
        "auth_sessions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auth_sessions_family_id"),
        "auth_sessions",
        ["family_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auth_sessions_token_hash"),
        "auth_sessions",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_auth_sessions_user_id"),
        "auth_sessions",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_auth_sessions_user_id"),
        table_name="auth_sessions",
    )
    op.drop_index(
        op.f("ix_auth_sessions_token_hash"),
        table_name="auth_sessions",
    )
    op.drop_index(
        op.f("ix_auth_sessions_family_id"),
        table_name="auth_sessions",
    )
    op.drop_index(
        op.f("ix_auth_sessions_expires_at"),
        table_name="auth_sessions",
    )
    op.drop_table("auth_sessions")
