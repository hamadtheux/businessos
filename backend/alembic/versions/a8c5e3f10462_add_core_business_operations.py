"""add core business operations

Revision ID: a8c5e3f10462
Revises: f7a4c9d2e510
Create Date: 2026-08-23 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a8c5e3f10462"
down_revision: str | Sequence[str] | None = "f7a4c9d2e510"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column, sa.Column]:
    return (
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def _business_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f(f"fk_{table}_business_id_businesses"))


def upgrade() -> None:
    op.add_column("customers", sa.Column("first_name", sa.String(80), nullable=True))
    op.add_column("customers", sa.Column("last_name", sa.String(80), nullable=True))
    op.add_column("customers", sa.Column("email", sa.String(320), nullable=True))
    op.add_column("customers", sa.Column("phone", sa.String(32), nullable=True))
    op.add_column("customers", sa.Column("status", sa.String(24), server_default="active", nullable=False))
    op.add_column("customers", sa.Column("source", sa.String(32), server_default="manual", nullable=False))
    op.add_column("customers", sa.Column("tags", postgresql.ARRAY(sa.String(40)), server_default=sa.text("'{}'::varchar[]"), nullable=False))
    op.add_column("customers", sa.Column("company", sa.String(160), nullable=True))
    op.add_column("customers", sa.Column("notes", sa.Text(), nullable=True))
    for expression, name in (
        ("first_name IS NULL OR char_length(btrim(first_name)) BETWEEN 1 AND 80", "valid_first_name"),
        ("last_name IS NULL OR char_length(btrim(last_name)) BETWEEN 1 AND 80", "valid_last_name"),
        ("email IS NULL OR char_length(btrim(email)) BETWEEN 3 AND 320", "valid_email"),
        ("phone IS NULL OR char_length(btrim(phone)) BETWEEN 3 AND 32", "valid_phone"),
        ("status IN ('active', 'inactive', 'archived')", "valid_status"),
        ("source ~ '^[a-z][a-z0-9_]{0,31}$'", "valid_source"),
        ("company IS NULL OR char_length(btrim(company)) BETWEEN 1 AND 160", "valid_company"),
        ("notes IS NULL OR char_length(notes) <= 4000", "valid_notes"),
        ("cardinality(tags) <= 20", "valid_tag_count"),
    ):
        op.create_check_constraint(op.f(f"ck_customers_{name}"), "customers", expression)
    op.create_index("ix_customers_business_status_updated", "customers", ["business_id", "status", "updated_at", "id"])
    op.create_index("ix_customers_business_email", "customers", ["business_id", "email"])
    op.create_unique_constraint("uq_catalog_items_id_business", "catalog_items", ["id", "business_id"])

    op.create_table(
        "crm_leads",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True), sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("company", sa.String(160), nullable=True), sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True), sa.Column("stage", sa.String(24), server_default="new", nullable=False),
        sa.Column("source", sa.String(32), server_default="manual", nullable=False), sa.Column("priority", sa.String(16), server_default="medium", nullable=False),
        sa.Column("qualification_state", sa.String(24), server_default="unqualified", nullable=False),
        sa.Column("estimated_value", sa.Numeric(14, 2), nullable=True), sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("expected_close_date", sa.Date(), nullable=True), sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True), *_timestamps(),
        sa.CheckConstraint("char_length(btrim(display_name)) BETWEEN 1 AND 160", name=op.f("ck_crm_leads_valid_display_name")),
        sa.CheckConstraint("stage IN ('new','qualified','contacted','viewing','proposal','won','lost')", name=op.f("ck_crm_leads_valid_stage")),
        sa.CheckConstraint("priority IN ('low','medium','high','urgent')", name=op.f("ck_crm_leads_valid_priority")),
        sa.CheckConstraint("qualification_state IN ('unqualified','qualifying','qualified','disqualified')", name=op.f("ck_crm_leads_valid_qualification_state")),
        sa.CheckConstraint("source ~ '^[a-z][a-z0-9_]{0,31}$'", name=op.f("ck_crm_leads_valid_source")),
        sa.CheckConstraint("estimated_value IS NULL OR (estimated_value >= 0 AND estimated_value <= 999999999999.99)", name=op.f("ck_crm_leads_valid_estimated_value")),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_crm_leads_valid_currency")),
        sa.CheckConstraint("notes IS NULL OR char_length(notes) <= 4000", name=op.f("ck_crm_leads_valid_notes")),
        sa.CheckConstraint("email IS NULL OR char_length(btrim(email)) BETWEEN 3 AND 320", name=op.f("ck_crm_leads_valid_email")),
        sa.CheckConstraint("phone IS NULL OR char_length(btrim(phone)) BETWEEN 3 AND 32", name=op.f("ck_crm_leads_valid_phone")),
        _business_fk("crm_leads"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL", name=op.f("fk_crm_leads_owner_user_id_users")),
        sa.ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_crm_leads_customer_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crm_leads")), sa.UniqueConstraint("id", "business_id", name="uq_crm_leads_id_business"),
    )
    op.create_index("ix_crm_leads_business_stage_updated", "crm_leads", ["business_id", "stage", "updated_at", "id"])
    op.create_index("ix_crm_leads_business_follow_up", "crm_leads", ["business_id", "next_follow_up_at", "id"])
    op.create_index("ix_crm_leads_business_owner", "crm_leads", ["business_id", "owner_user_id", "id"])

    op.create_table(
        "orders", sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("order_number", sa.String(40), nullable=False), sa.Column("status", sa.String(24), server_default="draft", nullable=False),
        sa.Column("source", sa.String(32), server_default="manual", nullable=False), sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False), sa.Column("adjustment_amount", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("total", sa.Numeric(14, 2), nullable=False), sa.Column("notes", sa.Text(), nullable=True), *_timestamps(),
        sa.CheckConstraint("char_length(btrim(order_number)) BETWEEN 1 AND 40", name=op.f("ck_orders_valid_order_number")),
        sa.CheckConstraint("status IN ('draft','confirmed','processing','completed','canceled')", name=op.f("ck_orders_valid_status")),
        sa.CheckConstraint("source ~ '^[a-z][a-z0-9_]{0,31}$'", name=op.f("ck_orders_valid_source")),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_orders_valid_currency")),
        sa.CheckConstraint("subtotal >= 0 AND subtotal <= 999999999999.99", name=op.f("ck_orders_valid_subtotal")),
        sa.CheckConstraint("adjustment_amount >= 0 AND adjustment_amount <= 999999999999.99", name=op.f("ck_orders_valid_adjustment")),
        sa.CheckConstraint("total = subtotal + adjustment_amount", name=op.f("ck_orders_valid_total")),
        sa.CheckConstraint("notes IS NULL OR char_length(notes) <= 4000", name=op.f("ck_orders_valid_notes")),
        _business_fk("orders"), sa.ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_orders_customer_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")), sa.UniqueConstraint("id", "business_id", name="uq_orders_id_business"),
        sa.UniqueConstraint("business_id", "order_number", name="uq_orders_business_number"),
    )
    op.create_index("ix_orders_business_status_created", "orders", ["business_id", "status", "created_at", "id"])
    op.create_index("ix_orders_business_customer_created", "orders", ["business_id", "customer_id", "created_at", "id"])

    op.create_table(
        "order_line_items", sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_item_id", sa.Uuid(), nullable=True), sa.Column("description", sa.String(300), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False), sa.Column("unit_price", sa.Numeric(14, 2), nullable=False), *_timestamps(),
        sa.CheckConstraint("char_length(btrim(description)) BETWEEN 1 AND 300", name=op.f("ck_order_line_items_valid_description")),
        sa.CheckConstraint("quantity BETWEEN 1 AND 100000", name=op.f("ck_order_line_items_valid_quantity")),
        sa.CheckConstraint("unit_price >= 0 AND unit_price <= 999999999999.99", name=op.f("ck_order_line_items_valid_unit_price")),
        sa.ForeignKeyConstraint(["order_id", "business_id"], ["orders.id", "orders.business_id"], name="fk_order_line_items_order_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["catalog_item_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_order_line_items_catalog_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_line_items")),
    )
    op.create_index("ix_order_line_items_business_order", "order_line_items", ["business_id", "order_id", "id"])

    op.create_table(
        "conversations", sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("channel", sa.String(24), nullable=False), sa.Column("external_reference", sa.String(255), nullable=True),
        sa.Column("status", sa.String(24), server_default="open", nullable=False), sa.Column("assigned_user_id", sa.Uuid(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), *_timestamps(),
        sa.CheckConstraint("channel IN ('website','whatsapp','email','facebook','instagram','manual','other')", name=op.f("ck_conversations_valid_channel")),
        sa.CheckConstraint("status IN ('open','escalated','resolved')", name=op.f("ck_conversations_valid_status")),
        sa.CheckConstraint("external_reference IS NULL OR char_length(btrim(external_reference)) BETWEEN 1 AND 255", name=op.f("ck_conversations_valid_external_reference")),
        _business_fk("conversations"), sa.ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_conversations_customer_business"),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["users.id"], ondelete="SET NULL", name=op.f("fk_conversations_assigned_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")), sa.UniqueConstraint("id", "business_id", name="uq_conversations_id_business"),
        sa.UniqueConstraint("business_id", "channel", "external_reference", name="uq_conversations_business_channel_external"),
    )
    op.create_index("ix_conversations_business_status_activity", "conversations", ["business_id", "status", "last_activity_at", "id"])
    op.create_index("ix_conversations_business_customer", "conversations", ["business_id", "customer_id", "id"])

    op.create_table(
        "conversation_messages", sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False), sa.Column("sender_type", sa.String(16), nullable=False),
        sa.Column("sender_user_id", sa.Uuid(), nullable=True), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("external_reference", sa.String(255), nullable=True), sa.Column("delivery_status", sa.String(16), server_default="recorded", nullable=False), *_timestamps(),
        sa.CheckConstraint("direction IN ('inbound','outbound','internal')", name=op.f("ck_conversation_messages_valid_direction")),
        sa.CheckConstraint("sender_type IN ('customer','user','ai','system')", name=op.f("ck_conversation_messages_valid_sender_type")),
        sa.CheckConstraint("delivery_status IN ('received','recorded','failed')", name=op.f("ck_conversation_messages_valid_delivery_status")),
        sa.CheckConstraint("char_length(content) BETWEEN 1 AND 10000", name=op.f("ck_conversation_messages_valid_content")),
        sa.CheckConstraint("external_reference IS NULL OR char_length(btrim(external_reference)) BETWEEN 1 AND 255", name=op.f("ck_conversation_messages_valid_external_reference")),
        _business_fk("conversation_messages"),
        sa.ForeignKeyConstraint(["conversation_id", "business_id"], ["conversations.id", "conversations.business_id"], name="fk_conversation_messages_conversation_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"], ondelete="SET NULL", name=op.f("fk_conversation_messages_sender_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_messages")),
    )
    op.create_index("ix_conversation_messages_business_conversation_sent", "conversation_messages", ["business_id", "conversation_id", "sent_at", "id"])

    op.create_table(
        "notifications", sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("recipient_user_id", sa.Uuid(), nullable=True),
        sa.Column("category", sa.String(48), nullable=False), sa.Column("title", sa.String(180), nullable=False), sa.Column("message", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(16), server_default="medium", nullable=False), sa.Column("read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("related_entity_type", sa.String(48), nullable=True), sa.Column("related_entity_id", sa.Uuid(), nullable=True), *_timestamps(),
        sa.CheckConstraint("category ~ '^[a-z][a-z0-9_]{0,47}$'", name=op.f("ck_notifications_valid_category")),
        sa.CheckConstraint("priority IN ('low','medium','high')", name=op.f("ck_notifications_valid_priority")),
        sa.CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 180", name=op.f("ck_notifications_valid_title")),
        sa.CheckConstraint("char_length(message) BETWEEN 1 AND 1000", name=op.f("ck_notifications_valid_message")),
        sa.CheckConstraint("related_entity_type IS NULL OR related_entity_type ~ '^[a-z][a-z0-9_]{0,47}$'", name=op.f("ck_notifications_valid_related_entity_type")),
        _business_fk("notifications"), sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE", name=op.f("fk_notifications_recipient_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index("ix_notifications_business_user_read_created", "notifications", ["business_id", "recipient_user_id", "read", "created_at", "id"])

    op.create_table(
        "opportunities", sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("title", sa.String(180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False), sa.Column("category", sa.String(48), nullable=False), sa.Column("source", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), server_default="medium", nullable=False), sa.Column("estimated_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True), sa.Column("status", sa.String(24), server_default="open", nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True), sa.Column("lead_id", sa.Uuid(), nullable=True), *_timestamps(),
        sa.CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 180", name=op.f("ck_opportunities_valid_title")),
        sa.CheckConstraint("char_length(description) BETWEEN 1 AND 3000", name=op.f("ck_opportunities_valid_description")),
        sa.CheckConstraint("category ~ '^[a-z][a-z0-9_]{0,47}$'", name=op.f("ck_opportunities_valid_category")),
        sa.CheckConstraint("source ~ '^[a-z][a-z0-9_]{0,31}$'", name=op.f("ck_opportunities_valid_source")),
        sa.CheckConstraint("priority IN ('low','medium','high','urgent')", name=op.f("ck_opportunities_valid_priority")),
        sa.CheckConstraint("status IN ('open','in_progress','won','lost','dismissed')", name=op.f("ck_opportunities_valid_status")),
        sa.CheckConstraint("estimated_value IS NULL OR (estimated_value >= 0 AND estimated_value <= 999999999999.99)", name=op.f("ck_opportunities_valid_estimated_value")),
        sa.CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name=op.f("ck_opportunities_valid_currency")),
        _business_fk("opportunities"), sa.ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_opportunities_customer_business"),
        sa.ForeignKeyConstraint(["lead_id", "business_id"], ["crm_leads.id", "crm_leads.business_id"], name="fk_opportunities_lead_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunities")),
    )
    op.create_index("ix_opportunities_business_status_updated", "opportunities", ["business_id", "status", "updated_at", "id"])
    op.create_index("ix_opportunities_business_priority", "opportunities", ["business_id", "priority", "id"])

    op.create_table(
        "business_audit_log", sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(16), nullable=False), sa.Column("event_type", sa.String(80), nullable=False), sa.Column("entity_type", sa.String(48), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True), sa.Column("summary", sa.Text(), nullable=False), sa.Column("before_value", sa.String(500), nullable=True),
        sa.Column("after_value", sa.String(500), nullable=True), sa.Column("status", sa.String(16), server_default="completed", nullable=False), *_timestamps(),
        sa.CheckConstraint("event_type ~ '^[a-z][a-z0-9_.]{0,79}$'", name=op.f("ck_business_audit_log_valid_event_type")),
        sa.CheckConstraint("entity_type ~ '^[a-z][a-z0-9_]{0,47}$'", name=op.f("ck_business_audit_log_valid_entity_type")),
        sa.CheckConstraint("actor_type IN ('user','ai','system')", name=op.f("ck_business_audit_log_valid_actor_type")),
        sa.CheckConstraint("status IN ('completed','failed','pending')", name=op.f("ck_business_audit_log_valid_status")),
        sa.CheckConstraint("char_length(summary) BETWEEN 1 AND 1000", name=op.f("ck_business_audit_log_valid_summary")),
        sa.CheckConstraint("before_value IS NULL OR char_length(before_value) <= 500", name=op.f("ck_business_audit_log_valid_before_value")),
        sa.CheckConstraint("after_value IS NULL OR char_length(after_value) <= 500", name=op.f("ck_business_audit_log_valid_after_value")),
        _business_fk("business_audit_log"), sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL", name=op.f("fk_business_audit_log_actor_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_audit_log")),
    )
    op.create_index("ix_business_audit_log_business_created", "business_audit_log", ["business_id", "created_at", "id"])
    op.create_index("ix_business_audit_log_business_event", "business_audit_log", ["business_id", "event_type", "created_at", "id"])

    op.create_table(
        "business_reports", sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("report_type", sa.String(32), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False), sa.Column("period_end", sa.Date(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("summary", sa.Text(), nullable=False), sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False), *_timestamps(),
        sa.CheckConstraint("report_type IN ('daily_operations','sales','customer','scheduling')", name=op.f("ck_business_reports_valid_report_type")),
        sa.CheckConstraint("status IN ('ready','failed')", name=op.f("ck_business_reports_valid_status")),
        sa.CheckConstraint("period_end >= period_start", name=op.f("ck_business_reports_valid_period")),
        sa.CheckConstraint("char_length(summary) BETWEEN 1 AND 2000", name=op.f("ck_business_reports_valid_summary")),
        sa.CheckConstraint("jsonb_typeof(metrics) = 'object'", name=op.f("ck_business_reports_valid_metrics")),
        _business_fk("business_reports"), sa.PrimaryKeyConstraint("id", name=op.f("pk_business_reports")),
    )
    op.create_index("ix_business_reports_business_type_period", "business_reports", ["business_id", "report_type", "period_end", "id"])


def downgrade() -> None:
    for table in ("business_reports", "business_audit_log", "opportunities", "notifications", "conversation_messages", "conversations", "order_line_items", "orders", "crm_leads"):
        op.drop_table(table)
    op.drop_constraint("uq_catalog_items_id_business", "catalog_items", type_="unique")
    op.drop_index("ix_customers_business_email", table_name="customers")
    op.drop_index("ix_customers_business_status_updated", table_name="customers")
    for name in ("valid_tag_count", "valid_notes", "valid_company", "valid_source", "valid_status", "valid_phone", "valid_email", "valid_last_name", "valid_first_name"):
        op.drop_constraint(op.f(f"ck_customers_{name}"), "customers", type_="check")
    for column in ("notes", "company", "tags", "source", "status", "phone", "email", "last_name", "first_name"):
        op.drop_column("customers", column)
