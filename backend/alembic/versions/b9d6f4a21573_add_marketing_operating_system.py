"""add marketing operating system

Revision ID: b9d6f4a21573
Revises: a8c5e3f10462
Create Date: 2026-08-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b9d6f4a21573"
down_revision: str | None = "a8c5e3f10462"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CHANNELS = "('meta','google_ads','instagram','facebook','linkedin','tiktok','email','whatsapp','website','other')"
CHANNEL_ARRAY = "ARRAY['meta','google_ads','instagram','facebook','linkedin','tiktok','email','whatsapp','website','other']::varchar[]"


def _core() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def _business_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name=op.f(f"fk_{table}_business_id_businesses"), ondelete="CASCADE")


def upgrade() -> None:
    op.create_unique_constraint("uq_opportunities_id_business", "opportunities", ["id", "business_id"])
    op.drop_constraint(op.f("ck_business_reports_valid_report_type"), "business_reports", type_="check")
    op.create_check_constraint(op.f("ck_business_reports_valid_report_type"), "business_reports", "report_type IN ('daily_operations','sales','customer','scheduling','marketing')")

    op.create_table(
        "marketing_audiences",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("name", sa.String(160), nullable=False),
        sa.Column("countries", postgresql.ARRAY(sa.String(2)), server_default="{}", nullable=False),
        sa.Column("regions", postgresql.ARRAY(sa.String(80)), server_default="{}", nullable=False),
        sa.Column("min_age", sa.Integer(), server_default="18", nullable=False), sa.Column("max_age", sa.Integer(), server_default="100", nullable=False),
        sa.Column("languages", postgresql.ARRAY(sa.String(16)), server_default="{}", nullable=False),
        sa.Column("customer_lifecycle", postgresql.ARRAY(sa.String(24)), server_default="{}", nullable=False),
        sa.Column("crm_stages", postgresql.ARRAY(sa.String(24)), server_default="{}", nullable=False),
        sa.Column("interests", postgresql.ARRAY(sa.String(80)), server_default="{}", nullable=False),
        sa.Column("existing_customer_segment", sa.String(160), nullable=True), sa.Column("segment_description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True), *_core(),
        sa.CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 160", name=op.f("ck_marketing_audiences_valid_name")),
        sa.CheckConstraint("min_age BETWEEN 18 AND 100 AND max_age BETWEEN min_age AND 100", name=op.f("ck_marketing_audiences_valid_age_range")),
        sa.CheckConstraint("cardinality(countries) <= 25", name=op.f("ck_marketing_audiences_valid_country_count")),
        sa.CheckConstraint("cardinality(regions) <= 50", name=op.f("ck_marketing_audiences_valid_region_count")),
        sa.CheckConstraint("cardinality(languages) <= 20", name=op.f("ck_marketing_audiences_valid_language_count")),
        sa.CheckConstraint("cardinality(customer_lifecycle) <= 20", name=op.f("ck_marketing_audiences_valid_lifecycle_count")),
        sa.CheckConstraint("cardinality(crm_stages) <= 20", name=op.f("ck_marketing_audiences_valid_crm_stage_count")),
        sa.CheckConstraint("cardinality(interests) <= 50", name=op.f("ck_marketing_audiences_valid_interest_count")),
        sa.CheckConstraint("segment_description IS NULL OR char_length(segment_description) <= 2000", name=op.f("ck_marketing_audiences_valid_description")),
        _business_fk("marketing_audiences"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name=op.f("fk_marketing_audiences_created_by_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_audiences")), sa.UniqueConstraint("id", "business_id", name="uq_marketing_audiences_id_business"),
    )
    op.create_index("ix_marketing_audiences_business_updated", "marketing_audiences", ["business_id", "updated_at", "id"])

    op.create_table(
        "marketing_plans",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("audience_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(180), nullable=False), sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("target_audience", sa.Text(), nullable=False), sa.Column("positioning", sa.Text(), nullable=False),
        sa.Column("key_message", sa.Text(), nullable=False), sa.Column("offer", sa.Text(), nullable=True),
        sa.Column("channels", postgresql.ARRAY(sa.String(24)), nullable=False), sa.Column("budget_guidance", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False), sa.Column("period_start", sa.Date(), nullable=True), sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("content_strategy", sa.Text(), nullable=True), sa.Column("measurement_goals", postgresql.ARRAY(sa.String(160)), server_default="{}", nullable=False),
        sa.Column("status", sa.String(24), server_default="draft", nullable=False), sa.Column("generated_by", sa.String(16), server_default="user", nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True), *_core(),
        sa.CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 180", name=op.f("ck_marketing_plans_valid_title")),
        sa.CheckConstraint("char_length(objective) BETWEEN 1 AND 1000", name=op.f("ck_marketing_plans_valid_objective")),
        sa.CheckConstraint("char_length(target_audience) BETWEEN 1 AND 2000", name=op.f("ck_marketing_plans_valid_target_audience")),
        sa.CheckConstraint("char_length(positioning) BETWEEN 1 AND 3000", name=op.f("ck_marketing_plans_valid_positioning")),
        sa.CheckConstraint("char_length(key_message) BETWEEN 1 AND 3000", name=op.f("ck_marketing_plans_valid_key_message")),
        sa.CheckConstraint("offer IS NULL OR char_length(offer) <= 2000", name=op.f("ck_marketing_plans_valid_offer")),
        sa.CheckConstraint("content_strategy IS NULL OR char_length(content_strategy) <= 5000", name=op.f("ck_marketing_plans_valid_content_strategy")),
        sa.CheckConstraint(f"cardinality(channels) BETWEEN 1 AND 10 AND channels <@ {CHANNEL_ARRAY}", name=op.f("ck_marketing_plans_valid_channels")),
        sa.CheckConstraint("cardinality(measurement_goals) <= 20", name=op.f("ck_marketing_plans_valid_measurement_goals")),
        sa.CheckConstraint("budget_guidance IS NULL OR (budget_guidance >= 0 AND budget_guidance <= 1000000000)", name=op.f("ck_marketing_plans_valid_budget")),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_marketing_plans_valid_currency")),
        sa.CheckConstraint("period_end IS NULL OR period_start IS NULL OR period_end >= period_start", name=op.f("ck_marketing_plans_valid_period")),
        sa.CheckConstraint("status IN ('draft','ready','active','completed','archived')", name=op.f("ck_marketing_plans_valid_status")),
        sa.CheckConstraint("generated_by IN ('user','ai')", name=op.f("ck_marketing_plans_valid_generated_by")),
        _business_fk("marketing_plans"),
        sa.ForeignKeyConstraint(["audience_id", "business_id"], ["marketing_audiences.id", "marketing_audiences.business_id"], name="fk_marketing_plans_audience_business"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name=op.f("fk_marketing_plans_created_by_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_plans")), sa.UniqueConstraint("id", "business_id", name="uq_marketing_plans_id_business"),
    )
    op.create_index("ix_marketing_plans_business_status_updated", "marketing_plans", ["business_id", "status", "updated_at", "id"])

    op.create_table(
        "marketing_campaigns",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("marketing_plan_id", sa.Uuid(), nullable=True), sa.Column("audience_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(180), nullable=False), sa.Column("objective", sa.Text(), nullable=False), sa.Column("description", sa.Text(), nullable=True),
        sa.Column("offer", sa.Text(), nullable=True), sa.Column("audience_definition", sa.Text(), nullable=False),
        sa.Column("geographic_targeting", postgresql.ARRAY(sa.String(80)), server_default="{}", nullable=False),
        sa.Column("channels", postgresql.ARRAY(sa.String(24)), nullable=False), sa.Column("start_date", sa.Date(), nullable=True), sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("planned_budget", sa.Numeric(14, 2), server_default="0", nullable=False), sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("budget_mode", sa.String(16), server_default="lifetime", nullable=False), sa.Column("status", sa.String(32), server_default="draft", nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True), sa.Column("ai_generated", sa.Boolean(), server_default=sa.text("false"), nullable=False), *_core(),
        sa.CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 180", name=op.f("ck_marketing_campaigns_valid_name")),
        sa.CheckConstraint("char_length(objective) BETWEEN 1 AND 1000", name=op.f("ck_marketing_campaigns_valid_objective")),
        sa.CheckConstraint("description IS NULL OR char_length(description) <= 5000", name=op.f("ck_marketing_campaigns_valid_description")),
        sa.CheckConstraint("offer IS NULL OR char_length(offer) <= 2000", name=op.f("ck_marketing_campaigns_valid_offer")),
        sa.CheckConstraint("char_length(audience_definition) BETWEEN 1 AND 3000", name=op.f("ck_marketing_campaigns_valid_audience_definition")),
        sa.CheckConstraint("cardinality(geographic_targeting) <= 50", name=op.f("ck_marketing_campaigns_valid_geographic_targeting")),
        sa.CheckConstraint(f"cardinality(channels) BETWEEN 1 AND 10 AND channels <@ {CHANNEL_ARRAY}", name=op.f("ck_marketing_campaigns_valid_channels")),
        sa.CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name=op.f("ck_marketing_campaigns_valid_period")),
        sa.CheckConstraint("planned_budget >= 0 AND planned_budget <= 1000000000", name=op.f("ck_marketing_campaigns_valid_budget")),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_marketing_campaigns_valid_currency")),
        sa.CheckConstraint("budget_mode IN ('daily','lifetime')", name=op.f("ck_marketing_campaigns_valid_budget_mode")),
        sa.CheckConstraint("status IN ('draft','planned','awaiting_approval','approved','scheduled','active','paused','completed','canceled')", name=op.f("ck_marketing_campaigns_valid_status")),
        _business_fk("marketing_campaigns"),
        sa.ForeignKeyConstraint(["marketing_plan_id", "business_id"], ["marketing_plans.id", "marketing_plans.business_id"], name="fk_marketing_campaigns_plan_business"),
        sa.ForeignKeyConstraint(["audience_id", "business_id"], ["marketing_audiences.id", "marketing_audiences.business_id"], name="fk_marketing_campaigns_audience_business"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name=op.f("fk_marketing_campaigns_created_by_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_campaigns")), sa.UniqueConstraint("id", "business_id", name="uq_marketing_campaigns_id_business"),
    )
    op.create_index("ix_marketing_campaigns_business_status_updated", "marketing_campaigns", ["business_id", "status", "updated_at", "id"])
    op.create_index("ix_marketing_campaigns_business_period", "marketing_campaigns", ["business_id", "start_date", "end_date", "id"])

    op.create_table(
        "campaign_channel_plans",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("campaign_id", sa.Uuid(), nullable=False), sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False), sa.Column("budget_allocation", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("audience_strategy", sa.Text(), nullable=False), sa.Column("messaging", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), server_default="draft", nullable=False), sa.Column("planned_start", sa.DateTime(timezone=True), nullable=True), sa.Column("planned_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_configuration", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False), *_core(),
        sa.CheckConstraint(f"channel IN {CHANNELS}", name=op.f("ck_campaign_channel_plans_valid_channel")),
        sa.CheckConstraint("char_length(objective) BETWEEN 1 AND 1000", name=op.f("ck_campaign_channel_plans_valid_objective")),
        sa.CheckConstraint("budget_allocation >= 0 AND budget_allocation <= 1000000000", name=op.f("ck_campaign_channel_plans_valid_budget")),
        sa.CheckConstraint("char_length(audience_strategy) BETWEEN 1 AND 3000", name=op.f("ck_campaign_channel_plans_valid_audience_strategy")),
        sa.CheckConstraint("char_length(messaging) BETWEEN 1 AND 5000", name=op.f("ck_campaign_channel_plans_valid_messaging")),
        sa.CheckConstraint("status IN ('draft','ready','approved','scheduled','active','completed','archived')", name=op.f("ck_campaign_channel_plans_valid_status")),
        sa.CheckConstraint("planned_end IS NULL OR planned_start IS NULL OR planned_end >= planned_start", name=op.f("ck_campaign_channel_plans_valid_period")),
        sa.CheckConstraint("jsonb_typeof(safe_configuration) = 'object'", name=op.f("ck_campaign_channel_plans_valid_configuration")),
        _business_fk("campaign_channel_plans"),
        sa.ForeignKeyConstraint(["campaign_id", "business_id"], ["marketing_campaigns.id", "marketing_campaigns.business_id"], name="fk_campaign_channel_plans_campaign_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_channel_plans")), sa.UniqueConstraint("id", "business_id", name="uq_campaign_channel_plans_id_business"),
        sa.UniqueConstraint("business_id", "campaign_id", "channel", name="uq_campaign_channel_plans_campaign_channel"),
    )
    op.create_index("ix_campaign_channel_plans_business_campaign", "campaign_channel_plans", ["business_id", "campaign_id", "id"])

    op.create_table(
        "marketing_content",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("campaign_id", sa.Uuid(), nullable=True), sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False), sa.Column("title", sa.String(180), nullable=False), sa.Column("body", sa.Text(), nullable=False),
        sa.Column("cta", sa.String(300), nullable=True), sa.Column("language", sa.String(16), server_default="en", nullable=False),
        sa.Column("status", sa.String(24), server_default="draft", nullable=False), sa.Column("ai_generated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False), sa.Column("parent_content_id", sa.Uuid(), nullable=True),
        sa.Column("root_content_id", sa.Uuid(), nullable=False), sa.Column("created_by_user_id", sa.Uuid(), nullable=True), *_core(),
        sa.CheckConstraint(f"channel IN {CHANNELS}", name=op.f("ck_marketing_content_valid_channel")),
        sa.CheckConstraint("content_type IN ('social_post','ad_copy','email_draft','whatsapp_draft','blog_draft','landing_page_copy','headline','cta','content_package')", name=op.f("ck_marketing_content_valid_content_type")),
        sa.CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 180", name=op.f("ck_marketing_content_valid_title")),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 20000", name=op.f("ck_marketing_content_valid_body")),
        sa.CheckConstraint("cta IS NULL OR char_length(cta) <= 300", name=op.f("ck_marketing_content_valid_cta")),
        sa.CheckConstraint("language ~ '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$'", name=op.f("ck_marketing_content_valid_language")),
        sa.CheckConstraint("status IN ('draft','review','approved','scheduled','ready_to_publish','archived')", name=op.f("ck_marketing_content_valid_status")),
        sa.CheckConstraint("version BETWEEN 1 AND 10000", name=op.f("ck_marketing_content_valid_version")),
        _business_fk("marketing_content"),
        sa.ForeignKeyConstraint(["campaign_id", "business_id"], ["marketing_campaigns.id", "marketing_campaigns.business_id"], name="fk_marketing_content_campaign_business"),
        sa.ForeignKeyConstraint(["parent_content_id", "business_id"], ["marketing_content.id", "marketing_content.business_id"], name="fk_marketing_content_parent_business"),
        sa.ForeignKeyConstraint(["root_content_id", "business_id"], ["marketing_content.id", "marketing_content.business_id"], name="fk_marketing_content_root_business"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name=op.f("fk_marketing_content_created_by_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_content")), sa.UniqueConstraint("id", "business_id", name="uq_marketing_content_id_business"),
        sa.UniqueConstraint("business_id", "root_content_id", "version", name="uq_marketing_content_root_version"),
    )
    op.create_index("ix_marketing_content_business_status_updated", "marketing_content", ["business_id", "status", "updated_at", "id"])
    op.create_index("ix_marketing_content_business_campaign", "marketing_content", ["business_id", "campaign_id", "id"])

    op.create_table(
        "marketing_creative_assets",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("campaign_id", sa.Uuid(), nullable=True), sa.Column("content_id", sa.Uuid(), nullable=True),
        sa.Column("asset_type", sa.String(32), nullable=False), sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True), sa.Column("visual_direction", sa.Text(), nullable=True),
        sa.Column("generation_status", sa.String(24), server_default="draft", nullable=False), sa.Column("storage_reference", sa.String(1024), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True), sa.Column("height", sa.Integer(), nullable=True), sa.Column("aspect_ratio", sa.String(16), nullable=True), sa.Column("alt_text", sa.Text(), nullable=True), *_core(),
        sa.CheckConstraint("asset_type IN ('social_square','story_reel','landscape_ad','display_banner','creative_brief','other')", name=op.f("ck_marketing_creative_assets_valid_asset_type")),
        sa.CheckConstraint("source_type IN ('manual','import','ai_brief','future_provider')", name=op.f("ck_marketing_creative_assets_valid_source_type")),
        sa.CheckConstraint("instructions IS NULL OR char_length(instructions) <= 5000", name=op.f("ck_marketing_creative_assets_valid_instructions")),
        sa.CheckConstraint("visual_direction IS NULL OR char_length(visual_direction) <= 5000", name=op.f("ck_marketing_creative_assets_valid_visual_direction")),
        sa.CheckConstraint("generation_status IN ('draft','brief_ready','provider_required','ready','failed','archived')", name=op.f("ck_marketing_creative_assets_valid_generation_status")),
        sa.CheckConstraint("storage_reference IS NULL OR char_length(storage_reference) <= 1024", name=op.f("ck_marketing_creative_assets_valid_storage_reference")),
        sa.CheckConstraint("width IS NULL OR width BETWEEN 1 AND 20000", name=op.f("ck_marketing_creative_assets_valid_width")),
        sa.CheckConstraint("height IS NULL OR height BETWEEN 1 AND 20000", name=op.f("ck_marketing_creative_assets_valid_height")),
        sa.CheckConstraint("alt_text IS NULL OR char_length(alt_text) <= 1000", name=op.f("ck_marketing_creative_assets_valid_alt_text")),
        _business_fk("marketing_creative_assets"),
        sa.ForeignKeyConstraint(["campaign_id", "business_id"], ["marketing_campaigns.id", "marketing_campaigns.business_id"], name="fk_marketing_creative_assets_campaign_business"),
        sa.ForeignKeyConstraint(["content_id", "business_id"], ["marketing_content.id", "marketing_content.business_id"], name="fk_marketing_creative_assets_content_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_creative_assets")), sa.UniqueConstraint("id", "business_id", name="uq_marketing_creative_assets_id_business"),
    )
    op.create_index("ix_marketing_creative_assets_business_campaign", "marketing_creative_assets", ["business_id", "campaign_id", "id"])

    op.create_table(
        "social_content_schedules",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("content_id", sa.Uuid(), nullable=False), sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("channel", sa.String(24), nullable=False), sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False), sa.Column("status", sa.String(24), server_default="scheduled", nullable=False), *_core(),
        sa.CheckConstraint(f"channel IN {CHANNELS}", name=op.f("ck_social_content_schedules_valid_channel")),
        sa.CheckConstraint("status IN ('scheduled','unscheduled','canceled','ready_to_publish')", name=op.f("ck_social_content_schedules_valid_status")),
        sa.CheckConstraint("char_length(btrim(timezone)) BETWEEN 1 AND 64", name=op.f("ck_social_content_schedules_valid_timezone")),
        _business_fk("social_content_schedules"),
        sa.ForeignKeyConstraint(["content_id", "business_id"], ["marketing_content.id", "marketing_content.business_id"], name="fk_social_content_schedules_content_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["campaign_id", "business_id"], ["marketing_campaigns.id", "marketing_campaigns.business_id"], name="fk_social_content_schedules_campaign_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_social_content_schedules")), sa.UniqueConstraint("id", "business_id", name="uq_social_content_schedules_id_business"),
    )
    op.create_index("ix_social_content_schedules_business_scheduled", "social_content_schedules", ["business_id", "scheduled_for", "id"])
    op.create_index("ix_social_content_schedules_business_status", "social_content_schedules", ["business_id", "status", "scheduled_for", "id"])

    op.create_table(
        "marketing_competitors",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("name", sa.String(180), nullable=False), sa.Column("website_domain", sa.String(253), nullable=True),
        sa.Column("description", sa.Text(), nullable=True), sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False), sa.Column("notes", sa.Text(), nullable=True), *_core(),
        sa.CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 180", name=op.f("ck_marketing_competitors_valid_name")),
        sa.CheckConstraint("website_domain IS NULL OR website_domain ~ '^[A-Za-z0-9.-]{1,253}$'", name=op.f("ck_marketing_competitors_valid_website_domain")),
        sa.CheckConstraint("description IS NULL OR char_length(description) <= 3000", name=op.f("ck_marketing_competitors_valid_description")),
        sa.CheckConstraint("notes IS NULL OR char_length(notes) <= 4000", name=op.f("ck_marketing_competitors_valid_notes")),
        _business_fk("marketing_competitors"), sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_competitors")),
        sa.UniqueConstraint("id", "business_id", name="uq_marketing_competitors_id_business"), sa.UniqueConstraint("business_id", "website_domain", name="uq_marketing_competitors_business_domain"),
    )
    op.create_index("ix_marketing_competitors_business_active_updated", "marketing_competitors", ["business_id", "active", "updated_at", "id"])

    op.create_table(
        "competitor_observations",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("competitor_id", sa.Uuid(), nullable=False), sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(24), nullable=False), sa.Column("title", sa.String(180), nullable=False), sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(24), server_default="manual", nullable=False), sa.Column("source_reference", sa.String(1024), nullable=True),
        sa.Column("safe_metrics", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False), *_core(),
        sa.CheckConstraint("category IN ('pricing','product','marketing','content','positioning','promotion','social','website','offer')", name=op.f("ck_competitor_observations_valid_category")),
        sa.CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 180", name=op.f("ck_competitor_observations_valid_title")),
        sa.CheckConstraint("char_length(summary) BETWEEN 1 AND 5000", name=op.f("ck_competitor_observations_valid_summary")),
        sa.CheckConstraint("source_type IN ('manual','import','ai_research')", name=op.f("ck_competitor_observations_valid_source_type")),
        sa.CheckConstraint("source_reference IS NULL OR char_length(source_reference) <= 1024", name=op.f("ck_competitor_observations_valid_source_reference")),
        sa.CheckConstraint("jsonb_typeof(safe_metrics) = 'object'", name=op.f("ck_competitor_observations_valid_metrics")),
        _business_fk("competitor_observations"),
        sa.ForeignKeyConstraint(["competitor_id", "business_id"], ["marketing_competitors.id", "marketing_competitors.business_id"], name="fk_competitor_observations_competitor_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitor_observations")), sa.UniqueConstraint("id", "business_id", name="uq_competitor_observations_id_business"),
    )
    op.create_index("ix_competitor_observations_business_competitor_observed", "competitor_observations", ["business_id", "competitor_id", "observed_at", "id"])

    op.create_table(
        "competitor_analyses",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("competitor_id", sa.Uuid(), nullable=False), sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("strengths", postgresql.ARRAY(sa.String(500)), server_default="{}", nullable=False),
        sa.Column("weaknesses", postgresql.ARRAY(sa.String(500)), server_default="{}", nullable=False),
        sa.Column("differences", postgresql.ARRAY(sa.String(500)), server_default="{}", nullable=False),
        sa.Column("positioning_gaps", postgresql.ARRAY(sa.String(500)), server_default="{}", nullable=False),
        sa.Column("content_gaps", postgresql.ARRAY(sa.String(500)), server_default="{}", nullable=False),
        sa.Column("campaign_opportunities", postgresql.ARRAY(sa.String(500)), server_default="{}", nullable=False),
        sa.Column("recommendations", postgresql.ARRAY(sa.String(500)), server_default="{}", nullable=False),
        sa.Column("source_observation_count", sa.Integer(), nullable=False), sa.Column("generated_by", sa.String(16), nullable=False), *_core(),
        sa.CheckConstraint("char_length(summary) BETWEEN 1 AND 5000", name=op.f("ck_competitor_analyses_valid_summary")),
        sa.CheckConstraint("cardinality(strengths) <= 20 AND cardinality(weaknesses) <= 20 AND cardinality(differences) <= 20", name=op.f("ck_competitor_analyses_valid_comparison_counts")),
        sa.CheckConstraint("cardinality(positioning_gaps) <= 20 AND cardinality(content_gaps) <= 20", name=op.f("ck_competitor_analyses_valid_gap_counts")),
        sa.CheckConstraint("cardinality(campaign_opportunities) <= 20 AND cardinality(recommendations) <= 20", name=op.f("ck_competitor_analyses_valid_recommendation_counts")),
        sa.CheckConstraint("source_observation_count BETWEEN 1 AND 1000", name=op.f("ck_competitor_analyses_valid_source_count")),
        sa.CheckConstraint("generated_by IN ('user','ai')", name=op.f("ck_competitor_analyses_valid_generated_by")),
        _business_fk("competitor_analyses"),
        sa.ForeignKeyConstraint(["competitor_id", "business_id"], ["marketing_competitors.id", "marketing_competitors.business_id"], name="fk_competitor_analyses_competitor_business", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitor_analyses")),
    )
    op.create_index("ix_competitor_analyses_business_competitor_created", "competitor_analyses", ["business_id", "competitor_id", "created_at", "id"])

    op.create_table(
        "marketing_trends",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("title", sa.String(180), nullable=False), sa.Column("category", sa.String(48), nullable=False),
        sa.Column("description", sa.Text(), nullable=False), sa.Column("source", sa.String(24), nullable=False), sa.Column("source_reference", sa.String(1024), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("relevance_score", sa.Numeric(4, 3), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True), sa.Column("status", sa.String(24), server_default="detected", nullable=False), sa.Column("opportunity_id", sa.Uuid(), nullable=True), *_core(),
        sa.CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 180", name=op.f("ck_marketing_trends_valid_title")),
        sa.CheckConstraint("category ~ '^[a-z][a-z0-9_]{0,47}$'", name=op.f("ck_marketing_trends_valid_category")),
        sa.CheckConstraint("char_length(description) BETWEEN 1 AND 5000", name=op.f("ck_marketing_trends_valid_description")),
        sa.CheckConstraint("source IN ('manual','import','ai_research')", name=op.f("ck_marketing_trends_valid_source")),
        sa.CheckConstraint("source_reference IS NULL OR char_length(source_reference) <= 1024", name=op.f("ck_marketing_trends_valid_source_reference")),
        sa.CheckConstraint("relevance_score BETWEEN 0.000 AND 1.000", name=op.f("ck_marketing_trends_valid_relevance")),
        sa.CheckConstraint("confidence IS NULL OR confidence BETWEEN 0.000 AND 1.000", name=op.f("ck_marketing_trends_valid_confidence")),
        sa.CheckConstraint("status IN ('detected','reviewed','acted_on','dismissed','expired')", name=op.f("ck_marketing_trends_valid_status")),
        _business_fk("marketing_trends"),
        sa.ForeignKeyConstraint(["opportunity_id", "business_id"], ["opportunities.id", "opportunities.business_id"], name="fk_marketing_trends_opportunity_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_trends")), sa.UniqueConstraint("id", "business_id", name="uq_marketing_trends_id_business"),
    )
    op.create_index("ix_marketing_trends_business_status_observed", "marketing_trends", ["business_id", "status", "observed_at", "id"])

    op.create_table(
        "marketing_performance",
        sa.Column("business_id", sa.Uuid(), nullable=False), sa.Column("campaign_id", sa.Uuid(), nullable=False), sa.Column("content_id", sa.Uuid(), nullable=True), sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False), sa.Column("period_end", sa.Date(), nullable=False), sa.Column("data_source", sa.String(24), nullable=False),
        sa.Column("spend", sa.Numeric(16, 4), server_default="0", nullable=False), sa.Column("impressions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reach", sa.Integer(), server_default="0", nullable=False), sa.Column("clicks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("leads", sa.Integer(), server_default="0", nullable=False), sa.Column("conversions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revenue", sa.Numeric(16, 4), server_default="0", nullable=False), sa.Column("ctr", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.Column("cpc", sa.Numeric(16, 6), server_default="0", nullable=False), sa.Column("cpm", sa.Numeric(16, 6), server_default="0", nullable=False),
        sa.Column("cpl", sa.Numeric(16, 6), server_default="0", nullable=False), sa.Column("cpa", sa.Numeric(16, 6), server_default="0", nullable=False),
        sa.Column("roas", sa.Numeric(16, 6), server_default="0", nullable=False), *_core(),
        sa.CheckConstraint(f"channel IN {CHANNELS}", name=op.f("ck_marketing_performance_valid_channel")),
        sa.CheckConstraint("period_end >= period_start", name=op.f("ck_marketing_performance_valid_period")),
        sa.CheckConstraint("data_source IN ('manual','import','future_connector')", name=op.f("ck_marketing_performance_valid_data_source")),
        sa.CheckConstraint("spend >= 0 AND revenue >= 0", name=op.f("ck_marketing_performance_valid_money")),
        sa.CheckConstraint("impressions >= 0 AND reach >= 0 AND clicks >= 0 AND leads >= 0 AND conversions >= 0", name=op.f("ck_marketing_performance_valid_counts")),
        sa.CheckConstraint("ctr >= 0 AND cpc >= 0 AND cpm >= 0 AND cpl >= 0 AND cpa >= 0 AND roas >= 0", name=op.f("ck_marketing_performance_valid_derived_metrics")),
        _business_fk("marketing_performance"),
        sa.ForeignKeyConstraint(["campaign_id", "business_id"], ["marketing_campaigns.id", "marketing_campaigns.business_id"], name="fk_marketing_performance_campaign_business", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_id", "business_id"], ["marketing_content.id", "marketing_content.business_id"], name="fk_marketing_performance_content_business"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_performance")), sa.UniqueConstraint("id", "business_id", name="uq_marketing_performance_id_business"),
    )
    op.create_index("ix_marketing_performance_business_period", "marketing_performance", ["business_id", "period_start", "period_end", "id"])
    op.create_index("ix_marketing_performance_business_campaign", "marketing_performance", ["business_id", "campaign_id", "period_start", "id"])


def downgrade() -> None:
    for table in (
        "marketing_performance", "marketing_trends", "competitor_analyses", "competitor_observations", "marketing_competitors",
        "social_content_schedules", "marketing_creative_assets", "marketing_content", "campaign_channel_plans", "marketing_campaigns",
        "marketing_plans", "marketing_audiences",
    ):
        op.drop_table(table)
    op.drop_constraint(op.f("ck_business_reports_valid_report_type"), "business_reports", type_="check")
    op.create_check_constraint(op.f("ck_business_reports_valid_report_type"), "business_reports", "report_type IN ('daily_operations','sales','customer','scheduling')")
    op.drop_constraint("uq_opportunities_id_business", "opportunities", type_="unique")
