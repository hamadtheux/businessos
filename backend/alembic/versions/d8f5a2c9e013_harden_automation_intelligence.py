"""harden automation-first intelligence for production

Revision ID: d8f5a2c9e013
Revises: c7d4e9a21f06
Create Date: 2026-08-24 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d8f5a2c9e013"
down_revision: str | None = "c7d4e9a21f06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Composite tenant references require a matching unique target key.
    op.create_unique_constraint(
        "uq_ai_agent_executions_id_business",
        "ai_agent_executions",
        ["id", "business_id"],
    )
    op.create_unique_constraint(
        "uq_approval_requests_id_business",
        "approval_requests",
        ["id", "business_id"],
    )

    op.drop_constraint(
        op.f("fk_ai_actions_execution_id_ai_agent_executions"),
        "ai_actions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_ai_actions_execution_business",
        "ai_actions",
        "ai_agent_executions",
        ["execution_id", "business_id"],
        ["id", "business_id"],
        ondelete="CASCADE",
    )
    op.add_column(
        "ai_actions", sa.Column("authorized_payload_hash", sa.String(64), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_ai_actions_valid_authorized_payload_hash"),
        "ai_actions",
        "authorized_payload_hash IS NULL OR "
        "(char_length(authorized_payload_hash) = 64 AND "
        "authorized_payload_hash ~ '^[0-9a-f]{64}$')",
    )

    for name in (
        "results_processed",
        "new_candidates",
        "refreshed_candidates",
        "evidence_added",
    ):
        op.add_column(
            "competitor_discovery_runs",
            sa.Column(name, sa.Integer(), server_default="0", nullable=False),
        )
        upper_bound = 20_000 if name == "evidence_added" else 1_000
        op.create_check_constraint(
            op.f(f"ck_competitor_discovery_runs_valid_{name}"),
            "competitor_discovery_runs",
            f"{name} BETWEEN 0 AND {upper_bound}",
        )

    op.add_column(
        "competitor_candidate_evidence",
        sa.Column("discovery_run_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE competitor_candidate_evidence AS evidence "
        "SET discovery_run_id = candidate.discovery_run_id "
        "FROM competitor_candidates AS candidate "
        "WHERE candidate.id = evidence.candidate_id "
        "AND candidate.business_id = evidence.business_id"
    )
    op.alter_column(
        "competitor_candidate_evidence", "discovery_run_id", nullable=False
    )
    op.drop_constraint(
        "uq_competitor_candidate_evidence_candidate_fingerprint",
        "competitor_candidate_evidence",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_competitor_evidence_candidate_run_fingerprint",
        "competitor_candidate_evidence",
        ["candidate_id", "discovery_run_id", "fingerprint"],
    )
    op.create_foreign_key(
        "fk_competitor_candidate_evidence_run_business",
        "competitor_candidate_evidence",
        "competitor_discovery_runs",
        ["discovery_run_id", "business_id"],
        ["id", "business_id"],
        ondelete="CASCADE",
    )

    # Campaign is the sole owner of the optional one-to-one audience link.
    op.execute(
        "WITH ranked AS ("
        " SELECT id, business_id, campaign_id,"
        " row_number() OVER (PARTITION BY business_id, campaign_id "
        " ORDER BY created_at, id) AS position"
        " FROM audience_hypotheses WHERE campaign_id IS NOT NULL"
        ") UPDATE marketing_campaigns AS campaign "
        "SET audience_hypothesis_id = ranked.id FROM ranked "
        "WHERE ranked.position = 1 AND campaign.id = ranked.campaign_id "
        "AND campaign.business_id = ranked.business_id "
        "AND campaign.audience_hypothesis_id IS NULL"
    )
    op.execute(
        "UPDATE marketing_campaigns AS campaign "
        "SET audience_hypothesis_id = NULL "
        "WHERE audience_hypothesis_id IS NOT NULL AND NOT EXISTS ("
        " SELECT 1 FROM audience_hypotheses AS audience"
        " WHERE audience.id = campaign.audience_hypothesis_id"
        " AND audience.business_id = campaign.business_id)"
    )
    op.execute(
        "WITH duplicates AS ("
        " SELECT id, row_number() OVER ("
        " PARTITION BY business_id, audience_hypothesis_id ORDER BY created_at, id"
        " ) AS position FROM marketing_campaigns"
        " WHERE audience_hypothesis_id IS NOT NULL"
        ") UPDATE marketing_campaigns AS campaign "
        "SET audience_hypothesis_id = NULL FROM duplicates "
        "WHERE campaign.id = duplicates.id AND duplicates.position > 1"
    )
    op.drop_constraint(
        "fk_audience_hypotheses_campaign_business",
        "audience_hypotheses",
        type_="foreignkey",
    )
    op.drop_column("audience_hypotheses", "campaign_id")
    op.add_column("audience_hypotheses", sa.Column("min_age", sa.Integer(), nullable=True))
    op.add_column("audience_hypotheses", sa.Column("max_age", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_audience_hypotheses_valid_age_range"),
        "audience_hypotheses",
        "(min_age IS NULL AND max_age IS NULL) OR "
        "(min_age BETWEEN 18 AND 100 AND max_age BETWEEN min_age AND 100)",
    )
    op.drop_constraint(
        "fk_mkt_campaigns_audience_hypothesis",
        "marketing_campaigns",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_mkt_campaigns_audience_hypothesis_business",
        "marketing_campaigns",
        "audience_hypotheses",
        ["audience_hypothesis_id", "business_id"],
        ["id", "business_id"],
    )
    op.create_unique_constraint(
        "uq_marketing_campaigns_business_audience_hypothesis",
        "marketing_campaigns",
        ["business_id", "audience_hypothesis_id"],
    )

    op.execute(
        "UPDATE marketing_competitors AS competitor "
        "SET source_candidate_id = NULL "
        "WHERE source_candidate_id IS NOT NULL AND NOT EXISTS ("
        " SELECT 1 FROM competitor_candidates AS candidate"
        " WHERE candidate.id = competitor.source_candidate_id"
        " AND candidate.business_id = competitor.business_id)"
    )
    op.drop_constraint(
        "fk_mkt_competitors_source_candidate",
        "marketing_competitors",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_mkt_competitors_source_candidate_business",
        "marketing_competitors",
        "competitor_candidates",
        ["source_candidate_id", "business_id"],
        ["id", "business_id"],
    )

    # Proposal rows are reconstructible boundary metadata. Remove any legacy
    # cross-tenant references before making the invariant non-optional.
    op.execute(
        "DELETE FROM marketing_action_proposals AS proposal WHERE NOT EXISTS ("
        " SELECT 1 FROM ai_agent_executions AS execution"
        " WHERE execution.id = proposal.execution_id"
        " AND execution.business_id = proposal.business_id) OR ("
        " proposal.approval_id IS NOT NULL AND NOT EXISTS ("
        " SELECT 1 FROM approval_requests AS approval"
        " WHERE approval.id = proposal.approval_id"
        " AND approval.business_id = proposal.business_id))"
    )
    op.drop_constraint(
        op.f("fk_marketing_action_proposals_execution_id_ai_agent_executions"),
        "marketing_action_proposals",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_marketing_action_proposals_approval_id_approval_requests"),
        "marketing_action_proposals",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_marketing_action_proposals_execution_business",
        "marketing_action_proposals",
        "ai_agent_executions",
        ["execution_id", "business_id"],
        ["id", "business_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_marketing_action_proposals_approval_business",
        "marketing_action_proposals",
        "approval_requests",
        ["approval_id", "business_id"],
        ["id", "business_id"],
    )

    op.execute(
        "DELETE FROM background_jobs AS job WHERE ("
        " job.competitor_discovery_run_id IS NOT NULL AND NOT EXISTS ("
        " SELECT 1 FROM competitor_discovery_runs AS run"
        " WHERE run.id = job.competitor_discovery_run_id"
        " AND run.business_id = job.business_id)) OR ("
        " job.marketing_automation_run_id IS NOT NULL AND NOT EXISTS ("
        " SELECT 1 FROM marketing_automation_runs AS run"
        " WHERE run.id = job.marketing_automation_run_id"
        " AND run.business_id = job.business_id))"
    )
    op.drop_constraint(
        "fk_jobs_competitor_discovery_run", "background_jobs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_jobs_marketing_automation_run", "background_jobs", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_jobs_competitor_discovery_run_business",
        "background_jobs",
        "competitor_discovery_runs",
        ["competitor_discovery_run_id", "business_id"],
        ["id", "business_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_jobs_marketing_automation_run_business",
        "background_jobs",
        "marketing_automation_runs",
        ["marketing_automation_run_id", "business_id"],
        ["id", "business_id"],
        ondelete="CASCADE",
    )

    op.add_column(
        "chatbot_deployments",
        sa.Column("deployment_target_key", sa.String(128), nullable=True),
    )
    op.add_column(
        "chatbot_deployments",
        sa.Column("provider_resource_reference", sa.String(255), nullable=True),
    )
    op.execute(
        "UPDATE chatbot_deployments SET deployment_target_key = CASE "
        "WHEN target_type = 'hosted' THEN 'hosted' "
        "WHEN integration_connection_id IS NOT NULL "
        "THEN 'connection:' || integration_connection_id::text "
        "ELSE 'legacy:' || id::text END"
    )
    op.alter_column("chatbot_deployments", "deployment_target_key", nullable=False)
    op.drop_constraint(
        "uq_chatbot_deployments_business_target",
        "chatbot_deployments",
        type_="unique",
    )
    op.create_check_constraint(
        op.f("ck_chatbot_deployments_valid_deployment_target_key"),
        "chatbot_deployments",
        "char_length(btrim(deployment_target_key)) BETWEEN 1 AND 128",
    )
    op.create_check_constraint(
        op.f("ck_chatbot_deployments_valid_provider_resource_reference"),
        "chatbot_deployments",
        "provider_resource_reference IS NULL OR "
        "char_length(btrim(provider_resource_reference)) BETWEEN 1 AND 255",
    )
    op.create_check_constraint(
        op.f("ck_chatbot_deployments_hosted_target_key"),
        "chatbot_deployments",
        "target_type <> 'hosted' OR deployment_target_key = 'hosted'",
    )
    op.create_unique_constraint(
        "uq_chatbot_deployments_business_target_key",
        "chatbot_deployments",
        ["business_id", "target_type", "deployment_target_key"],
    )
    op.create_unique_constraint(
        "uq_chatbot_deployments_business_provider_resource",
        "chatbot_deployments",
        ["business_id", "target_type", "provider_resource_reference"],
    )

    op.add_column("marketing_content", sa.Column("generation_reasoning", sa.Text(), nullable=True))

    op.create_table(
        "advertising_spend_policies",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("max_single_campaign_budget", sa.Numeric(14, 2), nullable=False),
        sa.Column("max_single_budget_change", sa.Numeric(14, 2), nullable=False),
        sa.Column("daily_advertising_limit", sa.Numeric(14, 2), nullable=True),
        sa.Column("monthly_ai_managed_limit", sa.Numeric(14, 2), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("set_by_user_id", sa.Uuid(), nullable=True),
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
            "currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_advertising_spend_policies_valid_currency"),
        ),
        sa.CheckConstraint(
            "max_single_campaign_budget >= 0 AND "
            "max_single_campaign_budget <= 1000000000",
            name=op.f("ck_advertising_spend_policies_valid_campaign_budget_cap"),
        ),
        sa.CheckConstraint(
            "max_single_budget_change >= 0 AND "
            "max_single_budget_change <= 1000000000",
            name=op.f("ck_advertising_spend_policies_valid_budget_change_cap"),
        ),
        sa.CheckConstraint(
            "daily_advertising_limit IS NULL OR "
            "(daily_advertising_limit >= 0 AND daily_advertising_limit <= 1000000000)",
            name=op.f("ck_advertising_spend_policies_valid_daily_advertising_limit"),
        ),
        sa.CheckConstraint(
            "monthly_ai_managed_limit IS NULL OR "
            "(monthly_ai_managed_limit >= 0 AND monthly_ai_managed_limit <= 1000000000)",
            name=op.f("ck_advertising_spend_policies_valid_monthly_ai_managed_limit"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_advertising_spend_policies_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["set_by_user_id"],
            ["users.id"],
            name=op.f("fk_advertising_spend_policies_set_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_advertising_spend_policies")),
        sa.UniqueConstraint(
            "id", "business_id", name="uq_ad_spend_policies_id_business"
        ),
        sa.UniqueConstraint("business_id", name="uq_ad_spend_policies_business"),
    )
    op.create_index(
        "ix_ad_spend_policies_business_active",
        "advertising_spend_policies",
        ["business_id", "active", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ad_spend_policies_business_active",
        table_name="advertising_spend_policies",
    )
    op.drop_table("advertising_spend_policies")

    op.drop_column("marketing_content", "generation_reasoning")

    op.drop_constraint(
        "uq_chatbot_deployments_business_provider_resource",
        "chatbot_deployments",
        type_="unique",
    )
    op.drop_constraint(
        "uq_chatbot_deployments_business_target_key",
        "chatbot_deployments",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_chatbot_deployments_hosted_target_key"),
        "chatbot_deployments",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_chatbot_deployments_valid_provider_resource_reference"),
        "chatbot_deployments",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_chatbot_deployments_valid_deployment_target_key"),
        "chatbot_deployments",
        type_="check",
    )
    # The old schema permits only one deployment per business and target.
    op.execute(
        "WITH duplicates AS ("
        " SELECT id, row_number() OVER (PARTITION BY business_id, target_type "
        " ORDER BY CASE WHEN deployment_target_key = 'hosted' THEN 0 ELSE 1 END,"
        " created_at, id) AS position FROM chatbot_deployments"
        ") DELETE FROM chatbot_deployments AS deployment USING duplicates "
        "WHERE deployment.id = duplicates.id AND duplicates.position > 1"
    )
    op.create_unique_constraint(
        "uq_chatbot_deployments_business_target",
        "chatbot_deployments",
        ["business_id", "target_type"],
    )
    op.drop_column("chatbot_deployments", "provider_resource_reference")
    op.drop_column("chatbot_deployments", "deployment_target_key")

    op.drop_constraint(
        "fk_jobs_marketing_automation_run_business",
        "background_jobs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_jobs_competitor_discovery_run_business",
        "background_jobs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_jobs_marketing_automation_run",
        "background_jobs",
        "marketing_automation_runs",
        ["marketing_automation_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_jobs_competitor_discovery_run",
        "background_jobs",
        "competitor_discovery_runs",
        ["competitor_discovery_run_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "fk_marketing_action_proposals_approval_business",
        "marketing_action_proposals",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_marketing_action_proposals_execution_business",
        "marketing_action_proposals",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_marketing_action_proposals_approval_id_approval_requests"),
        "marketing_action_proposals",
        "approval_requests",
        ["approval_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_marketing_action_proposals_execution_id_ai_agent_executions"),
        "marketing_action_proposals",
        "ai_agent_executions",
        ["execution_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "fk_mkt_competitors_source_candidate_business",
        "marketing_competitors",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_mkt_competitors_source_candidate",
        "marketing_competitors",
        "competitor_candidates",
        ["source_candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint(
        "uq_marketing_campaigns_business_audience_hypothesis",
        "marketing_campaigns",
        type_="unique",
    )
    op.drop_constraint(
        "fk_mkt_campaigns_audience_hypothesis_business",
        "marketing_campaigns",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_mkt_campaigns_audience_hypothesis",
        "marketing_campaigns",
        "audience_hypotheses",
        ["audience_hypothesis_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        op.f("ck_audience_hypotheses_valid_age_range"),
        "audience_hypotheses",
        type_="check",
    )
    op.drop_column("audience_hypotheses", "max_age")
    op.drop_column("audience_hypotheses", "min_age")
    op.add_column(
        "audience_hypotheses", sa.Column("campaign_id", sa.Uuid(), nullable=True)
    )
    op.execute(
        "UPDATE audience_hypotheses AS audience SET campaign_id = campaign.id "
        "FROM marketing_campaigns AS campaign "
        "WHERE campaign.audience_hypothesis_id = audience.id "
        "AND campaign.business_id = audience.business_id"
    )
    op.create_foreign_key(
        "fk_audience_hypotheses_campaign_business",
        "audience_hypotheses",
        "marketing_campaigns",
        ["campaign_id", "business_id"],
        ["id", "business_id"],
    )

    op.drop_constraint(
        "fk_competitor_candidate_evidence_run_business",
        "competitor_candidate_evidence",
        type_="foreignkey",
    )
    op.execute(
        "WITH duplicates AS ("
        " SELECT id, row_number() OVER (PARTITION BY candidate_id, fingerprint "
        " ORDER BY observed_at DESC, created_at DESC, id DESC) AS position"
        " FROM competitor_candidate_evidence"
        ") DELETE FROM competitor_candidate_evidence AS evidence USING duplicates "
        "WHERE evidence.id = duplicates.id AND duplicates.position > 1"
    )
    op.drop_constraint(
        "uq_competitor_evidence_candidate_run_fingerprint",
        "competitor_candidate_evidence",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_competitor_candidate_evidence_candidate_fingerprint",
        "competitor_candidate_evidence",
        ["candidate_id", "fingerprint"],
    )
    op.drop_column("competitor_candidate_evidence", "discovery_run_id")

    for name in reversed(
        (
            "results_processed",
            "new_candidates",
            "refreshed_candidates",
            "evidence_added",
        )
    ):
        op.drop_constraint(
            op.f(f"ck_competitor_discovery_runs_valid_{name}"),
            "competitor_discovery_runs",
            type_="check",
        )
        op.drop_column("competitor_discovery_runs", name)

    op.drop_constraint(
        op.f("ck_ai_actions_valid_authorized_payload_hash"),
        "ai_actions",
        type_="check",
    )
    op.drop_column("ai_actions", "authorized_payload_hash")
    op.drop_constraint(
        "fk_ai_actions_execution_business", "ai_actions", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_ai_actions_execution_id_ai_agent_executions"),
        "ai_actions",
        "ai_agent_executions",
        ["execution_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_approval_requests_id_business", "approval_requests", type_="unique"
    )
    op.drop_constraint(
        "uq_ai_agent_executions_id_business",
        "ai_agent_executions",
        type_="unique",
    )
