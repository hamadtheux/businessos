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


CHANNELS = (
    "meta",
    "google_ads",
    "instagram",
    "facebook",
    "linkedin",
    "tiktok",
    "email",
    "whatsapp",
    "website",
    "other",
)
CHANNEL_SQL = "('meta','google_ads','instagram','facebook','linkedin','tiktok','email','whatsapp','website','other')"
CHANNEL_ARRAY_SQL = "ARRAY['meta','google_ads','instagram','facebook','linkedin','tiktok','email','whatsapp','website','other']::varchar[]"


class MarketingAudience(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketing_audiences"
    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 160", name="valid_name"),
        CheckConstraint("min_age BETWEEN 18 AND 100 AND max_age BETWEEN min_age AND 100", name="valid_age_range"),
        CheckConstraint("cardinality(countries) <= 25", name="valid_country_count"),
        CheckConstraint("cardinality(regions) <= 50", name="valid_region_count"),
        CheckConstraint("cardinality(languages) <= 20", name="valid_language_count"),
        CheckConstraint("cardinality(customer_lifecycle) <= 20", name="valid_lifecycle_count"),
        CheckConstraint("cardinality(crm_stages) <= 20", name="valid_crm_stage_count"),
        CheckConstraint("cardinality(interests) <= 50", name="valid_interest_count"),
        CheckConstraint("segment_description IS NULL OR char_length(segment_description) <= 2000", name="valid_description"),
        UniqueConstraint("id", "business_id", name="uq_marketing_audiences_id_business"),
        Index("ix_marketing_audiences_business_updated", "business_id", "updated_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    countries: Mapped[list[str]] = mapped_column(ARRAY(String(2)), nullable=False, default=list, server_default="{}")
    regions: Mapped[list[str]] = mapped_column(ARRAY(String(80)), nullable=False, default=list, server_default="{}")
    min_age: Mapped[int] = mapped_column(Integer, nullable=False, default=18, server_default="18")
    max_age: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    languages: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False, default=list, server_default="{}")
    customer_lifecycle: Mapped[list[str]] = mapped_column(ARRAY(String(24)), nullable=False, default=list, server_default="{}")
    crm_stages: Mapped[list[str]] = mapped_column(ARRAY(String(24)), nullable=False, default=list, server_default="{}")
    interests: Mapped[list[str]] = mapped_column(ARRAY(String(80)), nullable=False, default=list, server_default="{}")
    existing_customer_segment: Mapped[str | None] = mapped_column(String(160), nullable=True)
    segment_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class MarketingPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketing_plans"
    __table_args__ = (
        ForeignKeyConstraint(["audience_id", "business_id"], ["marketing_audiences.id", "marketing_audiences.business_id"], name="fk_marketing_plans_audience_business"),
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 180", name="valid_title"),
        CheckConstraint("char_length(objective) BETWEEN 1 AND 1000", name="valid_objective"),
        CheckConstraint("char_length(target_audience) BETWEEN 1 AND 2000", name="valid_target_audience"),
        CheckConstraint("char_length(positioning) BETWEEN 1 AND 3000", name="valid_positioning"),
        CheckConstraint("char_length(key_message) BETWEEN 1 AND 3000", name="valid_key_message"),
        CheckConstraint("offer IS NULL OR char_length(offer) <= 2000", name="valid_offer"),
        CheckConstraint("content_strategy IS NULL OR char_length(content_strategy) <= 5000", name="valid_content_strategy"),
        CheckConstraint(f"cardinality(channels) BETWEEN 1 AND 10 AND channels <@ {CHANNEL_ARRAY_SQL}", name="valid_channels"),
        CheckConstraint("cardinality(measurement_goals) <= 20", name="valid_measurement_goals"),
        CheckConstraint("budget_guidance IS NULL OR (budget_guidance >= 0 AND budget_guidance <= 1000000000)", name="valid_budget"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="valid_currency"),
        CheckConstraint("period_end IS NULL OR period_start IS NULL OR period_end >= period_start", name="valid_period"),
        CheckConstraint("status IN ('draft','ready','active','completed','archived')", name="valid_status"),
        CheckConstraint("generated_by IN ('user','ai')", name="valid_generated_by"),
        UniqueConstraint("id", "business_id", name="uq_marketing_plans_id_business"),
        Index("ix_marketing_plans_business_status_updated", "business_id", "status", "updated_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    audience_id: Mapped[UUID | None] = mapped_column(nullable=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    target_audience: Mapped[str] = mapped_column(Text, nullable=False)
    positioning: Mapped[str] = mapped_column(Text, nullable=False)
    key_message: Mapped[str] = mapped_column(Text, nullable=False)
    offer: Mapped[str | None] = mapped_column(Text, nullable=True)
    channels: Mapped[list[str]] = mapped_column(ARRAY(String(24)), nullable=False)
    budget_guidance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    content_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    measurement_goals: Mapped[list[str]] = mapped_column(ARRAY(String(160)), nullable=False, default=list, server_default="{}")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    generated_by: Mapped[str] = mapped_column(String(16), nullable=False, default="user", server_default="user")
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketing_campaigns"
    __table_args__ = (
        ForeignKeyConstraint(["marketing_plan_id", "business_id"], ["marketing_plans.id", "marketing_plans.business_id"], name="fk_marketing_campaigns_plan_business"),
        ForeignKeyConstraint(["audience_id", "business_id"], ["marketing_audiences.id", "marketing_audiences.business_id"], name="fk_marketing_campaigns_audience_business"),
        ForeignKeyConstraint(
            ["audience_hypothesis_id", "business_id"],
            ["audience_hypotheses.id", "audience_hypotheses.business_id"],
            name="fk_mkt_campaigns_audience_hypothesis_business",
        ),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 180", name="valid_name"),
        CheckConstraint("char_length(objective) BETWEEN 1 AND 1000", name="valid_objective"),
        CheckConstraint("description IS NULL OR char_length(description) <= 5000", name="valid_description"),
        CheckConstraint("offer IS NULL OR char_length(offer) <= 2000", name="valid_offer"),
        CheckConstraint("char_length(audience_definition) BETWEEN 1 AND 3000", name="valid_audience_definition"),
        CheckConstraint("cardinality(geographic_targeting) <= 50", name="valid_geographic_targeting"),
        CheckConstraint(f"cardinality(channels) BETWEEN 1 AND 10 AND channels <@ {CHANNEL_ARRAY_SQL}", name="valid_channels"),
        CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name="valid_period"),
        CheckConstraint("planned_budget >= 0 AND planned_budget <= 1000000000", name="valid_budget"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="valid_currency"),
        CheckConstraint("budget_mode IN ('daily','lifetime')", name="valid_budget_mode"),
        CheckConstraint("status IN ('draft','planned','awaiting_approval','approved','scheduled','active','paused','completed','canceled')", name="valid_status"),
        UniqueConstraint("id", "business_id", name="uq_marketing_campaigns_id_business"),
        Index("ix_marketing_campaigns_business_status_updated", "business_id", "status", "updated_at", "id"),
        Index("ix_marketing_campaigns_business_period", "business_id", "start_date", "end_date", "id"),
        UniqueConstraint("business_id", "proposal_key", name="uq_marketing_campaigns_business_proposal_key"),
        UniqueConstraint(
            "business_id", "audience_hypothesis_id",
            name="uq_marketing_campaigns_business_audience_hypothesis",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    marketing_plan_id: Mapped[UUID | None] = mapped_column(nullable=True)
    audience_id: Mapped[UUID | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    offer: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_definition: Mapped[str] = mapped_column(Text, nullable=False)
    geographic_targeting: Mapped[list[str]] = mapped_column(ARRAY(String(80)), nullable=False, default=list, server_default="{}")
    channels: Mapped[list[str]] = mapped_column(ARRAY(String(24)), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_budget: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    budget_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="lifetime", server_default="lifetime")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", server_default="draft")
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    origin_type: Mapped[str] = mapped_column(String(24), nullable=False, default="manual", server_default="manual")
    proposal_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    proposal_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    creative_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_copy: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_cta: Mapped[str | None] = mapped_column(String(300), nullable=True)
    landing_destination: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    measurement_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    assumptions: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False, default=list, server_default="{}")
    risks: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False, default=list, server_default="{}")
    required_integrations: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, default=list, server_default="{}")
    source_evidence: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    audience_hypothesis_id: Mapped[UUID | None] = mapped_column(nullable=True)


class CampaignChannelPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "campaign_channel_plans"
    __table_args__ = (
        ForeignKeyConstraint(["campaign_id", "business_id"], ["marketing_campaigns.id", "marketing_campaigns.business_id"], name="fk_campaign_channel_plans_campaign_business", ondelete="CASCADE"),
        CheckConstraint(f"channel IN {CHANNEL_SQL}", name="valid_channel"),
        CheckConstraint("char_length(objective) BETWEEN 1 AND 1000", name="valid_objective"),
        CheckConstraint("budget_allocation >= 0 AND budget_allocation <= 1000000000", name="valid_budget"),
        CheckConstraint("char_length(audience_strategy) BETWEEN 1 AND 3000", name="valid_audience_strategy"),
        CheckConstraint("char_length(messaging) BETWEEN 1 AND 5000", name="valid_messaging"),
        CheckConstraint("status IN ('draft','ready','approved','scheduled','active','completed','archived')", name="valid_status"),
        CheckConstraint("planned_end IS NULL OR planned_start IS NULL OR planned_end >= planned_start", name="valid_period"),
        CheckConstraint("jsonb_typeof(safe_configuration) = 'object'", name="valid_configuration"),
        UniqueConstraint("id", "business_id", name="uq_campaign_channel_plans_id_business"),
        UniqueConstraint("business_id", "campaign_id", "channel", name="uq_campaign_channel_plans_campaign_channel"),
        Index("ix_campaign_channel_plans_business_campaign", "business_id", "campaign_id", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[UUID] = mapped_column(nullable=False)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    budget_allocation: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0")
    audience_strategy: Mapped[str] = mapped_column(Text, nullable=False)
    messaging: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    safe_configuration: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))


class MarketingContent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketing_content"
    __table_args__ = (
        ForeignKeyConstraint(["campaign_id", "business_id"], ["marketing_campaigns.id", "marketing_campaigns.business_id"], name="fk_marketing_content_campaign_business"),
        ForeignKeyConstraint(["parent_content_id", "business_id"], ["marketing_content.id", "marketing_content.business_id"], name="fk_marketing_content_parent_business"),
        ForeignKeyConstraint(["root_content_id", "business_id"], ["marketing_content.id", "marketing_content.business_id"], name="fk_marketing_content_root_business"),
        CheckConstraint(f"channel IN {CHANNEL_SQL}", name="valid_channel"),
        CheckConstraint("content_type IN ('social_post','ad_copy','email_draft','whatsapp_draft','blog_draft','landing_page_copy','headline','cta','content_package')", name="valid_content_type"),
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 180", name="valid_title"),
        CheckConstraint("char_length(body) BETWEEN 1 AND 20000", name="valid_body"),
        CheckConstraint("cta IS NULL OR char_length(cta) <= 300", name="valid_cta"),
        CheckConstraint("language ~ '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$'", name="valid_language"),
        CheckConstraint("status IN ('draft','review','approved','scheduled','ready_to_publish','archived')", name="valid_status"),
        CheckConstraint("version BETWEEN 1 AND 10000", name="valid_version"),
        UniqueConstraint("id", "business_id", name="uq_marketing_content_id_business"),
        UniqueConstraint("business_id", "root_content_id", "version", name="uq_marketing_content_root_version"),
        Index("ix_marketing_content_business_status_updated", "business_id", "status", "updated_at", "id"),
        Index("ix_marketing_content_business_campaign", "business_id", "campaign_id", "id"),
        UniqueConstraint("business_id", "proposal_key", name="uq_marketing_content_business_proposal_key"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[UUID | None] = mapped_column(nullable=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    cta: Mapped[str | None] = mapped_column(String(300), nullable=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en", server_default="en")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    parent_content_id: Mapped[UUID | None] = mapped_column(nullable=True)
    root_content_id: Mapped[UUID] = mapped_column(nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    proposal_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    creative_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_for: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_evidence: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))


class CreativeAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketing_creative_assets"
    __table_args__ = (
        ForeignKeyConstraint(["campaign_id", "business_id"], ["marketing_campaigns.id", "marketing_campaigns.business_id"], name="fk_marketing_creative_assets_campaign_business"),
        ForeignKeyConstraint(["content_id", "business_id"], ["marketing_content.id", "marketing_content.business_id"], name="fk_marketing_creative_assets_content_business"),
        CheckConstraint("asset_type IN ('social_square','story_reel','landscape_ad','display_banner','creative_brief','other')", name="valid_asset_type"),
        CheckConstraint("source_type IN ('manual','import','ai_brief','future_provider')", name="valid_source_type"),
        CheckConstraint("instructions IS NULL OR char_length(instructions) <= 5000", name="valid_instructions"),
        CheckConstraint("visual_direction IS NULL OR char_length(visual_direction) <= 5000", name="valid_visual_direction"),
        CheckConstraint("generation_status IN ('draft','brief_ready','provider_required','ready','failed','archived')", name="valid_generation_status"),
        CheckConstraint("storage_reference IS NULL OR char_length(storage_reference) <= 1024", name="valid_storage_reference"),
        CheckConstraint("width IS NULL OR width BETWEEN 1 AND 20000", name="valid_width"),
        CheckConstraint("height IS NULL OR height BETWEEN 1 AND 20000", name="valid_height"),
        CheckConstraint("alt_text IS NULL OR char_length(alt_text) <= 1000", name="valid_alt_text"),
        UniqueConstraint("id", "business_id", name="uq_marketing_creative_assets_id_business"),
        Index("ix_marketing_creative_assets_business_campaign", "business_id", "campaign_id", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[UUID | None] = mapped_column(nullable=True)
    content_id: Mapped[UUID | None] = mapped_column(nullable=True)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    storage_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aspect_ratio: Mapped[str | None] = mapped_column(String(16), nullable=True)
    alt_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class SocialSchedule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "social_content_schedules"
    __table_args__ = (
        ForeignKeyConstraint(["content_id", "business_id"], ["marketing_content.id", "marketing_content.business_id"], name="fk_social_content_schedules_content_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["campaign_id", "business_id"], ["marketing_campaigns.id", "marketing_campaigns.business_id"], name="fk_social_content_schedules_campaign_business"),
        CheckConstraint(f"channel IN {CHANNEL_SQL}", name="valid_channel"),
        CheckConstraint("status IN ('scheduled','unscheduled','canceled','ready_to_publish')", name="valid_status"),
        CheckConstraint("char_length(btrim(timezone)) BETWEEN 1 AND 64", name="valid_timezone"),
        UniqueConstraint("id", "business_id", name="uq_social_content_schedules_id_business"),
        Index("ix_social_content_schedules_business_scheduled", "business_id", "scheduled_for", "id"),
        Index("ix_social_content_schedules_business_status", "business_id", "status", "scheduled_for", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    content_id: Mapped[UUID] = mapped_column(nullable=False)
    campaign_id: Mapped[UUID | None] = mapped_column(nullable=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="scheduled", server_default="scheduled")


class Competitor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketing_competitors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_candidate_id", "business_id"],
            ["competitor_candidates.id", "competitor_candidates.business_id"],
            name="fk_mkt_competitors_source_candidate_business",
        ),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 180", name="valid_name"),
        CheckConstraint("website_domain IS NULL OR website_domain ~ '^[A-Za-z0-9.-]{1,253}$'", name="valid_website_domain"),
        CheckConstraint("description IS NULL OR char_length(description) <= 3000", name="valid_description"),
        CheckConstraint("notes IS NULL OR char_length(notes) <= 4000", name="valid_notes"),
        UniqueConstraint("id", "business_id", name="uq_marketing_competitors_id_business"),
        UniqueConstraint("business_id", "website_domain", name="uq_marketing_competitors_business_domain"),
        Index("ix_marketing_competitors_business_active_updated", "business_id", "active", "updated_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    website_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_candidate_id: Mapped[UUID | None] = mapped_column(nullable=True)
    confirmation_source: Mapped[str] = mapped_column(String(24), nullable=False, default="manual", server_default="manual")


class CompetitorObservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "competitor_observations"
    __table_args__ = (
        ForeignKeyConstraint(["competitor_id", "business_id"], ["marketing_competitors.id", "marketing_competitors.business_id"], name="fk_competitor_observations_competitor_business", ondelete="CASCADE"),
        CheckConstraint("category IN ('pricing','product','marketing','content','positioning','promotion','social','website','offer')", name="valid_category"),
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 180", name="valid_title"),
        CheckConstraint("char_length(summary) BETWEEN 1 AND 5000", name="valid_summary"),
        CheckConstraint("source_type IN ('manual','import','ai_research')", name="valid_source_type"),
        CheckConstraint("source_reference IS NULL OR char_length(source_reference) <= 1024", name="valid_source_reference"),
        CheckConstraint("jsonb_typeof(safe_metrics) = 'object'", name="valid_metrics"),
        UniqueConstraint("id", "business_id", name="uq_competitor_observations_id_business"),
        Index("ix_competitor_observations_business_competitor_observed", "business_id", "competitor_id", "observed_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    competitor_id: Mapped[UUID] = mapped_column(nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False, default="manual", server_default="manual")
    source_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    safe_metrics: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))


class CompetitorAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "competitor_analyses"
    __table_args__ = (
        ForeignKeyConstraint(["competitor_id", "business_id"], ["marketing_competitors.id", "marketing_competitors.business_id"], name="fk_competitor_analyses_competitor_business", ondelete="CASCADE"),
        CheckConstraint("char_length(summary) BETWEEN 1 AND 5000", name="valid_summary"),
        CheckConstraint("cardinality(strengths) <= 20 AND cardinality(weaknesses) <= 20 AND cardinality(differences) <= 20", name="valid_comparison_counts"),
        CheckConstraint("cardinality(positioning_gaps) <= 20 AND cardinality(content_gaps) <= 20", name="valid_gap_counts"),
        CheckConstraint("cardinality(campaign_opportunities) <= 20 AND cardinality(recommendations) <= 20", name="valid_recommendation_counts"),
        CheckConstraint("source_observation_count BETWEEN 1 AND 1000", name="valid_source_count"),
        CheckConstraint("generated_by IN ('user','ai')", name="valid_generated_by"),
        Index("ix_competitor_analyses_business_competitor_created", "business_id", "competitor_id", "created_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    competitor_id: Mapped[UUID] = mapped_column(nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    strengths: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False, default=list, server_default="{}")
    weaknesses: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False, default=list, server_default="{}")
    differences: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False, default=list, server_default="{}")
    positioning_gaps: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False, default=list, server_default="{}")
    content_gaps: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False, default=list, server_default="{}")
    campaign_opportunities: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False, default=list, server_default="{}")
    recommendations: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False, default=list, server_default="{}")
    source_observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(16), nullable=False)


class MarketingTrend(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketing_trends"
    __table_args__ = (
        ForeignKeyConstraint(["opportunity_id", "business_id"], ["opportunities.id", "opportunities.business_id"], name="fk_marketing_trends_opportunity_business"),
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 180", name="valid_title"),
        CheckConstraint("category ~ '^[a-z][a-z0-9_]{0,47}$'", name="valid_category"),
        CheckConstraint("char_length(description) BETWEEN 1 AND 5000", name="valid_description"),
        CheckConstraint("source IN ('manual','import','ai_research')", name="valid_source"),
        CheckConstraint("source_reference IS NULL OR char_length(source_reference) <= 1024", name="valid_source_reference"),
        CheckConstraint("relevance_score BETWEEN 0.000 AND 1.000", name="valid_relevance"),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0.000 AND 1.000", name="valid_confidence"),
        CheckConstraint("status IN ('detected','reviewed','acted_on','dismissed','expired')", name="valid_status"),
        UniqueConstraint("id", "business_id", name="uq_marketing_trends_id_business"),
        Index("ix_marketing_trends_business_status_observed", "business_id", "status", "observed_at", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    relevance_score: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="detected", server_default="detected")
    opportunity_id: Mapped[UUID | None] = mapped_column(nullable=True)


class MarketingPerformance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketing_performance"
    __table_args__ = (
        ForeignKeyConstraint(["campaign_id", "business_id"], ["marketing_campaigns.id", "marketing_campaigns.business_id"], name="fk_marketing_performance_campaign_business", ondelete="CASCADE"),
        ForeignKeyConstraint(["content_id", "business_id"], ["marketing_content.id", "marketing_content.business_id"], name="fk_marketing_performance_content_business"),
        CheckConstraint(f"channel IN {CHANNEL_SQL}", name="valid_channel"),
        CheckConstraint("period_end >= period_start", name="valid_period"),
        CheckConstraint("data_source IN ('manual','import','future_connector')", name="valid_data_source"),
        CheckConstraint("spend >= 0 AND revenue >= 0", name="valid_money"),
        CheckConstraint("impressions >= 0 AND reach >= 0 AND clicks >= 0 AND leads >= 0 AND conversions >= 0", name="valid_counts"),
        CheckConstraint("ctr >= 0 AND cpc >= 0 AND cpm >= 0 AND cpl >= 0 AND cpa >= 0 AND roas >= 0", name="valid_derived_metrics"),
        UniqueConstraint("id", "business_id", name="uq_marketing_performance_id_business"),
        Index("ix_marketing_performance_business_period", "business_id", "period_start", "period_end", "id"),
        Index("ix_marketing_performance_business_campaign", "business_id", "campaign_id", "period_start", "id"),
    )

    business_id: Mapped[UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[UUID] = mapped_column(nullable=False)
    content_id: Mapped[UUID | None] = mapped_column(nullable=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    data_source: Mapped[str] = mapped_column(String(24), nullable=False)
    spend: Mapped[Decimal] = mapped_column(Numeric(16, 4), nullable=False, default=Decimal("0"), server_default="0")
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reach: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    leads: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    revenue: Mapped[Decimal] = mapped_column(Numeric(16, 4), nullable=False, default=Decimal("0"), server_default="0")
    ctr: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"), server_default="0")
    cpc: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False, default=Decimal("0"), server_default="0")
    cpm: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False, default=Decimal("0"), server_default="0")
    cpl: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False, default=Decimal("0"), server_default="0")
    cpa: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False, default=Decimal("0"), server_default="0")
    roas: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False, default=Decimal("0"), server_default="0")
