from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CompetitorDiscoveryRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "competitor_discovery_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('onboarding','brain_change','scheduled','manual_refresh')",
            name="valid_trigger_type",
        ),
        CheckConstraint(
            "status IN ('queued','running','completed','provider_unavailable',"
            "'blocked_entitlement','failed')",
            name="valid_status",
        ),
        CheckConstraint("candidate_count BETWEEN 0 AND 1000", name="valid_candidate_count"),
        CheckConstraint("results_processed BETWEEN 0 AND 1000", name="valid_results_processed"),
        CheckConstraint("new_candidates BETWEEN 0 AND 1000", name="valid_new_candidates"),
        CheckConstraint("refreshed_candidates BETWEEN 0 AND 1000", name="valid_refreshed_candidates"),
        CheckConstraint("evidence_added BETWEEN 0 AND 20000", name="valid_evidence_added"),
        CheckConstraint(
            "brain_revision IS NULL OR (char_length(brain_revision) = 64 "
            "AND brain_revision ~ '^[0-9a-f]{64}$')",
            name="valid_brain_revision",
        ),
        CheckConstraint(
            "char_length(btrim(idempotency_key)) BETWEEN 1 AND 200",
            name="valid_idempotency_key",
        ),
        CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="valid_failure_code",
        ),
        UniqueConstraint("id", "business_id", name="uq_competitor_discovery_runs_id_business"),
        UniqueConstraint(
            "business_id", "idempotency_key",
            name="uq_competitor_discovery_runs_business_idempotency",
        ),
        Index(
            "ix_competitor_discovery_runs_business_created",
            "business_id", "created_at", "id",
        ),
        Index(
            "ix_competitor_discovery_runs_status_created",
            "status", "created_at", "id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    trigger_type: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    brain_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    candidate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    results_processed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    new_candidates: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    refreshed_candidates: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    evidence_added: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CompetitorCandidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "competitor_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["discovery_run_id", "business_id"],
            ["competitor_discovery_runs.id", "competitor_discovery_runs.business_id"],
            name="fk_competitor_candidates_run_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["competitor_id", "business_id"],
            ["marketing_competitors.id", "marketing_competitors.business_id"],
            name="fk_competitor_candidates_competitor_business",
        ),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 180", name="valid_name"),
        CheckConstraint(
            "website_domain IS NULL OR website_domain ~ '^[A-Za-z0-9.-]{1,253}$'",
            name="valid_website_domain",
        ),
        CheckConstraint(
            "canonical_url IS NULL OR char_length(canonical_url) <= 2048",
            name="valid_canonical_url",
        ),
        CheckConstraint(
            "char_length(discovery_reason) BETWEEN 1 AND 3000",
            name="valid_discovery_reason",
        ),
        CheckConstraint("confidence BETWEEN 0.000 AND 1.000", name="valid_confidence"),
        CheckConstraint(
            "status IN ('suggested','confirmed','dismissed','monitoring')",
            name="valid_status",
        ),
        CheckConstraint("char_length(fingerprint) = 64", name="valid_fingerprint"),
        UniqueConstraint("id", "business_id", name="uq_competitor_candidates_id_business"),
        UniqueConstraint(
            "business_id", "fingerprint",
            name="uq_competitor_candidates_business_fingerprint",
        ),
        Index(
            "ix_competitor_candidates_business_status_confidence",
            "business_id", "status", "confidence", "id",
        ),
        Index(
            "ix_competitor_candidates_run",
            "business_id", "discovery_run_id", "id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    discovery_run_id: Mapped[UUID] = mapped_column(nullable=False)
    competitor_id: Mapped[UUID | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    website_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    discovery_reason: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    industry_relationship: Mapped[str | None] = mapped_column(String(500), nullable=True)
    geographic_relationship: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="suggested", server_default="suggested"
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompetitorCandidateEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "competitor_candidate_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["candidate_id", "business_id"],
            ["competitor_candidates.id", "competitor_candidates.business_id"],
            name="fk_competitor_candidate_evidence_candidate_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["discovery_run_id", "business_id"],
            ["competitor_discovery_runs.id", "competitor_discovery_runs.business_id"],
            name="fk_competitor_candidate_evidence_run_business",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "source_type IN ('provider_result','public_url','public_metadata','ai_inference')",
            name="valid_source_type",
        ),
        CheckConstraint(
            "char_length(btrim(source_reference)) BETWEEN 1 AND 2048",
            name="valid_source_reference",
        ),
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 300", name="valid_title"),
        CheckConstraint("char_length(excerpt) BETWEEN 1 AND 4000", name="valid_excerpt"),
        CheckConstraint("char_length(fingerprint) = 64", name="valid_fingerprint"),
        CheckConstraint("jsonb_typeof(safe_metadata) = 'object'", name="valid_safe_metadata"),
        UniqueConstraint("id", "business_id", name="uq_competitor_candidate_evidence_id_business"),
        UniqueConstraint(
            "candidate_id", "discovery_run_id", "fingerprint",
            name="uq_competitor_evidence_candidate_run_fingerprint",
        ),
        Index(
            "ix_competitor_candidate_evidence_business_candidate",
            "business_id", "candidate_id", "observed_at", "id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[UUID] = mapped_column(nullable=False)
    discovery_run_id: Mapped[UUID] = mapped_column(nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    safe_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class AudienceHypothesis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audience_hypotheses"
    __table_args__ = (
        CheckConstraint(
            "classification IN ('first_party_observed','platform_supplied',"
            "'public_competitor_observation','ai_inference')",
            name="valid_classification",
        ),
        CheckConstraint("confidence BETWEEN 0.000 AND 1.000", name="valid_confidence"),
        CheckConstraint("char_length(summary) BETWEEN 1 AND 5000", name="valid_summary"),
        CheckConstraint("jsonb_typeof(evidence) = 'array'", name="valid_evidence"),
        CheckConstraint("jsonb_typeof(segments) = 'array'", name="valid_segments"),
        CheckConstraint(
            "(min_age IS NULL AND max_age IS NULL) OR "
            "(min_age BETWEEN 18 AND 100 AND max_age BETWEEN min_age AND 100)",
            name="valid_age_range",
        ),
        UniqueConstraint("id", "business_id", name="uq_audience_hypotheses_id_business"),
        Index(
            "ix_audience_hypotheses_business_created",
            "business_id", "created_at", "id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    evidence: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    segments: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    geographic_areas: Mapped[list[str]] = mapped_column(
        ARRAY(String(160)), nullable=False, default=list, server_default="{}"
    )
    interests: Mapped[list[str]] = mapped_column(
        ARRAY(String(160)), nullable=False, default=list, server_default="{}"
    )
    intent_signals: Mapped[list[str]] = mapped_column(
        ARRAY(String(300)), nullable=False, default=list, server_default="{}"
    )
    buyer_personas: Mapped[list[str]] = mapped_column(
        ARRAY(String(500)), nullable=False, default=list, server_default="{}"
    )
    likely_pain_points: Mapped[list[str]] = mapped_column(
        ARRAY(String(500)), nullable=False, default=list, server_default="{}"
    )
    preferred_channels: Mapped[list[str]] = mapped_column(
        ARRAY(String(24)), nullable=False, default=list, server_default="{}"
    )
    excluded_audiences: Mapped[list[str]] = mapped_column(
        ARRAY(String(300)), nullable=False, default=list, server_default="{}"
    )
    min_age: Mapped[int | None] = mapped_column(nullable=True)
    max_age: Mapped[int | None] = mapped_column(nullable=True)


class MarketingAutomationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketing_automation_runs"
    __table_args__ = (
        CheckConstraint(
            "run_type IN ('content_plan','campaign_opportunities','business_growth')",
            name="valid_run_type",
        ),
        CheckConstraint(
            "status IN ('queued','running','completed','provider_unavailable',"
            "'blocked_entitlement','failed')",
            name="valid_status",
        ),
        CheckConstraint("window_end >= window_start", name="valid_window"),
        CheckConstraint("proposal_count BETWEEN 0 AND 100", name="valid_proposal_count"),
        CheckConstraint(
            "char_length(btrim(idempotency_key)) BETWEEN 1 AND 200",
            name="valid_idempotency_key",
        ),
        CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="valid_failure_code",
        ),
        UniqueConstraint("id", "business_id", name="uq_marketing_automation_runs_id_business"),
        UniqueConstraint(
            "business_id", "idempotency_key",
            name="uq_marketing_automation_runs_business_idempotency",
        ),
        Index(
            "ix_marketing_automation_runs_business_type_created",
            "business_id", "run_type", "created_at", "id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    run_type: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    proposal_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketingActionProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Idempotent link from an internal marketing proposal to governed intent.

    The row proves that a campaign/content record was translated into the
    existing AIAction and approval systems. It is deliberately not an external
    execution record and must never contain connector credentials or results.
    """

    __tablename__ = "marketing_action_proposals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ai_action_id", "business_id"],
            ["ai_actions.id", "ai_actions.business_id"],
            name="fk_marketing_action_proposals_action_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["execution_id", "business_id"],
            ["ai_agent_executions.id", "ai_agent_executions.business_id"],
            name="fk_marketing_action_proposals_execution_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["approval_id", "business_id"],
            ["approval_requests.id", "approval_requests.business_id"],
            name="fk_marketing_action_proposals_approval_business",
        ),
        CheckConstraint(
            "entity_type IN ('campaign','content')",
            name="valid_entity_type",
        ),
        CheckConstraint(
            "char_length(btrim(channel)) BETWEEN 1 AND 24",
            name="valid_channel",
        ),
        CheckConstraint(
            "char_length(btrim(action_type)) BETWEEN 1 AND 100",
            name="valid_action_type",
        ),
        CheckConstraint(
            "char_length(btrim(connector_type)) BETWEEN 1 AND 48",
            name="valid_connector_type",
        ),
        UniqueConstraint(
            "id", "business_id",
            name="uq_marketing_action_proposals_id_business",
        ),
        UniqueConstraint(
            "business_id", "entity_type", "entity_id", "channel", "action_type",
            name="uq_marketing_action_proposals_entity_action",
        ),
        Index(
            "ix_marketing_action_proposals_business_entity",
            "business_id", "entity_type", "entity_id", "created_at", "id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(nullable=False)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(48), nullable=False)
    execution_id: Mapped[UUID] = mapped_column(nullable=False)
    ai_action_id: Mapped[UUID] = mapped_column(nullable=False)
    approval_id: Mapped[UUID | None] = mapped_column(nullable=True)


class ChatbotDeployment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chatbot_deployments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chatbot_config_id", "business_id"],
            ["chatbot_configs.id", "chatbot_configs.business_id"],
            name="fk_chatbot_deployments_config_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["integration_connection_id", "business_id"],
            ["integration_connections.id", "integration_connections.business_id"],
            name="fk_chatbot_deployments_connection_business",
        ),
        CheckConstraint(
            "target_type IN ('hosted','shopify','wordpress','wix','webflow',"
            "'squarespace','google_tag_manager','other','manual_embed')",
            name="valid_target_type",
        ),
        CheckConstraint(
            "state IN ('available','connection_required','connected',"
            "'installation_supported','installed','needs_manual_step','unsupported')",
            name="valid_state",
        ),
        CheckConstraint(
            "verification_status IN ('not_checked','healthy','failed')",
            name="valid_verification_status",
        ),
        CheckConstraint(
            "public_path IS NULL OR char_length(public_path) <= 2048",
            name="valid_public_path",
        ),
        CheckConstraint(
            "char_length(btrim(deployment_target_key)) BETWEEN 1 AND 128",
            name="valid_deployment_target_key",
        ),
        CheckConstraint(
            "provider_resource_reference IS NULL OR "
            "char_length(btrim(provider_resource_reference)) BETWEEN 1 AND 255",
            name="valid_provider_resource_reference",
        ),
        CheckConstraint(
            "target_type <> 'hosted' OR deployment_target_key = 'hosted'",
            name="hosted_target_key",
        ),
        CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="valid_failure_code",
        ),
        UniqueConstraint("id", "business_id", name="uq_chatbot_deployments_id_business"),
        UniqueConstraint(
            "business_id", "target_type", "deployment_target_key",
            name="uq_chatbot_deployments_business_target_key",
        ),
        UniqueConstraint(
            "business_id", "target_type", "provider_resource_reference",
            name="uq_chatbot_deployments_business_provider_resource",
        ),
        Index(
            "ix_chatbot_deployments_business_state",
            "business_id", "state", "target_type", "id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    chatbot_config_id: Mapped[UUID] = mapped_column(nullable=False)
    integration_connection_id: Mapped[UUID | None] = mapped_column(nullable=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    deployment_target_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_resource_reference: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    public_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="not_checked", server_default="not_checked"
    )
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AdvertisingSpendPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Server-owned limits evaluated before durable advertising execution."""

    __tablename__ = "advertising_spend_policies"
    __table_args__ = (
        UniqueConstraint("id", "business_id", name="uq_ad_spend_policies_id_business"),
        UniqueConstraint("business_id", name="uq_ad_spend_policies_business"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="valid_currency"),
        CheckConstraint(
            "max_single_campaign_budget >= 0 AND max_single_campaign_budget <= 1000000000",
            name="valid_campaign_budget_cap",
        ),
        CheckConstraint(
            "max_single_budget_change >= 0 AND max_single_budget_change <= 1000000000",
            name="valid_budget_change_cap",
        ),
        CheckConstraint(
            "daily_advertising_limit IS NULL OR "
            "(daily_advertising_limit >= 0 AND daily_advertising_limit <= 1000000000)",
            name="valid_daily_advertising_limit",
        ),
        CheckConstraint(
            "monthly_ai_managed_limit IS NULL OR "
            "(monthly_ai_managed_limit >= 0 AND monthly_ai_managed_limit <= 1000000000)",
            name="valid_monthly_ai_managed_limit",
        ),
        Index("ix_ad_spend_policies_business_active", "business_id", "active", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    max_single_campaign_budget: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    max_single_budget_change: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    daily_advertising_limit: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    monthly_ai_managed_limit: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    set_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
