"""add omnichannel support foundation

Revision ID: 8c4f2a91d7e6
Revises: 4e8c1a9d7b20
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "8c4f2a91d7e6"
down_revision: str | Sequence[str] | None = "4e8c1a9d7b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column, sa.Column]:
    return (
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "customer_channel_identities",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("integration_connection_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_resource_reference", sa.String(length=255), nullable=False),
        sa.Column("external_user_reference", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("provider IN ('facebook','instagram','whatsapp_business','website','gmail','microsoft_outlook','other')", name=op.f("ck_customer_channel_identities_valid_provider")),
        sa.CheckConstraint("char_length(btrim(external_resource_reference)) BETWEEN 1 AND 255", name=op.f("ck_customer_channel_identities_valid_external_resource_reference")),
        sa.CheckConstraint("char_length(btrim(external_user_reference)) BETWEEN 1 AND 255", name=op.f("ck_customer_channel_identities_valid_external_user_reference")),
        sa.CheckConstraint("display_name IS NULL OR char_length(btrim(display_name)) BETWEEN 1 AND 160", name=op.f("ck_customer_channel_identities_valid_display_name")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_customer_channel_identities_business_id_businesses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["integration_connection_id", "business_id"], ["integration_connections.id", "integration_connections.business_id"], name="fk_customer_channel_identities_integration_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_customer_channel_identities_customer_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_channel_identities")),
        sa.UniqueConstraint("id", "business_id", name="uq_customer_channel_identities_id_business"),
        sa.UniqueConstraint("business_id", "provider", "external_resource_reference", "external_user_reference", name="uq_customer_channel_identities_provider_identity"),
    )
    op.create_index("ix_customer_channel_identities_business_customer", "customer_channel_identities", ["business_id", "customer_id", "id"])
    op.create_index("ix_customer_channel_identities_business_provider", "customer_channel_identities", ["business_id", "provider", "last_seen_at", "id"])

    op.add_column("conversations", sa.Column("customer_channel_identity_id", sa.Uuid(), nullable=True))
    op.add_column("conversations", sa.Column("external_resource_reference", sa.String(length=255), nullable=True))
    op.add_column("conversations", sa.Column("handling_state", sa.String(length=24), server_default="ai_active", nullable=False))
    op.add_column("conversations", sa.Column("unread_count", sa.Integer(), server_default="0", nullable=False))
    op.create_foreign_key("fk_conversations_channel_identity_business", "conversations", "customer_channel_identities", ["customer_channel_identity_id", "business_id"], ["id", "business_id"])
    op.create_check_constraint(op.f("ck_conversations_valid_external_resource_reference"), "conversations", "external_resource_reference IS NULL OR char_length(btrim(external_resource_reference)) BETWEEN 1 AND 255")
    op.create_check_constraint(op.f("ck_conversations_valid_handling_state"), "conversations", "handling_state IN ('ai_active','ai_paused','human_takeover','escalated')")
    op.create_check_constraint(op.f("ck_conversations_valid_unread_count"), "conversations", "unread_count BETWEEN 0 AND 2147483647")
    op.drop_constraint("uq_conversations_business_channel_external", "conversations", type_="unique")
    op.create_index(
        "uq_conversations_provider_thread",
        "conversations",
        [
            "business_id",
            "integration_connection_id",
            "channel",
            "external_resource_reference",
            "external_reference",
        ],
        unique=True,
        postgresql_where=sa.text(
            "integration_connection_id IS NOT NULL "
            "AND external_reference IS NOT NULL "
            "AND external_resource_reference IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_conversations_provider_thread_without_resource",
        "conversations",
        [
            "business_id",
            "integration_connection_id",
            "channel",
            "external_reference",
        ],
        unique=True,
        postgresql_where=sa.text(
            "integration_connection_id IS NOT NULL "
            "AND external_reference IS NOT NULL "
            "AND external_resource_reference IS NULL"
        ),
    )
    op.create_index(
        "uq_conversations_local_thread",
        "conversations",
        ["business_id", "channel", "external_reference"],
        unique=True,
        postgresql_where=sa.text(
            "integration_connection_id IS NULL "
            "AND external_reference IS NOT NULL"
        ),
    )
    op.create_index("ix_conversations_business_handling_activity", "conversations", ["business_id", "handling_state", "last_activity_at", "id"])
    op.create_index("ix_conversations_business_external_identity", "conversations", ["business_id", "customer_channel_identity_id", "id"])

    # Durable human-authorized outbound messaging.
    #
    # The HTTP request persists a queued ConversationMessage and its one-shot
    # BackgroundJob in the same transaction. The worker may then cross the
    # external provider boundary without allowing an ambiguous send to be
    # blindly replayed.
    op.add_column(
        "conversation_messages",
        sa.Column("client_request_id", sa.Uuid(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_conversation_messages_business_client_request",
        "conversation_messages",
        ["business_id", "client_request_id"],
    )
    op.drop_constraint(
        op.f("ck_conversation_messages_valid_delivery_status"),
        "conversation_messages",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_conversation_messages_valid_delivery_status"),
        "conversation_messages",
        "delivery_status IN ("
        "'received','recorded','queued','dispatching','submitted',"
        "'sent','delivered','read','failed','uncertain'"
        ")",
    )

    op.add_column(
        "background_jobs",
        sa.Column("conversation_message_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_jobs_conversation_message_business",
        "background_jobs",
        "conversation_messages",
        ["conversation_message_id", "business_id"],
        ["id", "business_id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_consistent_conversation_message_reference"),
        "background_jobs",
        "(job_type = 'dispatch_conversation_message' "
        "AND conversation_message_id IS NOT NULL) OR "
        "(job_type <> 'dispatch_conversation_message' "
        "AND conversation_message_id IS NULL)",
    )
    op.drop_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        "job_type IN ("
        "'process_automation_event','resume_workflow_run',"
        "'process_scheduled_workflow','process_integration_event',"
        "'customer_agent_response','dispatch_action_execution',"
        "'dispatch_conversation_message','reconcile_uncertain_attempt',"
        "'mark_social_schedule_ready','maintain_subscription',"
        "'discover_competitors','generate_content_plan',"
        "'analyze_campaign_opportunities','analyze_business_opportunity',"
        "'commerce_initial_sync','commerce_incremental_sync',"
        "'commerce_webhook_reconcile','google_merchant_status_sync',"
        "'meta_catalog_status_sync','google_ads_performance_sync',"
        "'meta_ads_performance_sync'"
        ")",
    )

    op.create_table(
        "support_cases",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("case_number", sa.String(length=40), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("integration_connection_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_user_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_ai_role", sa.String(length=32), server_default="support", nullable=True),
        sa.Column("status", sa.String(length=32), server_default="new", nullable=False),
        sa.Column("priority", sa.String(length=16), server_default="medium", nullable=False),
        sa.Column("category", sa.String(length=24), server_default="general", nullable=False),
        sa.Column("issue_summary", sa.String(length=500), nullable=False),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.Column("resolution_summary", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("related_order_id", sa.Uuid(), nullable=True),
        sa.Column("related_product_id", sa.Uuid(), nullable=True),
        sa.Column("related_lead_id", sa.Uuid(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("status IN ('new','open','ai_handling','waiting_for_customer','waiting_for_business','escalated','resolved','closed')", name=op.f("ck_support_cases_valid_status")),
        sa.CheckConstraint("priority IN ('low','medium','high','urgent')", name=op.f("ck_support_cases_valid_priority")),
        sa.CheckConstraint("category IN ('general','order','delivery','return','refund','product','account','appointment','technical','complaint','payment')", name=op.f("ck_support_cases_valid_category")),
        sa.CheckConstraint("source ~ '^[a-z][a-z0-9_]{0,31}$'", name=op.f("ck_support_cases_valid_source")),
        sa.CheckConstraint("char_length(btrim(case_number)) BETWEEN 1 AND 40", name=op.f("ck_support_cases_valid_case_number")),
        sa.CheckConstraint("char_length(btrim(issue_summary)) BETWEEN 1 AND 500", name=op.f("ck_support_cases_valid_issue_summary")),
        sa.CheckConstraint("escalation_reason IS NULL OR char_length(escalation_reason) <= 1000", name=op.f("ck_support_cases_valid_escalation_reason")),
        sa.CheckConstraint("resolution_summary IS NULL OR char_length(resolution_summary) <= 2000", name=op.f("ck_support_cases_valid_resolution_summary")),
        sa.CheckConstraint("assigned_ai_role IS NULL OR assigned_ai_role = 'support'", name=op.f("ck_support_cases_valid_assigned_ai_role")),
        sa.CheckConstraint("resolved_at IS NULL OR resolved_at >= opened_at", name=op.f("ck_support_cases_valid_resolved_at")),
        sa.CheckConstraint("closed_at IS NULL OR closed_at >= opened_at", name=op.f("ck_support_cases_valid_closed_at")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_support_cases_business_id_businesses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id", "business_id"], ["conversations.id", "conversations.business_id"], name="fk_support_cases_conversation_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id", "business_id"], ["customers.id", "customers.business_id"], name="fk_support_cases_customer_business"),
        sa.ForeignKeyConstraint(["integration_connection_id", "business_id"], ["integration_connections.id", "integration_connections.business_id"], name="fk_support_cases_integration_business"),
        sa.ForeignKeyConstraint(["related_order_id", "business_id"], ["orders.id", "orders.business_id"], name="fk_support_cases_order_business"),
        sa.ForeignKeyConstraint(["related_product_id", "business_id"], ["catalog_items.id", "catalog_items.business_id"], name="fk_support_cases_product_business"),
        sa.ForeignKeyConstraint(["related_lead_id", "business_id"], ["crm_leads.id", "crm_leads.business_id"], name="fk_support_cases_lead_business"),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["users.id"], name=op.f("fk_support_cases_assigned_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_cases")),
        sa.UniqueConstraint("id", "business_id", name="uq_support_cases_id_business"),
        sa.UniqueConstraint("business_id", "case_number", name="uq_support_cases_business_number"),
    )
    op.create_index("ix_support_cases_business_status_activity", "support_cases", ["business_id", "status", "last_activity_at", "id"])
    op.create_index("ix_support_cases_business_priority_activity", "support_cases", ["business_id", "priority", "last_activity_at", "id"])
    op.create_index("ix_support_cases_business_customer", "support_cases", ["business_id", "customer_id", "id"])
    op.create_index("uq_support_cases_active_conversation", "support_cases", ["business_id", "conversation_id"], unique=True, postgresql_where=sa.text("status NOT IN ('resolved','closed')"))


def downgrade() -> None:
    bind = op.get_bind()

    incompatible_thread = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM conversations
            WHERE external_reference IS NOT NULL
            GROUP BY business_id, channel, external_reference
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()

    if incompatible_thread is not None:
        raise RuntimeError(
            "Cannot downgrade omnichannel support migration: "
            "conversation data now contains threads that the previous "
            "business/channel/external-reference uniqueness constraint "
            "cannot represent."
        )

    incompatible_delivery = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM conversation_messages
            WHERE delivery_status IN ('queued','dispatching','uncertain')
            LIMIT 1
            """
        )
    ).first()
    if incompatible_delivery is not None:
        raise RuntimeError(
            "Cannot downgrade omnichannel support migration: "
            "conversation messages contain durable outbound delivery states "
            "that the previous schema cannot represent."
        )

    incompatible_dispatch_job = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM background_jobs
            WHERE job_type = 'dispatch_conversation_message'
            LIMIT 1
            """
        )
    ).first()
    if incompatible_dispatch_job is not None:
        raise RuntimeError(
            "Cannot downgrade omnichannel support migration: "
            "durable conversation-message dispatch jobs still exist."
        )

    op.drop_index("uq_support_cases_active_conversation", table_name="support_cases")
    op.drop_index("ix_support_cases_business_customer", table_name="support_cases")
    op.drop_index("ix_support_cases_business_priority_activity", table_name="support_cases")
    op.drop_index("ix_support_cases_business_status_activity", table_name="support_cases")
    op.drop_table("support_cases")

    op.drop_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_background_jobs_valid_job_type"),
        "background_jobs",
        "job_type IN ("
        "'process_automation_event','resume_workflow_run',"
        "'process_scheduled_workflow','process_integration_event',"
        "'customer_agent_response','dispatch_action_execution',"
        "'reconcile_uncertain_attempt','mark_social_schedule_ready',"
        "'maintain_subscription','discover_competitors',"
        "'generate_content_plan','analyze_campaign_opportunities',"
        "'analyze_business_opportunity','commerce_initial_sync',"
        "'commerce_incremental_sync','commerce_webhook_reconcile',"
        "'google_merchant_status_sync','meta_catalog_status_sync',"
        "'google_ads_performance_sync','meta_ads_performance_sync'"
        ")",
    )
    op.drop_constraint(
        op.f("ck_background_jobs_consistent_conversation_message_reference"),
        "background_jobs",
        type_="check",
    )
    op.drop_constraint(
        "fk_jobs_conversation_message_business",
        "background_jobs",
        type_="foreignkey",
    )
    op.drop_column("background_jobs", "conversation_message_id")

    op.drop_constraint(
        op.f("ck_conversation_messages_valid_delivery_status"),
        "conversation_messages",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_conversation_messages_valid_delivery_status"),
        "conversation_messages",
        "delivery_status IN ("
        "'received','recorded','submitted','sent','delivered','read','failed'"
        ")",
    )
    op.drop_constraint(
        "uq_conversation_messages_business_client_request",
        "conversation_messages",
        type_="unique",
    )
    op.drop_column("conversation_messages", "client_request_id")

    op.drop_index("ix_conversations_business_external_identity", table_name="conversations")
    op.drop_index("ix_conversations_business_handling_activity", table_name="conversations")
    op.drop_index("uq_conversations_local_thread", table_name="conversations")
    op.drop_index(
        "uq_conversations_provider_thread_without_resource",
        table_name="conversations",
    )
    op.drop_index("uq_conversations_provider_thread", table_name="conversations")
    op.create_unique_constraint("uq_conversations_business_channel_external", "conversations", ["business_id", "channel", "external_reference"])
    op.drop_constraint(op.f("ck_conversations_valid_unread_count"), "conversations", type_="check")
    op.drop_constraint(op.f("ck_conversations_valid_handling_state"), "conversations", type_="check")
    op.drop_constraint(op.f("ck_conversations_valid_external_resource_reference"), "conversations", type_="check")
    op.drop_constraint("fk_conversations_channel_identity_business", "conversations", type_="foreignkey")
    op.drop_column("conversations", "unread_count")
    op.drop_column("conversations", "handling_state")
    op.drop_column("conversations", "external_resource_reference")
    op.drop_column("conversations", "customer_channel_identity_id")
    op.drop_index("ix_customer_channel_identities_business_provider", table_name="customer_channel_identities")
    op.drop_index("ix_customer_channel_identities_business_customer", table_name="customer_channel_identities")
    op.drop_table("customer_channel_identities")
