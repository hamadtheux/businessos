"""add SaaS billing, subscription, entitlement, and usage foundation

Revision ID: b8e1f4a7c962
Revises: a5d9e2f8b074
Create Date: 2026-08-23 00:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b8e1f4a7c962"
down_revision: str | None = "a5d9e2f8b074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id(name: str):
    return uuid5(NAMESPACE_URL, f"ai-business-os:billing:{name}")


FEATURES = (
    "ai_command_center", "ai_agents", "website_chatbot", "automations",
    "advanced_automations", "marketing_cmo", "campaigns",
    "competitor_intelligence", "trend_intelligence", "scheduling",
    "integrations", "advanced_analytics", "reports",
)
LIMITS = (
    "max_businesses", "max_members", "max_active_workflows", "max_integrations",
    "max_chatbot_sessions_month", "max_chatbot_messages_month",
    "max_ai_executions_month", "max_ai_input_tokens_month",
    "max_ai_output_tokens_month", "max_automation_runs_month",
)
FEATURE_KEYS_SQL = "(" + ",".join(f"'{key}'" for key in FEATURES) + ")"
LIMIT_KEYS_SQL = "(" + ",".join(f"'{key}'" for key in LIMITS) + ")"
REGISTERED_TYPED_VALUE_SQL = (
    f"(entitlement_key IN {FEATURE_KEYS_SQL} AND boolean_value IS NOT NULL AND integer_value IS NULL) OR "
    f"(entitlement_key IN {LIMIT_KEYS_SQL} AND boolean_value IS NULL "
    "AND integer_value IS NOT NULL AND integer_value >= 0)"
)
PLAN_VALUES: dict[str, dict[str, bool | int]] = {
    "free": {
        **{key: key in {"ai_command_center", "ai_agents", "scheduling", "reports"} for key in FEATURES},
        "max_businesses": 1, "max_members": 1, "max_active_workflows": 0,
        "max_integrations": 0, "max_chatbot_sessions_month": 0,
        "max_chatbot_messages_month": 0, "max_ai_executions_month": 20,
        "max_ai_input_tokens_month": 100_000, "max_ai_output_tokens_month": 25_000,
        "max_automation_runs_month": 0,
    },
    "starter": {
        **{key: key in {"ai_command_center", "ai_agents", "website_chatbot", "automations", "marketing_cmo", "campaigns", "scheduling", "integrations", "reports"} for key in FEATURES},
        "max_businesses": 2, "max_members": 5, "max_active_workflows": 5,
        "max_integrations": 2, "max_chatbot_sessions_month": 500,
        "max_chatbot_messages_month": 5_000, "max_ai_executions_month": 500,
        "max_ai_input_tokens_month": 2_000_000, "max_ai_output_tokens_month": 500_000,
        "max_automation_runs_month": 2_000,
    },
    "growth": {
        **{key: key not in {"advanced_automations"} for key in FEATURES},
        "max_businesses": 5, "max_members": 20, "max_active_workflows": 25,
        "max_integrations": 5, "max_chatbot_sessions_month": 5_000,
        "max_chatbot_messages_month": 50_000, "max_ai_executions_month": 3_000,
        "max_ai_input_tokens_month": 12_000_000, "max_ai_output_tokens_month": 3_000_000,
        "max_automation_runs_month": 20_000,
    },
    "pro": {
        **{key: True for key in FEATURES},
        "max_businesses": 20, "max_members": 100, "max_active_workflows": 100,
        "max_integrations": 8, "max_chatbot_sessions_month": 25_000,
        "max_chatbot_messages_month": 250_000, "max_ai_executions_month": 15_000,
        "max_ai_input_tokens_month": 60_000_000, "max_ai_output_tokens_month": 15_000_000,
        "max_automation_runs_month": 100_000,
    },
    # Hidden, bounded grandfathering preserves pre-billing tenant behavior.
    "legacy": {
        **{key: True for key in FEATURES},
        "max_businesses": 1_000, "max_members": 10_000, "max_active_workflows": 10_000,
        "max_integrations": 1_000, "max_chatbot_sessions_month": 10_000_000,
        "max_chatbot_messages_month": 100_000_000, "max_ai_executions_month": 10_000_000,
        "max_ai_input_tokens_month": 100_000_000_000, "max_ai_output_tokens_month": 100_000_000_000,
        "max_automation_runs_month": 100_000_000,
    },
}


def upgrade() -> None:
    op.create_table(
        "billing_plans",
        sa.Column("code", sa.String(48), nullable=False), sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False), sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("public", sa.Boolean(), server_default=sa.text("true"), nullable=False), sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("trial_days", sa.Integer(), server_default="0", nullable=False), sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]{0,47}$'", name=op.f("ck_billing_plans_valid_code")),
        sa.CheckConstraint("char_length(btrim(display_name)) BETWEEN 1 AND 100", name=op.f("ck_billing_plans_valid_display_name")),
        sa.CheckConstraint("trial_days BETWEEN 0 AND 365", name=op.f("ck_billing_plans_valid_trial_days")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_plans")), sa.UniqueConstraint("code", name="uq_billing_plans_code"),
    )
    op.create_table(
        "billing_plan_versions",
        sa.Column("plan_id", sa.Uuid(), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), server_default="USD", nullable=False), sa.Column("monthly_price_minor", sa.BigInteger(), nullable=True),
        sa.Column("yearly_price_minor", sa.BigInteger(), nullable=True), sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False), sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_billing_plan_versions_valid_version")),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_billing_plan_versions_valid_currency")),
        sa.CheckConstraint("monthly_price_minor IS NULL OR monthly_price_minor >= 0", name=op.f("ck_billing_plan_versions_valid_monthly_price")),
        sa.CheckConstraint("yearly_price_minor IS NULL OR yearly_price_minor >= 0", name=op.f("ck_billing_plan_versions_valid_yearly_price")),
        sa.ForeignKeyConstraint(["plan_id"], ["billing_plans.id"], name=op.f("fk_billing_plan_versions_plan_id_billing_plans"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_plan_versions")),
        sa.UniqueConstraint("id", "plan_id", name="uq_billing_plan_versions_id_plan"),
        sa.UniqueConstraint("plan_id", "version", name="uq_billing_plan_versions_plan_version"),
    )
    op.create_index("ix_billing_plan_versions_plan_active", "billing_plan_versions", ["plan_id", "active", "version"])
    op.create_table(
        "billing_plan_entitlements",
        sa.Column("plan_version_id", sa.Uuid(), nullable=False), sa.Column("entitlement_key", sa.String(64), nullable=False),
        sa.Column("boolean_value", sa.Boolean(), nullable=True), sa.Column("integer_value", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("entitlement_key ~ '^[a-z][a-z0-9_]{0,63}$'", name=op.f("ck_billing_plan_entitlements_valid_entitlement_key")),
        sa.CheckConstraint(REGISTERED_TYPED_VALUE_SQL, name=op.f("ck_billing_plan_entitlements_registered_typed_value")),
        sa.ForeignKeyConstraint(["plan_version_id"], ["billing_plan_versions.id"], name=op.f("fk_billing_plan_entitlements_plan_version_id_billing_plan_versions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_plan_entitlements")),
        sa.UniqueConstraint("plan_version_id", "entitlement_key", name="uq_plan_entitlements_version_key"),
    )
    op.create_table(
        "business_subscriptions",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("plan_id", sa.Uuid(), nullable=False), sa.Column("plan_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("source", sa.String(32), nullable=False), sa.Column("billing_interval", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), server_default="disabled", nullable=False), sa.Column("provider_customer_reference", sa.String(255), nullable=True),
        sa.Column("provider_subscription_reference", sa.String(255), nullable=True), sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False), sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), server_default=sa.text("false"), nullable=False), sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True), sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('trialing','active','canceled','expired','suspended')", name=op.f("ck_business_subscriptions_valid_status")),
        sa.CheckConstraint("billing_interval IN ('month','year')", name=op.f("ck_business_subscriptions_valid_billing_interval")),
        sa.CheckConstraint("source IN ('free_default','legacy_bootstrap','platform_admin','provider')", name=op.f("ck_business_subscriptions_valid_source")),
        sa.CheckConstraint("provider IN ('disabled')", name=op.f("ck_business_subscriptions_valid_provider")),
        sa.CheckConstraint("current_period_end > current_period_start", name=op.f("ck_business_subscriptions_valid_period")),
        sa.CheckConstraint("(trial_started_at IS NULL AND trial_ends_at IS NULL) OR (trial_started_at IS NOT NULL AND trial_ends_at IS NOT NULL AND trial_ends_at >= trial_started_at)", name=op.f("ck_business_subscriptions_valid_trial_period")),
        sa.CheckConstraint("status <> 'trialing' OR trial_started_at IS NOT NULL", name=op.f("ck_business_subscriptions_trialing_requires_period")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_business_subscriptions_business_id_businesses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["billing_plans.id"], name=op.f("fk_business_subscriptions_plan_id_billing_plans"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_version_id", "plan_id"], ["billing_plan_versions.id", "billing_plan_versions.plan_id"], name="fk_business_subscriptions_version_plan", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_subscriptions")), sa.UniqueConstraint("business_id", name="uq_business_subscriptions_business"),
    )
    op.create_index("ix_business_subscriptions_status_period_end", "business_subscriptions", ["status", "current_period_end", "id"])
    op.create_table(
        "billing_subscription_events",
        sa.Column("subscription_id", sa.Uuid(), nullable=False), sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False), sa.Column("actor_user_id", sa.Uuid(), nullable=True), sa.Column("from_status", sa.String(24), nullable=True),
        sa.Column("to_status", sa.String(24), nullable=True), sa.Column("from_plan_version_id", sa.Uuid(), nullable=True), sa.Column("to_plan_version_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("event_type ~ '^[a-z][a-z0-9_]{0,63}$'", name=op.f("ck_billing_subscription_events_valid_event_type")),
        sa.ForeignKeyConstraint(["subscription_id"], ["business_subscriptions.id"], name=op.f("fk_billing_subscription_events_subscription_id_business_subscriptions"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_billing_subscription_events_business_id_businesses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name=op.f("fk_billing_subscription_events_actor_user_id_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["from_plan_version_id"], ["billing_plan_versions.id"], name=op.f("fk_billing_subscription_events_from_plan_version_id_billing_plan_versions"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["to_plan_version_id"], ["billing_plan_versions.id"], name=op.f("fk_billing_subscription_events_to_plan_version_id_billing_plan_versions"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_subscription_events")), sa.UniqueConstraint("idempotency_key", name="uq_billing_subscription_events_idempotency"),
    )
    op.create_index("ix_billing_subscription_events_subscription_created", "billing_subscription_events", ["subscription_id", "created_at", "id"])
    op.create_table(
        "business_entitlement_overrides",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("entitlement_key", sa.String(64), nullable=False),
        sa.Column("boolean_value", sa.Boolean(), nullable=True), sa.Column("integer_value", sa.BigInteger(), nullable=True), sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("entitlement_key ~ '^[a-z][a-z0-9_]{0,63}$'", name=op.f("ck_business_entitlement_overrides_valid_entitlement_key")),
        sa.CheckConstraint(REGISTERED_TYPED_VALUE_SQL, name=op.f("ck_business_entitlement_overrides_registered_typed_value")),
        sa.CheckConstraint("char_length(btrim(reason)) BETWEEN 3 AND 500", name=op.f("ck_business_entitlement_overrides_valid_reason")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_business_entitlement_overrides_business_id_businesses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name=op.f("fk_business_entitlement_overrides_created_by_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_entitlement_overrides")),
    )
    op.create_index("ix_business_entitlement_overrides_business_key_created", "business_entitlement_overrides", ["business_id", "entitlement_key", "created_at", "id"])
    op.create_table(
        "billing_webhook_events",
        sa.Column("provider", sa.String(32), nullable=False), sa.Column("provider_event_id", sa.String(255), nullable=False), sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("business_id", sa.Uuid(), nullable=True), sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True), sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('received','processed','rejected','failed')", name=op.f("ck_billing_webhook_events_valid_status")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_billing_webhook_events_business_id_businesses"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_webhook_events")), sa.UniqueConstraint("provider", "provider_event_id", name="uq_billing_webhook_events_provider_event"),
    )
    op.create_table(
        "billing_audit_events",
        sa.Column("business_id", sa.Uuid(), nullable=True), sa.Column("actor_user_id", sa.Uuid(), nullable=True), sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(48), nullable=False), sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("before_state", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("after_state", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("event_type ~ '^[a-z][a-z0-9_.]{0,79}$'", name=op.f("ck_billing_audit_events_valid_event_type")),
        sa.CheckConstraint("char_length(btrim(reason)) BETWEEN 3 AND 500", name=op.f("ck_billing_audit_events_valid_reason")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f("fk_billing_audit_events_business_id_businesses"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name=op.f("fk_billing_audit_events_actor_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_audit_events")),
    )
    op.create_index("ix_billing_audit_events_business_created", "billing_audit_events", ["business_id", "created_at", "id"])

    now = datetime(2026, 8, 23, tzinfo=UTC)
    plan_meta = {
        "free": ("Free", "A limited baseline for getting started.", 0, 0, 0, True),
        "starter": ("Starter", "Core AI, chatbot, automation, and integration tools.", 1, 2900, 29000, True),
        "growth": ("Growth", "Expanded intelligence, capacity, and analytics for growing teams.", 2, 7900, 79000, True),
        "pro": ("Pro", "Advanced automation and higher operating limits.", 3, 14900, 149000, True),
        "legacy": ("Legacy", "Grandfathered access for tenants created before billing launch.", 99, None, None, False),
    }
    plans = sa.table("billing_plans", sa.column("id", sa.Uuid()), sa.column("code", sa.String()), sa.column("display_name", sa.String()), sa.column("description", sa.Text()), sa.column("active", sa.Boolean()), sa.column("public", sa.Boolean()), sa.column("sort_order", sa.Integer()), sa.column("trial_days", sa.Integer()), sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)))
    versions = sa.table("billing_plan_versions", sa.column("id", sa.Uuid()), sa.column("plan_id", sa.Uuid()), sa.column("version", sa.Integer()), sa.column("currency", sa.String()), sa.column("monthly_price_minor", sa.BigInteger()), sa.column("yearly_price_minor", sa.BigInteger()), sa.column("active", sa.Boolean()), sa.column("effective_at", sa.DateTime(timezone=True)), sa.column("retired_at", sa.DateTime(timezone=True)), sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)))
    entitlements = sa.table("billing_plan_entitlements", sa.column("id", sa.Uuid()), sa.column("plan_version_id", sa.Uuid()), sa.column("entitlement_key", sa.String()), sa.column("boolean_value", sa.Boolean()), sa.column("integer_value", sa.BigInteger()), sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)))
    op.bulk_insert(plans, [{"id": _id(f"plan:{code}"), "code": code, "display_name": meta[0], "description": meta[1], "active": True, "public": meta[5], "sort_order": meta[2], "trial_days": 14 if code in {"starter", "growth", "pro"} else 0, "created_at": now, "updated_at": now} for code, meta in plan_meta.items()])
    op.bulk_insert(versions, [{"id": _id(f"version:{code}:1"), "plan_id": _id(f"plan:{code}"), "version": 1, "currency": "USD", "monthly_price_minor": meta[3], "yearly_price_minor": meta[4], "active": True, "effective_at": now, "retired_at": None, "created_at": now, "updated_at": now} for code, meta in plan_meta.items()])
    op.bulk_insert(entitlements, [{"id": _id(f"entitlement:{code}:1:{key}"), "plan_version_id": _id(f"version:{code}:1"), "entitlement_key": key, "boolean_value": value if type(value) is bool else None, "integer_value": value if type(value) is int else None, "created_at": now, "updated_at": now} for code, values in PLAN_VALUES.items() for key, value in values.items()])

    op.execute(sa.text("""
        INSERT INTO business_subscriptions (
            id, business_id, plan_id, plan_version_id, status, source,
            billing_interval, provider, current_period_start, current_period_end,
            trial_ends_at, cancel_at_period_end, created_at, updated_at
        )
        SELECT gen_random_uuid(), b.id, :plan_id, :version_id, 'active',
               'legacy_bootstrap', 'month', 'disabled', date_trunc('month', now()),
               date_trunc('month', now()) + interval '1 month', NULL, false, now(), now()
        FROM businesses b
    """).bindparams(plan_id=_id("plan:legacy"), version_id=_id("version:legacy:1")))
    op.execute("""
        INSERT INTO billing_subscription_events (
            id, subscription_id, business_id, event_type, idempotency_key,
            from_status, to_status, to_plan_version_id, reason, created_at
        )
        SELECT gen_random_uuid(), s.id, s.business_id, 'subscription_created',
               'legacy-bootstrap:' || s.id::text, NULL, s.status,
               s.plan_version_id, 'Existing tenant assigned bounded Legacy access.', now()
        FROM business_subscriptions s
        WHERE s.source = 'legacy_bootstrap'
    """)

    # Provisioning is database-enforced so every future tenant receives an
    # explicit Free subscription in the same transaction as the business.
    op.execute("""
        CREATE FUNCTION provision_free_business_subscription()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE selected_plan uuid; selected_version uuid; new_subscription uuid := gen_random_uuid();
        BEGIN
            SELECT p.id, v.id INTO selected_plan, selected_version
            FROM billing_plans p
            JOIN billing_plan_versions v ON v.plan_id = p.id
            WHERE p.code = 'free' AND p.active = true AND v.active = true
            ORDER BY v.version DESC LIMIT 1;
            IF selected_plan IS NULL OR selected_version IS NULL THEN
                RAISE EXCEPTION 'active free billing baseline is unavailable';
            END IF;
            INSERT INTO business_subscriptions (
                id, business_id, plan_id, plan_version_id, status, source,
                billing_interval, provider, current_period_start,
                current_period_end, cancel_at_period_end, created_at, updated_at
            ) VALUES (
                new_subscription, NEW.id, selected_plan, selected_version,
                'active', 'free_default', 'month', 'disabled',
                date_trunc('month', now()),
                date_trunc('month', now()) + interval '1 month',
                false, now(), now()
            );
            INSERT INTO billing_subscription_events (
                id, subscription_id, business_id, event_type, idempotency_key,
                from_status, to_status, to_plan_version_id, reason, created_at
            ) VALUES (
                gen_random_uuid(), new_subscription, NEW.id, 'subscription_created',
                'subscription-created:' || new_subscription::text, NULL, 'active',
                selected_version, 'New tenant assigned the explicit Free baseline.', now()
            );
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_businesses_provision_free_subscription
        AFTER INSERT ON businesses FOR EACH ROW
        EXECUTE FUNCTION provision_free_business_subscription()
    """)
    op.execute("""
        CREATE FUNCTION enforce_owner_business_entitlement()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE owned_count bigint; allowed_count bigint;
        BEGIN
            IF NEW.role <> 'owner' OR NEW.status <> 'active' THEN RETURN NEW; END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended('billing:owner:' || NEW.user_id::text, 0));
            SELECT count(*) INTO owned_count FROM business_memberships m
             WHERE m.user_id = NEW.user_id AND m.role = 'owner' AND m.status = 'active'
               AND m.id <> NEW.id;
            IF owned_count = 0 THEN RETURN NEW; END IF;
            SELECT max(COALESCE(
                CASE WHEN o.active = true AND (o.expires_at IS NULL OR o.expires_at > now())
                     THEN o.integer_value ELSE NULL END,
                e.integer_value, 0
            )) INTO allowed_count
            FROM business_memberships m
            JOIN business_subscriptions s ON s.business_id = m.business_id
            JOIN billing_plan_entitlements e ON e.plan_version_id = s.plan_version_id
             AND e.entitlement_key = 'max_businesses'
            LEFT JOIN LATERAL (
                SELECT x.integer_value, x.active, x.expires_at FROM business_entitlement_overrides x
                 WHERE x.business_id = m.business_id AND x.entitlement_key = 'max_businesses'
                 ORDER BY x.created_at DESC, x.id DESC LIMIT 1
            ) o ON true
            WHERE m.user_id = NEW.user_id AND m.role = 'owner' AND m.status = 'active'
              AND (
                (s.status = 'active' AND (s.cancel_at_period_end = false OR s.current_period_end > now()))
                OR (s.status = 'trialing' AND s.trial_ends_at > now()
                    AND (s.cancel_at_period_end = false OR s.current_period_end > now()))
              );
            allowed_count := COALESCE(allowed_count, 1);
            IF owned_count >= allowed_count THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001',
                  MESSAGE = 'billing entitlement exceeded',
                  DETAIL = 'max_businesses';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_business_memberships_owner_limit
        BEFORE INSERT OR UPDATE OF user_id, role, status ON business_memberships
        FOR EACH ROW EXECUTE FUNCTION enforce_owner_business_entitlement()
    """)
    op.execute("""
        CREATE FUNCTION billing_effective_integer(target_business uuid, requested_key text)
        RETURNS bigint LANGUAGE plpgsql STABLE AS $$
        DECLARE selected_version uuid; overridden bigint; planned bigint;
                override_active boolean; override_expires timestamptz;
                subscription_permitted boolean := false;
        BEGIN
            SELECT s.plan_version_id INTO selected_version FROM business_subscriptions s
             WHERE s.business_id = target_business
               AND (
                 (s.status = 'active' AND (s.cancel_at_period_end = false OR s.current_period_end > now()))
                 OR (s.status = 'trialing' AND s.trial_ends_at > now()
                     AND (s.cancel_at_period_end = false OR s.current_period_end > now()))
               );
            subscription_permitted := selected_version IS NOT NULL;
            IF selected_version IS NULL THEN
                SELECT v.id INTO selected_version FROM billing_plans p
                JOIN billing_plan_versions v ON v.plan_id = p.id
                WHERE p.code = 'free' AND p.active = true AND v.active = true
                ORDER BY v.version DESC LIMIT 1;
            END IF;
            IF subscription_permitted THEN
                SELECT x.integer_value, x.active, x.expires_at
                  INTO overridden, override_active, override_expires
                  FROM business_entitlement_overrides x
                 WHERE x.business_id = target_business AND x.entitlement_key = requested_key
                 ORDER BY x.created_at DESC, x.id DESC LIMIT 1;
            END IF;
            IF override_active = true AND (override_expires IS NULL OR override_expires > now())
               AND overridden IS NOT NULL THEN RETURN overridden; END IF;
            SELECT e.integer_value INTO planned FROM billing_plan_entitlements e
             WHERE e.plan_version_id = selected_version AND e.entitlement_key = requested_key;
            RETURN COALESCE(planned, 0);
        END;
        $$;
    """)
    op.execute("""
        CREATE FUNCTION billing_effective_boolean(target_business uuid, requested_key text)
        RETURNS boolean LANGUAGE plpgsql STABLE AS $$
        DECLARE selected_version uuid; overridden boolean; planned boolean;
                override_active boolean; override_expires timestamptz;
                subscription_permitted boolean := false;
        BEGIN
            SELECT s.plan_version_id INTO selected_version FROM business_subscriptions s
             WHERE s.business_id = target_business
               AND (
                 (s.status = 'active' AND (s.cancel_at_period_end = false OR s.current_period_end > now()))
                 OR (s.status = 'trialing' AND s.trial_ends_at > now()
                     AND (s.cancel_at_period_end = false OR s.current_period_end > now()))
               );
            subscription_permitted := selected_version IS NOT NULL;
            IF selected_version IS NULL THEN
                SELECT v.id INTO selected_version FROM billing_plans p
                JOIN billing_plan_versions v ON v.plan_id = p.id
                WHERE p.code = 'free' AND p.active = true AND v.active = true
                ORDER BY v.version DESC LIMIT 1;
            END IF;
            IF subscription_permitted THEN
                SELECT x.boolean_value, x.active, x.expires_at
                  INTO overridden, override_active, override_expires
                  FROM business_entitlement_overrides x
                 WHERE x.business_id = target_business AND x.entitlement_key = requested_key
                 ORDER BY x.created_at DESC, x.id DESC LIMIT 1;
            END IF;
            IF override_active = true AND (override_expires IS NULL OR override_expires > now())
               AND overridden IS NOT NULL THEN RETURN overridden; END IF;
            SELECT e.boolean_value INTO planned FROM billing_plan_entitlements e
             WHERE e.plan_version_id = selected_version AND e.entitlement_key = requested_key;
            RETURN COALESCE(planned, false);
        END;
        $$;
    """)
    op.execute("""
        CREATE FUNCTION billing_effective_period_start(target_business uuid)
        RETURNS timestamptz LANGUAGE plpgsql STABLE AS $$
        DECLARE period_start timestamptz; period_end timestamptz; selected_interval text;
        BEGIN
            SELECT s.current_period_start, s.current_period_end, s.billing_interval
              INTO period_start, period_end, selected_interval
              FROM business_subscriptions s WHERE s.business_id = target_business
               AND (
                 (s.status = 'active' AND (s.cancel_at_period_end = false OR s.current_period_end > now()))
                 OR (s.status = 'trialing' AND s.trial_ends_at > now()
                     AND (s.cancel_at_period_end = false OR s.current_period_end > now()))
               );
            IF period_start IS NULL OR period_end IS NULL THEN
                RETURN date_trunc('month', now());
            END IF;
            WHILE period_end <= now() LOOP
                period_start := period_end;
                period_end := period_end + CASE WHEN selected_interval = 'year' THEN interval '1 year' ELSE interval '1 month' END;
            END LOOP;
            RETURN period_start;
        END;
        $$;
    """)
    op.execute("""
        CREATE FUNCTION enforce_business_member_entitlement()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_count bigint; allowed_count bigint;
        BEGIN
            IF NEW.status <> 'active' THEN RETURN NEW; END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended('billing:' || NEW.business_id::text || '-max_members', 0));
            SELECT count(*) INTO current_count FROM business_memberships m
             WHERE m.business_id = NEW.business_id AND m.status = 'active' AND m.id <> NEW.id;
            allowed_count := billing_effective_integer(NEW.business_id, 'max_members');
            IF current_count >= allowed_count THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing entitlement exceeded', DETAIL = 'max_members';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_business_memberships_member_limit
        BEFORE INSERT OR UPDATE OF business_id, status ON business_memberships
        FOR EACH ROW EXECUTE FUNCTION enforce_business_member_entitlement()
    """)
    op.execute("""
        CREATE FUNCTION enforce_workflow_entitlement()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_count bigint; allowed_count bigint;
        BEGIN
            IF NEW.status <> 'active' OR NEW.enabled <> true THEN RETURN NEW; END IF;
            IF TG_OP = 'UPDATE' AND OLD.status = 'active' AND OLD.enabled = true THEN RETURN NEW; END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended('billing:' || NEW.business_id::text || '-max_active_workflows', 0));
            IF billing_effective_boolean(NEW.business_id, 'automations') <> true THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing feature unavailable', DETAIL = 'automations';
            END IF;
            SELECT count(*) INTO current_count FROM automation_workflows w
             WHERE w.business_id = NEW.business_id AND w.status = 'active' AND w.enabled = true AND w.id <> NEW.id;
            allowed_count := billing_effective_integer(NEW.business_id, 'max_active_workflows');
            IF current_count >= allowed_count THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing entitlement exceeded', DETAIL = 'max_active_workflows';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_automation_workflows_plan_limit
        BEFORE INSERT OR UPDATE OF status, enabled ON automation_workflows
        FOR EACH ROW EXECUTE FUNCTION enforce_workflow_entitlement()
    """)
    op.execute("""
        CREATE FUNCTION enforce_integration_entitlement()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_count bigint; allowed_count bigint;
        BEGIN
            IF NEW.status NOT IN ('connected','degraded') OR NEW.authentication_state <> 'authorized' THEN RETURN NEW; END IF;
            IF TG_OP = 'UPDATE' AND OLD.status IN ('connected','degraded') AND OLD.authentication_state = 'authorized' THEN RETURN NEW; END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended('billing:' || NEW.business_id::text || '-max_integrations', 0));
            IF billing_effective_boolean(NEW.business_id, 'integrations') <> true THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing feature unavailable', DETAIL = 'integrations';
            END IF;
            SELECT count(*) INTO current_count FROM integration_connections c
             WHERE c.business_id = NEW.business_id AND c.status IN ('connected','degraded')
               AND c.authentication_state = 'authorized' AND c.id <> NEW.id;
            allowed_count := billing_effective_integer(NEW.business_id, 'max_integrations');
            IF current_count >= allowed_count THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing entitlement exceeded', DETAIL = 'max_integrations';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_integration_connections_plan_limit
        BEFORE INSERT OR UPDATE OF status, authentication_state ON integration_connections
        FOR EACH ROW EXECUTE FUNCTION enforce_integration_entitlement()
    """)
    op.execute("""
        CREATE FUNCTION enforce_ai_execution_entitlement()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_count bigint; allowed_count bigint; feature_key text;
        BEGIN
            feature_key := CASE WHEN NEW.trigger_type = 'command' THEN 'ai_command_center' ELSE 'ai_agents' END;
            PERFORM pg_advisory_xact_lock(hashtextextended('billing:' || NEW.business_id::text || '-max_ai_executions_month', 0));
            IF billing_effective_boolean(NEW.business_id, feature_key) <> true THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing feature unavailable', DETAIL = feature_key;
            END IF;
            SELECT count(*) INTO current_count FROM ai_agent_executions x
             WHERE x.business_id = NEW.business_id AND x.created_at >= billing_effective_period_start(NEW.business_id);
            allowed_count := billing_effective_integer(NEW.business_id, 'max_ai_executions_month');
            IF current_count >= allowed_count THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing entitlement exceeded', DETAIL = 'max_ai_executions_month';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_ai_agent_executions_plan_limit
        BEFORE INSERT ON ai_agent_executions FOR EACH ROW
        EXECUTE FUNCTION enforce_ai_execution_entitlement()
    """)
    op.execute("""
        CREATE FUNCTION enforce_chatbot_session_entitlement()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_count bigint; allowed_count bigint;
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended('billing:' || NEW.business_id::text || '-max_chatbot_sessions_month', 0));
            IF billing_effective_boolean(NEW.business_id, 'website_chatbot') <> true THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing feature unavailable', DETAIL = 'website_chatbot';
            END IF;
            SELECT count(*) INTO current_count FROM chatbot_sessions x
             WHERE x.business_id = NEW.business_id AND x.started_at >= billing_effective_period_start(NEW.business_id);
            allowed_count := billing_effective_integer(NEW.business_id, 'max_chatbot_sessions_month');
            IF current_count >= allowed_count THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing entitlement exceeded', DETAIL = 'max_chatbot_sessions_month';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_chatbot_sessions_plan_limit
        BEFORE INSERT ON chatbot_sessions FOR EACH ROW
        EXECUTE FUNCTION enforce_chatbot_session_entitlement()
    """)
    op.execute("""
        CREATE FUNCTION enforce_chatbot_message_entitlement()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_count bigint; allowed_count bigint; increment_count bigint;
        BEGIN
            increment_count := NEW.message_count - OLD.message_count;
            IF increment_count <= 0 THEN RETURN NEW; END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended('billing:' || NEW.business_id::text || '-max_chatbot_messages_month', 0));
            SELECT COALESCE(sum(x.message_count), 0) INTO current_count FROM chatbot_sessions x
             WHERE x.business_id = NEW.business_id AND x.started_at >= billing_effective_period_start(NEW.business_id);
            allowed_count := billing_effective_integer(NEW.business_id, 'max_chatbot_messages_month');
            IF current_count + increment_count > allowed_count THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing entitlement exceeded', DETAIL = 'max_chatbot_messages_month';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_chatbot_sessions_message_limit
        BEFORE UPDATE OF message_count ON chatbot_sessions FOR EACH ROW
        EXECUTE FUNCTION enforce_chatbot_message_entitlement()
    """)
    op.execute("""
        CREATE FUNCTION enforce_automation_run_entitlement()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_count bigint; allowed_count bigint;
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended('billing:' || NEW.business_id::text || '-max_automation_runs_month', 0));
            IF billing_effective_boolean(NEW.business_id, 'automations') <> true THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing feature unavailable', DETAIL = 'automations';
            END IF;
            SELECT count(*) INTO current_count FROM automation_workflow_runs x
             WHERE x.business_id = NEW.business_id AND x.created_at >= billing_effective_period_start(NEW.business_id);
            allowed_count := billing_effective_integer(NEW.business_id, 'max_automation_runs_month');
            IF current_count >= allowed_count THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing entitlement exceeded', DETAIL = 'max_automation_runs_month';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_automation_workflow_runs_plan_limit
        BEFORE INSERT ON automation_workflow_runs FOR EACH ROW
        EXECUTE FUNCTION enforce_automation_run_entitlement()
    """)
    op.execute("""
        CREATE FUNCTION enforce_plan_version_immutability()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing plan versions are immutable';
            END IF;
            IF NEW.plan_id IS DISTINCT FROM OLD.plan_id
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.currency IS DISTINCT FROM OLD.currency
               OR NEW.monthly_price_minor IS DISTINCT FROM OLD.monthly_price_minor
               OR NEW.yearly_price_minor IS DISTINCT FROM OLD.yearly_price_minor
               OR NEW.effective_at IS DISTINCT FROM OLD.effective_at THEN
                RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing plan version content is immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_billing_plan_versions_immutable
        BEFORE UPDATE OR DELETE ON billing_plan_versions FOR EACH ROW
        EXECUTE FUNCTION enforce_plan_version_immutability()
    """)
    op.execute("""
        CREATE FUNCTION enforce_plan_entitlement_immutability()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'billing plan entitlements are immutable';
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_billing_plan_entitlements_immutable
        BEFORE UPDATE OR DELETE ON billing_plan_entitlements FOR EACH ROW
        EXECUTE FUNCTION enforce_plan_entitlement_immutability()
    """)

    op.add_column("background_jobs", sa.Column("subscription_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f("fk_background_jobs_subscription_id_business_subscriptions"), "background_jobs", "business_subscriptions", ["subscription_id"], ["id"], ondelete="CASCADE")
    op.drop_constraint(op.f("ck_background_jobs_valid_job_type"), "background_jobs", type_="check")
    op.create_check_constraint("valid_job_type", "background_jobs", "job_type IN ('process_automation_event','resume_workflow_run','process_scheduled_workflow','process_integration_event','reconcile_uncertain_attempt','mark_social_schedule_ready','maintain_subscription')")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_billing_plan_entitlements_immutable ON billing_plan_entitlements")
    op.execute("DROP FUNCTION IF EXISTS enforce_plan_entitlement_immutability()")
    op.execute("DROP TRIGGER IF EXISTS trg_billing_plan_versions_immutable ON billing_plan_versions")
    op.execute("DROP FUNCTION IF EXISTS enforce_plan_version_immutability()")
    op.execute("DROP TRIGGER IF EXISTS trg_automation_workflow_runs_plan_limit ON automation_workflow_runs")
    op.execute("DROP FUNCTION IF EXISTS enforce_automation_run_entitlement()")
    op.execute("DROP TRIGGER IF EXISTS trg_chatbot_sessions_message_limit ON chatbot_sessions")
    op.execute("DROP FUNCTION IF EXISTS enforce_chatbot_message_entitlement()")
    op.execute("DROP TRIGGER IF EXISTS trg_chatbot_sessions_plan_limit ON chatbot_sessions")
    op.execute("DROP FUNCTION IF EXISTS enforce_chatbot_session_entitlement()")
    op.execute("DROP TRIGGER IF EXISTS trg_ai_agent_executions_plan_limit ON ai_agent_executions")
    op.execute("DROP FUNCTION IF EXISTS enforce_ai_execution_entitlement()")
    op.execute("DROP TRIGGER IF EXISTS trg_integration_connections_plan_limit ON integration_connections")
    op.execute("DROP FUNCTION IF EXISTS enforce_integration_entitlement()")
    op.execute("DROP TRIGGER IF EXISTS trg_automation_workflows_plan_limit ON automation_workflows")
    op.execute("DROP FUNCTION IF EXISTS enforce_workflow_entitlement()")
    op.execute("DROP TRIGGER IF EXISTS trg_business_memberships_member_limit ON business_memberships")
    op.execute("DROP FUNCTION IF EXISTS enforce_business_member_entitlement()")
    op.execute("DROP FUNCTION IF EXISTS billing_effective_period_start(uuid)")
    op.execute("DROP FUNCTION IF EXISTS billing_effective_boolean(uuid, text)")
    op.execute("DROP FUNCTION IF EXISTS billing_effective_integer(uuid, text)")
    op.execute("DROP TRIGGER IF EXISTS trg_business_memberships_owner_limit ON business_memberships")
    op.execute("DROP FUNCTION IF EXISTS enforce_owner_business_entitlement()")
    op.execute("DROP TRIGGER IF EXISTS trg_businesses_provision_free_subscription ON businesses")
    op.execute("DROP FUNCTION IF EXISTS provision_free_business_subscription()")
    op.drop_constraint(op.f("ck_background_jobs_valid_job_type"), "background_jobs", type_="check")
    op.create_check_constraint("valid_job_type", "background_jobs", "job_type IN ('process_automation_event','resume_workflow_run','process_scheduled_workflow','process_integration_event','reconcile_uncertain_attempt','mark_social_schedule_ready')")
    op.drop_constraint(op.f("fk_background_jobs_subscription_id_business_subscriptions"), "background_jobs", type_="foreignkey")
    op.drop_column("background_jobs", "subscription_id")
    op.drop_index("ix_billing_audit_events_business_created", table_name="billing_audit_events"); op.drop_table("billing_audit_events")
    op.drop_table("billing_webhook_events")
    op.drop_index("ix_business_entitlement_overrides_business_key_created", table_name="business_entitlement_overrides"); op.drop_table("business_entitlement_overrides")
    op.drop_index("ix_billing_subscription_events_subscription_created", table_name="billing_subscription_events"); op.drop_table("billing_subscription_events")
    op.drop_index("ix_business_subscriptions_status_period_end", table_name="business_subscriptions"); op.drop_table("business_subscriptions")
    op.drop_table("billing_plan_entitlements")
    op.drop_index("ix_billing_plan_versions_plan_active", table_name="billing_plan_versions"); op.drop_table("billing_plan_versions")
    op.drop_table("billing_plans")
