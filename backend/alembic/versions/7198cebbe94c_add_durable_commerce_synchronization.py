"""add durable commerce synchronization

Revision ID: 7198cebbe94c
Revises: f2d3e4a5b6c7
Create Date: 2026-08-26 03:06:47.485557

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7198cebbe94c'
down_revision: Union[str, Sequence[str], None] = 'f2d3e4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f('ck_background_jobs_valid_job_type'), 'background_jobs', type_='check')
    op.create_check_constraint(
        op.f('ck_background_jobs_valid_job_type'), 'background_jobs',
        "job_type IN ('process_automation_event','resume_workflow_run','process_scheduled_workflow','process_integration_event','dispatch_action_execution','reconcile_uncertain_attempt','mark_social_schedule_ready','maintain_subscription','discover_competitors','generate_content_plan','analyze_campaign_opportunities','commerce_initial_sync','commerce_incremental_sync','commerce_webhook_reconcile')",
    )
    op.drop_constraint(op.f('ck_commerce_connections_valid_status'), 'commerce_connections', type_='check')
    op.drop_constraint(op.f('ck_commerce_connections_valid_health'), 'commerce_connections', type_='check')
    op.create_check_constraint(op.f('ck_commerce_connections_valid_status'), 'commerce_connections', "status IN ('configuration_required','connection_required','connected','syncing','attention_required','authentication_expired','rate_limited','failed','disabled')")
    op.create_check_constraint(op.f('ck_commerce_connections_valid_health'), 'commerce_connections', "health IN ('not_checked','healthy','degraded','reauth_required','rate_limited','failed','disabled')")
    op.drop_constraint(op.f('ck_commerce_sync_runs_valid_counts'), 'commerce_sync_runs', type_='check')
    op.create_table('external_customer_mappings',
    sa.Column('business_id', sa.Uuid(), nullable=False),
    sa.Column('connection_id', sa.Uuid(), nullable=False),
    sa.Column('customer_id', sa.Uuid(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('external_account_id', sa.String(length=255), nullable=False),
    sa.Column('external_object_id', sa.String(length=255), nullable=False),
    sa.Column('provider_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_synchronized_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_external_customer_mappings_business_id_businesses'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['connection_id', 'business_id'], ['commerce_connections.id', 'commerce_connections.business_id'], name='fk_external_customer_mappings_connection_business', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['customer_id', 'business_id'], ['customers.id', 'customers.business_id'], name='fk_external_customer_mappings_customer_business', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_external_customer_mappings')),
    sa.UniqueConstraint('business_id', 'provider', 'external_account_id', 'external_object_id', name='uq_external_customer_mappings_external_identity'),
    sa.UniqueConstraint('id', 'business_id', name='uq_external_customer_mappings_id_business')
    )
    op.create_index('ix_external_customer_mappings_business_customer', 'external_customer_mappings', ['business_id', 'customer_id', 'id'], unique=False)
    op.create_table('external_order_mappings',
    sa.Column('business_id', sa.Uuid(), nullable=False),
    sa.Column('connection_id', sa.Uuid(), nullable=False),
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('external_account_id', sa.String(length=255), nullable=False),
    sa.Column('external_object_id', sa.String(length=255), nullable=False),
    sa.Column('provider_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_synchronized_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_external_order_mappings_business_id_businesses'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['connection_id', 'business_id'], ['commerce_connections.id', 'commerce_connections.business_id'], name='fk_external_order_mappings_connection_business', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['order_id', 'business_id'], ['orders.id', 'orders.business_id'], name='fk_external_order_mappings_order_business', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_external_order_mappings')),
    sa.UniqueConstraint('business_id', 'provider', 'external_account_id', 'external_object_id', name='uq_external_order_mappings_external_identity'),
    sa.UniqueConstraint('id', 'business_id', name='uq_external_order_mappings_id_business')
    )
    op.create_index('ix_external_order_mappings_business_order', 'external_order_mappings', ['business_id', 'order_id', 'id'], unique=False)
    op.create_table('order_addresses',
    sa.Column('business_id', sa.Uuid(), nullable=False),
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('address_type', sa.String(length=16), nullable=False),
    sa.Column('first_name', sa.String(length=80), nullable=True),
    sa.Column('last_name', sa.String(length=80), nullable=True),
    sa.Column('company', sa.String(length=160), nullable=True),
    sa.Column('address1', sa.String(length=255), nullable=True),
    sa.Column('address2', sa.String(length=255), nullable=True),
    sa.Column('city', sa.String(length=120), nullable=True),
    sa.Column('region', sa.String(length=120), nullable=True),
    sa.Column('postal_code', sa.String(length=32), nullable=True),
    sa.Column('country_code', sa.String(length=2), nullable=True),
    sa.Column('phone', sa.String(length=32), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("address_type IN ('billing','shipping')", name=op.f('ck_order_addresses_valid_address_type')),
    sa.ForeignKeyConstraint(['order_id', 'business_id'], ['orders.id', 'orders.business_id'], name='fk_order_addresses_order_business', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_addresses')),
    sa.UniqueConstraint('business_id', 'order_id', 'address_type', name='uq_order_addresses_order_type')
    )
    op.create_table('order_fulfillments',
    sa.Column('business_id', sa.Uuid(), nullable=False),
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('external_object_id', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('tracking_company', sa.String(length=160), nullable=True),
    sa.Column('tracking_number', sa.String(length=255), nullable=True),
    sa.Column('tracking_url', sa.String(length=2048), nullable=True),
    sa.Column('external_order_line_ids', sa.ARRAY(sa.String(length=255)), server_default='{}', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending','open','in_progress','fulfilled','canceled','failed')", name=op.f('ck_order_fulfillments_valid_status')),
    sa.CheckConstraint('cardinality(external_order_line_ids) <= 500', name=op.f('ck_order_fulfillments_valid_line_count')),
    sa.ForeignKeyConstraint(['order_id', 'business_id'], ['orders.id', 'orders.business_id'], name='fk_order_fulfillments_order_business', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_fulfillments')),
    sa.UniqueConstraint('business_id', 'order_id', 'external_object_id', name='uq_order_fulfillments_external_identity'),
    sa.UniqueConstraint('id', 'business_id', name='uq_order_fulfillments_id_business')
    )
    op.create_table('order_refunds',
    sa.Column('business_id', sa.Uuid(), nullable=False),
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('external_object_id', sa.String(length=255), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reason', sa.String(length=1000), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('amount >= 0', name=op.f('ck_order_refunds_valid_amount')),
    sa.ForeignKeyConstraint(['order_id', 'business_id'], ['orders.id', 'orders.business_id'], name='fk_order_refunds_order_business', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_refunds')),
    sa.UniqueConstraint('business_id', 'order_id', 'external_object_id', name='uq_order_refunds_external_identity'),
    sa.UniqueConstraint('id', 'business_id', name='uq_order_refunds_id_business')
    )
    op.create_table('commerce_webhook_receipts',
    sa.Column('business_id', sa.Uuid(), nullable=False),
    sa.Column('connection_id', sa.Uuid(), nullable=False),
    sa.Column('sync_run_id', sa.Uuid(), nullable=True),
    sa.Column('external_event_id', sa.String(length=255), nullable=False),
    sa.Column('topic', sa.String(length=100), nullable=False),
    sa.Column('reconciliation_domain', sa.String(length=32), nullable=False),
    sa.Column('external_object_id', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='received', nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reconciled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failure_code', sa.String(length=64), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('received','queued','reconciled','failed','duplicate')", name=op.f('ck_commerce_webhook_receipts_valid_status')),
    sa.CheckConstraint("reconciliation_domain IN ('products','customers','orders','inventory')", name=op.f('ck_commerce_webhook_receipts_valid_reconciliation_domain')),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name=op.f('fk_commerce_webhook_receipts_business_id_businesses'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['connection_id', 'business_id'], ['commerce_connections.id', 'commerce_connections.business_id'], name='fk_commerce_webhook_receipts_connection_business', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['sync_run_id', 'business_id'], ['commerce_sync_runs.id', 'commerce_sync_runs.business_id'], name='fk_commerce_webhook_receipts_run_business'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_commerce_webhook_receipts')),
    sa.UniqueConstraint('connection_id', 'external_event_id', name='uq_commerce_webhook_receipts_connection_event'),
    sa.UniqueConstraint('id', 'business_id', name='uq_commerce_webhook_receipts_id_business')
    )
    op.create_index('ix_commerce_webhook_receipts_business_received', 'commerce_webhook_receipts', ['business_id', 'received_at', 'id'], unique=False)
    op.create_table('order_refund_lines',
    sa.Column('business_id', sa.Uuid(), nullable=False),
    sa.Column('refund_id', sa.Uuid(), nullable=False),
    sa.Column('external_order_line_id', sa.String(length=255), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('quantity >= 1 AND amount >= 0', name=op.f('ck_order_refund_lines_valid_values')),
    sa.ForeignKeyConstraint(['refund_id', 'business_id'], ['order_refunds.id', 'order_refunds.business_id'], name='fk_order_refund_lines_refund_business', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_refund_lines'))
    )
    op.create_index('ix_order_refund_lines_business_refund', 'order_refund_lines', ['business_id', 'refund_id', 'id'], unique=False)
    op.add_column('background_jobs', sa.Column('commerce_sync_run_id', sa.Uuid(), nullable=True))
    op.add_column('background_jobs', sa.Column('commerce_webhook_receipt_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_jobs_commerce_sync_run_business', 'background_jobs', 'commerce_sync_runs', ['commerce_sync_run_id', 'business_id'], ['id', 'business_id'], ondelete='CASCADE')
    op.create_foreign_key('fk_jobs_commerce_webhook_receipt_business', 'background_jobs', 'commerce_webhook_receipts', ['commerce_webhook_receipt_id', 'business_id'], ['id', 'business_id'], ondelete='CASCADE')
    op.add_column('catalog_media', sa.Column('provider', sa.String(length=32), nullable=True))
    op.add_column('catalog_media', sa.Column('external_account_id', sa.String(length=255), nullable=True))
    op.add_column('catalog_media', sa.Column('external_object_id', sa.String(length=255), nullable=True))
    op.add_column('catalog_media', sa.Column('active', sa.Boolean(), server_default='true', nullable=False))
    op.create_unique_constraint('uq_catalog_media_external_identity', 'catalog_media', ['business_id', 'provider', 'external_account_id', 'external_object_id'])
    op.add_column('catalog_variants', sa.Column('published', sa.Boolean(), server_default='true', nullable=False))
    op.add_column('catalog_variants', sa.Column('barcode', sa.String(length=64), nullable=True))
    op.add_column('commerce_connections', sa.Column('credential_reference', sa.String(length=255), nullable=True))
    op.add_column('commerce_connections', sa.Column('consecutive_failures', sa.Integer(), server_default='0', nullable=False))
    op.add_column('commerce_connections', sa.Column('store_name', sa.String(length=160), nullable=True))
    op.create_check_constraint(op.f('ck_commerce_connections_valid_credential_reference'), 'commerce_connections', "credential_reference IS NULL OR char_length(btrim(credential_reference)) BETWEEN 1 AND 255")
    op.create_check_constraint(op.f('ck_commerce_connections_valid_consecutive_failures'), 'commerce_connections', 'consecutive_failures >= 0')
    op.add_column('commerce_sync_runs', sa.Column('customers_created', sa.Integer(), server_default='0', nullable=False))
    op.add_column('commerce_sync_runs', sa.Column('customers_updated', sa.Integer(), server_default='0', nullable=False))
    op.add_column('commerce_sync_runs', sa.Column('orders_created', sa.Integer(), server_default='0', nullable=False))
    op.add_column('commerce_sync_runs', sa.Column('orders_updated', sa.Integer(), server_default='0', nullable=False))
    op.add_column('commerce_sync_runs', sa.Column('refunds_processed', sa.Integer(), server_default='0', nullable=False))
    op.add_column('commerce_sync_runs', sa.Column('fulfillments_processed', sa.Integer(), server_default='0', nullable=False))
    op.add_column('commerce_sync_runs', sa.Column('pages_processed', sa.Integer(), server_default='0', nullable=False))
    op.add_column('commerce_sync_runs', sa.Column('warnings', sa.Integer(), server_default='0', nullable=False))
    op.create_check_constraint(op.f('ck_commerce_sync_runs_valid_counts'), 'commerce_sync_runs', 'products_created >= 0 AND products_updated >= 0 AND products_archived >= 0 AND variants_processed >= 0 AND customers_created >= 0 AND customers_updated >= 0 AND orders_created >= 0 AND orders_updated >= 0 AND refunds_processed >= 0 AND fulfillments_processed >= 0 AND pages_processed >= 0 AND warnings >= 0 AND failures >= 0')
    op.add_column('order_line_items', sa.Column('external_object_id', sa.String(length=255), nullable=True))
    op.add_column('order_line_items', sa.Column('external_variant_id', sa.String(length=255), nullable=True))
    op.add_column('order_line_items', sa.Column('sku', sa.String(length=100), nullable=True))
    op.add_column('order_line_items', sa.Column('discount_amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False))
    op.add_column('order_line_items', sa.Column('tax_amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False))
    op.create_unique_constraint('uq_order_line_items_external_identity', 'order_line_items', ['business_id', 'order_id', 'external_object_id'])
    op.create_check_constraint(op.f('ck_order_line_items_valid_commerce_amounts'), 'order_line_items', 'discount_amount >= 0 AND tax_amount >= 0')
    op.add_column('orders', sa.Column('discount_amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False))
    op.add_column('orders', sa.Column('tax_amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False))
    op.add_column('orders', sa.Column('shipping_amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False))
    op.add_column('orders', sa.Column('refunded_amount', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False))
    op.add_column('orders', sa.Column('payment_status', sa.String(length=32), server_default='unknown', nullable=False))
    op.add_column('orders', sa.Column('fulfillment_status', sa.String(length=24), server_default='unknown', nullable=False))
    op.add_column('orders', sa.Column('provider_created_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('provider_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.alter_column('orders', 'customer_id',
               existing_type=sa.UUID(),
               nullable=True)
    op.drop_constraint(op.f('ck_orders_valid_total'), 'orders', type_='check')
    op.create_check_constraint(op.f('ck_orders_valid_total'), 'orders', 'total >= 0 AND total <= 999999999999.99')
    op.create_check_constraint(op.f('ck_orders_valid_commerce_amounts'), 'orders', 'discount_amount >= 0 AND tax_amount >= 0 AND shipping_amount >= 0 AND refunded_amount >= 0')
    op.create_check_constraint(op.f('ck_orders_valid_fulfillment_status'), 'orders', "fulfillment_status IN ('unknown','unfulfilled','partial','fulfilled','canceled')")
    op.create_check_constraint(op.f('ck_orders_valid_payment_status'), 'orders', "payment_status IN ('unknown','pending','authorized','paid','partially_refunded','refunded','voided','failed')")
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_commerce_sync_runs_valid_counts'), 'commerce_sync_runs', type_='check')
    op.drop_constraint(op.f('ck_orders_valid_payment_status'), 'orders', type_='check')
    op.drop_constraint(op.f('ck_orders_valid_fulfillment_status'), 'orders', type_='check')
    op.drop_constraint(op.f('ck_orders_valid_commerce_amounts'), 'orders', type_='check')
    op.drop_constraint(op.f('ck_orders_valid_total'), 'orders', type_='check')
    op.create_check_constraint(op.f('ck_orders_valid_total'), 'orders', 'total = (subtotal + adjustment_amount)')
    op.alter_column('orders', 'customer_id',
               existing_type=sa.UUID(),
               nullable=False)
    op.drop_column('orders', 'provider_updated_at')
    op.drop_column('orders', 'provider_created_at')
    op.drop_column('orders', 'fulfillment_status')
    op.drop_column('orders', 'payment_status')
    op.drop_column('orders', 'refunded_amount')
    op.drop_column('orders', 'shipping_amount')
    op.drop_column('orders', 'tax_amount')
    op.drop_column('orders', 'discount_amount')
    op.drop_constraint(op.f('ck_order_line_items_valid_commerce_amounts'), 'order_line_items', type_='check')
    op.drop_constraint('uq_order_line_items_external_identity', 'order_line_items', type_='unique')
    op.drop_column('order_line_items', 'tax_amount')
    op.drop_column('order_line_items', 'discount_amount')
    op.drop_column('order_line_items', 'sku')
    op.drop_column('order_line_items', 'external_variant_id')
    op.drop_column('order_line_items', 'external_object_id')
    op.drop_column('commerce_sync_runs', 'pages_processed')
    op.drop_column('commerce_sync_runs', 'fulfillments_processed')
    op.drop_column('commerce_sync_runs', 'refunds_processed')
    op.drop_column('commerce_sync_runs', 'orders_updated')
    op.drop_column('commerce_sync_runs', 'orders_created')
    op.drop_column('commerce_sync_runs', 'customers_updated')
    op.drop_column('commerce_sync_runs', 'customers_created')
    op.drop_column('commerce_sync_runs', 'warnings')
    op.create_check_constraint(op.f('ck_commerce_sync_runs_valid_counts'), 'commerce_sync_runs', 'products_created >= 0 AND products_updated >= 0 AND products_archived >= 0 AND variants_processed >= 0 AND failures >= 0')
    op.drop_constraint(op.f('ck_commerce_connections_valid_consecutive_failures'), 'commerce_connections', type_='check')
    op.drop_constraint(op.f('ck_commerce_connections_valid_credential_reference'), 'commerce_connections', type_='check')
    op.drop_column('commerce_connections', 'store_name')
    op.drop_column('commerce_connections', 'consecutive_failures')
    op.drop_column('commerce_connections', 'credential_reference')
    op.drop_column('catalog_variants', 'barcode')
    op.drop_column('catalog_variants', 'published')
    op.drop_constraint('uq_catalog_media_external_identity', 'catalog_media', type_='unique')
    op.drop_column('catalog_media', 'active')
    op.drop_column('catalog_media', 'external_object_id')
    op.drop_column('catalog_media', 'external_account_id')
    op.drop_column('catalog_media', 'provider')
    op.drop_constraint('fk_jobs_commerce_webhook_receipt_business', 'background_jobs', type_='foreignkey')
    op.drop_constraint('fk_jobs_commerce_sync_run_business', 'background_jobs', type_='foreignkey')
    op.drop_column('background_jobs', 'commerce_webhook_receipt_id')
    op.drop_column('background_jobs', 'commerce_sync_run_id')
    op.drop_index('ix_order_refund_lines_business_refund', table_name='order_refund_lines')
    op.drop_table('order_refund_lines')
    op.drop_index('ix_commerce_webhook_receipts_business_received', table_name='commerce_webhook_receipts')
    op.drop_table('commerce_webhook_receipts')
    op.drop_table('order_refunds')
    op.drop_table('order_fulfillments')
    op.drop_table('order_addresses')
    op.drop_index('ix_external_order_mappings_business_order', table_name='external_order_mappings')
    op.drop_table('external_order_mappings')
    op.drop_index('ix_external_customer_mappings_business_customer', table_name='external_customer_mappings')
    op.drop_table('external_customer_mappings')
    op.drop_constraint(op.f('ck_commerce_connections_valid_health'), 'commerce_connections', type_='check')
    op.drop_constraint(op.f('ck_commerce_connections_valid_status'), 'commerce_connections', type_='check')
    op.create_check_constraint(op.f('ck_commerce_connections_valid_status'), 'commerce_connections', "status IN ('configuration_required','connection_required','connected','syncing','attention_required','disabled')")
    op.create_check_constraint(op.f('ck_commerce_connections_valid_health'), 'commerce_connections', "health IN ('not_checked','healthy','degraded','reauth_required','disabled')")
    op.drop_constraint(op.f('ck_background_jobs_valid_job_type'), 'background_jobs', type_='check')
    op.create_check_constraint(op.f('ck_background_jobs_valid_job_type'), 'background_jobs', "job_type IN ('process_automation_event','resume_workflow_run','process_scheduled_workflow','process_integration_event','dispatch_action_execution','reconcile_uncertain_attempt','mark_social_schedule_ready','maintain_subscription','discover_competitors','generate_content_plan','analyze_campaign_opportunities')")
