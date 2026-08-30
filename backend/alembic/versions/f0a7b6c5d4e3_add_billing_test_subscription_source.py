"""add explicit billing test subscription source

Revision ID: f0a7b6c5d4e3
Revises: c8e4f1a2b730
Create Date: 2026-08-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f0a7b6c5d4e3"
down_revision: str | None = "c8e4f1a2b730"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SOURCE_CONSTRAINT = "ck_business_subscriptions_valid_source"


def upgrade() -> None:
    op.drop_constraint(
        op.f(_SOURCE_CONSTRAINT),
        "business_subscriptions",
        type_="check",
    )
    op.create_check_constraint(
        op.f(_SOURCE_CONSTRAINT),
        "business_subscriptions",
        "source IN ('free_default','legacy_bootstrap','platform_admin','provider','billing_test_mode')",
    )


def downgrade() -> None:
    # A downgrade must fail closed: remove test-granted paid access before the
    # old source constraint is restored. No payment/provider evidence is made.
    op.execute(sa.text("""
        UPDATE business_subscriptions AS subscription
        SET plan_id = free_plan.id,
            plan_version_id = free_plan.version_id,
            status = 'active',
            source = 'free_default',
            billing_interval = 'month',
            provider = 'disabled',
            provider_customer_reference = NULL,
            provider_subscription_reference = NULL,
            current_period_start = date_trunc('month', now()),
            current_period_end = date_trunc('month', now()) + interval '1 month',
            trial_started_at = NULL,
            trial_ends_at = NULL,
            cancel_at_period_end = false,
            canceled_at = NULL,
            ended_at = NULL
        FROM (
            SELECT plan.id, version.id AS version_id
            FROM billing_plans AS plan
            JOIN billing_plan_versions AS version ON version.plan_id = plan.id
            WHERE plan.code = 'free' AND plan.active = true AND version.active = true
            ORDER BY version.version DESC
            LIMIT 1
        ) AS free_plan
        WHERE subscription.source = 'billing_test_mode'
    """))
    op.drop_constraint(
        op.f(_SOURCE_CONSTRAINT),
        "business_subscriptions",
        type_="check",
    )
    op.create_check_constraint(
        op.f(_SOURCE_CONSTRAINT),
        "business_subscriptions",
        "source IN ('free_default','legacy_bootstrap','platform_admin','provider')",
    )
