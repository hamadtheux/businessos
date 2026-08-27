"""scope refund and fulfillment provider identities

Revision ID: 0991074b3c9e
Revises: 7198cebbe94c
Create Date: 2026-08-26 07:23:10.567698

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0991074b3c9e'
down_revision: Union[str, Sequence[str], None] = '7198cebbe94c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('order_fulfillments', sa.Column('provider', sa.String(length=32), nullable=True))
    op.add_column('order_fulfillments', sa.Column('external_account_id', sa.String(length=255), nullable=True))
    op.execute("""
        UPDATE order_fulfillments AS value
        SET provider = COALESCE(
                (SELECT mapping.provider FROM external_order_mappings AS mapping
                 WHERE mapping.business_id = value.business_id
                   AND mapping.order_id = value.order_id LIMIT 1),
                'manual'
            ),
            external_account_id = COALESCE(
                (SELECT mapping.external_account_id FROM external_order_mappings AS mapping
                 WHERE mapping.business_id = value.business_id
                   AND mapping.order_id = value.order_id LIMIT 1),
                'order:' || value.order_id::text
            )
    """)
    op.alter_column('order_fulfillments', 'provider', nullable=False)
    op.alter_column('order_fulfillments', 'external_account_id', nullable=False)
    op.create_unique_constraint('uq_order_fulfillments_provider_identity', 'order_fulfillments', ['business_id', 'provider', 'external_account_id', 'external_object_id'])
    op.create_check_constraint(op.f('ck_order_fulfillments_valid_provider'), 'order_fulfillments', "provider ~ '^[a-z][a-z0-9_]{0,31}$'")
    op.add_column('order_refunds', sa.Column('provider', sa.String(length=32), nullable=True))
    op.add_column('order_refunds', sa.Column('external_account_id', sa.String(length=255), nullable=True))
    op.execute("""
        UPDATE order_refunds AS value
        SET provider = COALESCE(
                (SELECT mapping.provider FROM external_order_mappings AS mapping
                 WHERE mapping.business_id = value.business_id
                   AND mapping.order_id = value.order_id LIMIT 1),
                'manual'
            ),
            external_account_id = COALESCE(
                (SELECT mapping.external_account_id FROM external_order_mappings AS mapping
                 WHERE mapping.business_id = value.business_id
                   AND mapping.order_id = value.order_id LIMIT 1),
                'order:' || value.order_id::text
            )
    """)
    op.alter_column('order_refunds', 'provider', nullable=False)
    op.alter_column('order_refunds', 'external_account_id', nullable=False)
    op.create_unique_constraint('uq_order_refunds_provider_identity', 'order_refunds', ['business_id', 'provider', 'external_account_id', 'external_object_id'])
    op.create_check_constraint(op.f('ck_order_refunds_valid_provider'), 'order_refunds', "provider ~ '^[a-z][a-z0-9_]{0,31}$'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_order_refunds_valid_provider'), 'order_refunds', type_='check')
    op.drop_constraint('uq_order_refunds_provider_identity', 'order_refunds', type_='unique')
    op.drop_column('order_refunds', 'external_account_id')
    op.drop_column('order_refunds', 'provider')
    op.drop_constraint(op.f('ck_order_fulfillments_valid_provider'), 'order_fulfillments', type_='check')
    op.drop_constraint('uq_order_fulfillments_provider_identity', 'order_fulfillments', type_='unique')
    op.drop_column('order_fulfillments', 'external_account_id')
    op.drop_column('order_fulfillments', 'provider')
