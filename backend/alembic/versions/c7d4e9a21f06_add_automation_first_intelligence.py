"""add automation-first intelligence and deployment domains

Revision ID: c7d4e9a21f06
Revises: b8e1f4a7c962
Create Date: 2026-08-24 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c7d4e9a21f06"
down_revision: str | None = "b8e1f4a7c962"
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
        "competitor_discovery_runs",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_type", sa.String(24), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=True),
        sa.Column("brain_revision", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), server_default="queued", nullable=False),
        sa.Column("candidate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("trigger_type IN ('onboarding','brain_change','scheduled','manual_refresh')", name=op.f("ck_competitor_discovery_runs_valid_trigger_type")),
        sa.CheckConstraint("status IN ('queued','running','completed','provider_unavailable','blocked_entitlement','failed')", name=op.f("ck_competitor_discovery_runs_valid_status")),
        sa.CheckConstraint("candidate_count BETWEEN 0 AND 1000", name=op.f("ck_competitor_discovery_runs_valid_candidate_count")),
        sa.CheckConstraint("brain_revision IS NULL OR (char_length(brain_revision) = 64 AND brain_revision ~ '^[0-9a-f]{64}$')", name=op.f("ck_competitor_discovery_runs_valid_brain_revision")),
        sa.CheckConstraint("char_length(btrim(idempotency_key)) BETWEEN 1 AND 200", name=op.f("ck_competitor_discovery_runs_valid_idempotency_key")),
        sa.CheckConstraint("failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$'", name=op.f("ck_competitor_discovery_runs_valid_failure_code")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_competitor_discovery_runs_business_id_businesses")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitor_discovery_runs")),
        sa.UniqueConstraint("id", "business_id", name="uq_competitor_discovery_runs_id_business"),
        sa.UniqueConstraint("business_id", "idempotency_key", name="uq_competitor_discovery_runs_business_idempotency"),
    )
    op.create_index("ix_competitor_discovery_runs_business_created", "competitor_discovery_runs", ["business_id", "created_at", "id"])
    op.create_index("ix_competitor_discovery_runs_status_created", "competitor_discovery_runs", ["status", "created_at", "id"])

    op.create_table(
        "competitor_candidates",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("discovery_run_id", sa.Uuid(), nullable=False),
        sa.Column("competitor_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("website_domain", sa.String(253), nullable=True),
        sa.Column("canonical_url", sa.String(2048), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("discovery_reason", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("industry_relationship", sa.String(500), nullable=True),
        sa.Column("geographic_relationship", sa.String(500), nullable=True),
        sa.Column("status", sa.String(24), server_default="suggested", nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 180", name=op.f("ck_competitor_candidates_valid_name")),
        sa.CheckConstraint("website_domain IS NULL OR website_domain ~ '^[A-Za-z0-9.-]{1,253}$'", name=op.f("ck_competitor_candidates_valid_website_domain")),
        sa.CheckConstraint("canonical_url IS NULL OR char_length(canonical_url) <= 2048", name=op.f("ck_competitor_candidates_valid_canonical_url")),
        sa.CheckConstraint("char_length(discovery_reason) BETWEEN 1 AND 3000", name=op.f("ck_competitor_candidates_valid_discovery_reason")),
        sa.CheckConstraint("confidence BETWEEN 0.000 AND 1.000", name=op.f("ck_competitor_candidates_valid_confidence")),
        sa.CheckConstraint("status IN ('suggested','confirmed','dismissed','monitoring')", name=op.f("ck_competitor_candidates_valid_status")),
        sa.CheckConstraint("char_length(fingerprint) = 64", name=op.f("ck_competitor_candidates_valid_fingerprint")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_competitor_candidates_business_id_businesses")),
        sa.ForeignKeyConstraint(["discovery_run_id", "business_id"], ["competitor_discovery_runs.id", "competitor_discovery_runs.business_id"], name="fk_competitor_candidates_run_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["competitor_id", "business_id"], ["marketing_competitors.id", "marketing_competitors.business_id"], name="fk_competitor_candidates_competitor_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitor_candidates")),
        sa.UniqueConstraint("id", "business_id", name="uq_competitor_candidates_id_business"),
        sa.UniqueConstraint("business_id", "fingerprint", name="uq_competitor_candidates_business_fingerprint"),
    )
    op.create_index("ix_competitor_candidates_business_status_confidence", "competitor_candidates", ["business_id", "status", "confidence", "id"])
    op.create_index("ix_competitor_candidates_run", "competitor_candidates", ["business_id", "discovery_run_id", "id"])

    op.create_table(
        "competitor_candidate_evidence",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_reference", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("safe_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("source_type IN ('provider_result','public_url','public_metadata','ai_inference')", name=op.f("ck_competitor_candidate_evidence_valid_source_type")),
        sa.CheckConstraint("char_length(btrim(source_reference)) BETWEEN 1 AND 2048", name=op.f("ck_competitor_candidate_evidence_valid_source_reference")),
        sa.CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 300", name=op.f("ck_competitor_candidate_evidence_valid_title")),
        sa.CheckConstraint("char_length(excerpt) BETWEEN 1 AND 4000", name=op.f("ck_competitor_candidate_evidence_valid_excerpt")),
        sa.CheckConstraint("char_length(fingerprint) = 64", name=op.f("ck_competitor_candidate_evidence_valid_fingerprint")),
        sa.CheckConstraint("jsonb_typeof(safe_metadata) = 'object'", name=op.f("ck_competitor_candidate_evidence_valid_safe_metadata")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_competitor_candidate_evidence_business_id_businesses")),
        sa.ForeignKeyConstraint(["candidate_id", "business_id"], ["competitor_candidates.id", "competitor_candidates.business_id"], name="fk_competitor_candidate_evidence_candidate_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitor_candidate_evidence")),
        sa.UniqueConstraint("id", "business_id", name="uq_competitor_candidate_evidence_id_business"),
        sa.UniqueConstraint("candidate_id", "fingerprint", name="uq_competitor_candidate_evidence_candidate_fingerprint"),
    )
    op.create_index("ix_competitor_candidate_evidence_business_candidate", "competitor_candidate_evidence", ["business_id", "candidate_id", "observed_at", "id"])

    op.create_table(
        "audience_hypotheses",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("segments", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("geographic_areas", postgresql.ARRAY(sa.String(160)), server_default="{}", nullable=False),
        sa.Column("interests", postgresql.ARRAY(sa.String(160)), server_default="{}", nullable=False),
        sa.Column("intent_signals", postgresql.ARRAY(sa.String(300)), server_default="{}", nullable=False),
        sa.Column("buyer_personas", postgresql.ARRAY(sa.String(500)), server_default="{}", nullable=False),
        sa.Column("likely_pain_points", postgresql.ARRAY(sa.String(500)), server_default="{}", nullable=False),
        sa.Column("preferred_channels", postgresql.ARRAY(sa.String(24)), server_default="{}", nullable=False),
        sa.Column("excluded_audiences", postgresql.ARRAY(sa.String(300)), server_default="{}", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("classification IN ('first_party_observed','platform_supplied','public_competitor_observation','ai_inference')", name=op.f("ck_audience_hypotheses_valid_classification")),
        sa.CheckConstraint("confidence BETWEEN 0.000 AND 1.000", name=op.f("ck_audience_hypotheses_valid_confidence")),
        sa.CheckConstraint("char_length(summary) BETWEEN 1 AND 5000", name=op.f("ck_audience_hypotheses_valid_summary")),
        sa.CheckConstraint("jsonb_typeof(evidence) = 'array'", name=op.f("ck_audience_hypotheses_valid_evidence")),
        sa.CheckConstraint("jsonb_typeof(segments) = 'array'", name=op.f("ck_audience_hypotheses_valid_segments")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_audience_hypotheses_business_id_businesses")),
        sa.ForeignKeyConstraint(["campaign_id", "business_id"], ["marketing_campaigns.id", "marketing_campaigns.business_id"], name="fk_audience_hypotheses_campaign_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audience_hypotheses")),
        sa.UniqueConstraint("id", "business_id", name="uq_audience_hypotheses_id_business"),
    )
    op.create_index("ix_audience_hypotheses_business_created", "audience_hypotheses", ["business_id", "created_at", "id"])

    op.create_table(
        "marketing_automation_runs",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("run_type", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), server_default="queued", nullable=False),
        sa.Column("proposal_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("run_type IN ('content_plan','campaign_opportunities','business_growth')", name=op.f("ck_marketing_automation_runs_valid_run_type")),
        sa.CheckConstraint("status IN ('queued','running','completed','provider_unavailable','blocked_entitlement','failed')", name=op.f("ck_marketing_automation_runs_valid_status")),
        sa.CheckConstraint("window_end >= window_start", name=op.f("ck_marketing_automation_runs_valid_window")),
        sa.CheckConstraint("proposal_count BETWEEN 0 AND 100", name=op.f("ck_marketing_automation_runs_valid_proposal_count")),
        sa.CheckConstraint("char_length(btrim(idempotency_key)) BETWEEN 1 AND 200", name=op.f("ck_marketing_automation_runs_valid_idempotency_key")),
        sa.CheckConstraint("failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$'", name=op.f("ck_marketing_automation_runs_valid_failure_code")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_marketing_automation_runs_business_id_businesses")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_automation_runs")),
        sa.UniqueConstraint("id", "business_id", name="uq_marketing_automation_runs_id_business"),
        sa.UniqueConstraint("business_id", "idempotency_key", name="uq_marketing_automation_runs_business_idempotency"),
    )
    op.create_index("ix_marketing_automation_runs_business_type_created", "marketing_automation_runs", ["business_id", "run_type", "created_at", "id"])

    op.create_table(
        "marketing_action_proposals",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(24), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("connector_type", sa.String(48), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("ai_action_id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("entity_type IN ('campaign','content')", name=op.f("ck_marketing_action_proposals_valid_entity_type")),
        sa.CheckConstraint("char_length(btrim(channel)) BETWEEN 1 AND 24", name=op.f("ck_marketing_action_proposals_valid_channel")),
        sa.CheckConstraint("char_length(btrim(action_type)) BETWEEN 1 AND 100", name=op.f("ck_marketing_action_proposals_valid_action_type")),
        sa.CheckConstraint("char_length(btrim(connector_type)) BETWEEN 1 AND 48", name=op.f("ck_marketing_action_proposals_valid_connector_type")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_marketing_action_proposals_business_id_businesses")),
        sa.ForeignKeyConstraint(["execution_id"], ["ai_agent_executions.id"], ondelete="CASCADE", name=op.f("fk_marketing_action_proposals_execution_id_ai_agent_executions")),
        sa.ForeignKeyConstraint(["ai_action_id", "business_id"], ["ai_actions.id", "ai_actions.business_id"], name="fk_marketing_action_proposals_action_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approval_id"], ["approval_requests.id"], ondelete="SET NULL", name=op.f("fk_marketing_action_proposals_approval_id_approval_requests")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_action_proposals")),
        sa.UniqueConstraint("id", "business_id", name="uq_marketing_action_proposals_id_business"),
        sa.UniqueConstraint("business_id", "entity_type", "entity_id", "channel", "action_type", name="uq_marketing_action_proposals_entity_action"),
    )
    op.create_index("ix_marketing_action_proposals_business_entity", "marketing_action_proposals", ["business_id", "entity_type", "entity_id", "created_at", "id"])

    op.create_table(
        "chatbot_deployments",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("chatbot_config_id", sa.Uuid(), nullable=False),
        sa.Column("integration_connection_id", sa.Uuid(), nullable=True),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=True),
        sa.Column("public_path", sa.String(2048), nullable=True),
        sa.Column("verification_status", sa.String(24), server_default="not_checked", nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("target_type IN ('hosted','shopify','wordpress','wix','webflow','squarespace','google_tag_manager','other','manual_embed')", name=op.f("ck_chatbot_deployments_valid_target_type")),
        sa.CheckConstraint("state IN ('available','connection_required','connected','installation_supported','installed','needs_manual_step','unsupported')", name=op.f("ck_chatbot_deployments_valid_state")),
        sa.CheckConstraint("verification_status IN ('not_checked','healthy','failed')", name=op.f("ck_chatbot_deployments_valid_verification_status")),
        sa.CheckConstraint("public_path IS NULL OR char_length(public_path) <= 2048", name=op.f("ck_chatbot_deployments_valid_public_path")),
        sa.CheckConstraint("failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$'", name=op.f("ck_chatbot_deployments_valid_failure_code")),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE", name=op.f("fk_chatbot_deployments_business_id_businesses")),
        sa.ForeignKeyConstraint(["chatbot_config_id", "business_id"], ["chatbot_configs.id", "chatbot_configs.business_id"], name="fk_chatbot_deployments_config_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["integration_connection_id", "business_id"], ["integration_connections.id", "integration_connections.business_id"], name="fk_chatbot_deployments_connection_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chatbot_deployments")),
        sa.UniqueConstraint("id", "business_id", name="uq_chatbot_deployments_id_business"),
        sa.UniqueConstraint("business_id", "target_type", name="uq_chatbot_deployments_business_target"),
    )
    op.create_index("ix_chatbot_deployments_business_state", "chatbot_deployments", ["business_id", "state", "target_type", "id"])

    op.add_column("marketing_competitors", sa.Column("source_candidate_id", sa.Uuid(), nullable=True))
    op.add_column("marketing_competitors", sa.Column("confirmation_source", sa.String(24), server_default="manual", nullable=False))
    op.create_foreign_key("fk_mkt_competitors_source_candidate", "marketing_competitors", "competitor_candidates", ["source_candidate_id"], ["id"], ondelete="SET NULL")

    campaign_columns = (
        sa.Column("origin_type", sa.String(24), server_default="manual", nullable=False),
        sa.Column("proposal_key", sa.String(200), nullable=True),
        sa.Column("proposal_reasoning", sa.Text(), nullable=True),
        sa.Column("creative_brief", sa.Text(), nullable=True),
        sa.Column("proposed_copy", sa.Text(), nullable=True),
        sa.Column("proposed_cta", sa.String(300), nullable=True),
        sa.Column("landing_destination", sa.String(2048), nullable=True),
        sa.Column("measurement_plan", sa.Text(), nullable=True),
        sa.Column("assumptions", postgresql.ARRAY(sa.String(500)), server_default="{}", nullable=False),
        sa.Column("risks", postgresql.ARRAY(sa.String(500)), server_default="{}", nullable=False),
        sa.Column("required_integrations", postgresql.ARRAY(sa.String(64)), server_default="{}", nullable=False),
        sa.Column("source_evidence", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("audience_hypothesis_id", sa.Uuid(), nullable=True),
    )
    for column in campaign_columns:
        op.add_column("marketing_campaigns", column)
    op.create_unique_constraint("uq_marketing_campaigns_business_proposal_key", "marketing_campaigns", ["business_id", "proposal_key"])
    op.create_foreign_key("fk_mkt_campaigns_audience_hypothesis", "marketing_campaigns", "audience_hypotheses", ["audience_hypothesis_id"], ["id"], ondelete="SET NULL")

    for column in (
        sa.Column("proposal_key", sa.String(200), nullable=True),
        sa.Column("creative_brief", sa.Text(), nullable=True),
        sa.Column("recommended_for", sa.String(500), nullable=True),
        sa.Column("source_evidence", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    ):
        op.add_column("marketing_content", column)
    op.create_unique_constraint("uq_marketing_content_business_proposal_key", "marketing_content", ["business_id", "proposal_key"])

    for column in (
        sa.Column("source_entity_type", sa.String(48), nullable=True),
        sa.Column("source_entity_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("suggested_action", sa.String(64), nullable=True),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=True),
    ):
        op.add_column("opportunities", column)
    op.create_unique_constraint("uq_opportunities_business_dedupe_key", "opportunities", ["business_id", "dedupe_key"])

    op.add_column("background_jobs", sa.Column("competitor_discovery_run_id", sa.Uuid(), nullable=True))
    op.add_column("background_jobs", sa.Column("marketing_automation_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_jobs_competitor_discovery_run", "background_jobs", "competitor_discovery_runs", ["competitor_discovery_run_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_jobs_marketing_automation_run", "background_jobs", "marketing_automation_runs", ["marketing_automation_run_id"], ["id"], ondelete="CASCADE")
    op.drop_constraint(op.f("ck_background_jobs_valid_job_type"), "background_jobs", type_="check")
    op.create_check_constraint(op.f("ck_background_jobs_valid_job_type"), "background_jobs", "job_type IN ('process_automation_event','resume_workflow_run','process_scheduled_workflow','process_integration_event','reconcile_uncertain_attempt','mark_social_schedule_ready','maintain_subscription','discover_competitors','generate_content_plan','analyze_campaign_opportunities')")
    op.drop_constraint(op.f("ck_background_jobs_valid_failure_code"), "background_jobs", type_="check")
    op.create_check_constraint(op.f("ck_background_jobs_valid_failure_code"), "background_jobs", "failure_code IS NULL OR failure_code IN ('dependency_unavailable','external_execution_disabled','invalid_job_state','resource_not_found','retry_exhausted','uncertain_external_outcome','workflow_execution_failed','workflow_invalid','provider_unavailable','feature_not_entitled')")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_background_jobs_valid_failure_code"), "background_jobs", type_="check")
    op.execute(
        "UPDATE background_jobs SET failure_code = 'dependency_unavailable' "
        "WHERE failure_code IN ('provider_unavailable','feature_not_entitled')"
    )
    op.create_check_constraint(op.f("ck_background_jobs_valid_failure_code"), "background_jobs", "failure_code IS NULL OR failure_code IN ('dependency_unavailable','external_execution_disabled','invalid_job_state','resource_not_found','retry_exhausted','uncertain_external_outcome','workflow_execution_failed','workflow_invalid')")
    op.drop_constraint(op.f("ck_background_jobs_valid_job_type"), "background_jobs", type_="check")
    op.execute(
        "DELETE FROM background_jobs WHERE job_type IN "
        "('discover_competitors','generate_content_plan','analyze_campaign_opportunities')"
    )
    op.create_check_constraint(op.f("ck_background_jobs_valid_job_type"), "background_jobs", "job_type IN ('process_automation_event','resume_workflow_run','process_scheduled_workflow','process_integration_event','reconcile_uncertain_attempt','mark_social_schedule_ready','maintain_subscription')")
    op.drop_constraint("fk_jobs_marketing_automation_run", "background_jobs", type_="foreignkey")
    op.drop_constraint("fk_jobs_competitor_discovery_run", "background_jobs", type_="foreignkey")
    op.drop_column("background_jobs", "marketing_automation_run_id")
    op.drop_column("background_jobs", "competitor_discovery_run_id")

    op.drop_constraint("uq_opportunities_business_dedupe_key", "opportunities", type_="unique")
    for name in ("dedupe_key", "provenance", "suggested_action", "recommendation", "confidence", "reason", "source_entity_id", "source_entity_type"):
        op.drop_column("opportunities", name)

    op.drop_constraint("uq_marketing_content_business_proposal_key", "marketing_content", type_="unique")
    for name in ("source_evidence", "recommended_for", "creative_brief", "proposal_key"):
        op.drop_column("marketing_content", name)

    op.drop_constraint("fk_mkt_campaigns_audience_hypothesis", "marketing_campaigns", type_="foreignkey")
    op.drop_constraint("uq_marketing_campaigns_business_proposal_key", "marketing_campaigns", type_="unique")
    for name in ("audience_hypothesis_id", "source_evidence", "required_integrations", "risks", "assumptions", "measurement_plan", "landing_destination", "proposed_cta", "proposed_copy", "creative_brief", "proposal_reasoning", "proposal_key", "origin_type"):
        op.drop_column("marketing_campaigns", name)

    op.drop_constraint("fk_mkt_competitors_source_candidate", "marketing_competitors", type_="foreignkey")
    op.drop_column("marketing_competitors", "confirmation_source")
    op.drop_column("marketing_competitors", "source_candidate_id")

    op.drop_table("chatbot_deployments")
    op.drop_table("marketing_action_proposals")
    op.drop_table("marketing_automation_runs")
    op.drop_table("audience_hypotheses")
    op.drop_table("competitor_candidate_evidence")
    op.drop_table("competitor_candidates")
    op.drop_table("competitor_discovery_runs")
