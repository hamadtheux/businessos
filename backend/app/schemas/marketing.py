from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


Channel = Literal["meta", "google_ads", "instagram", "facebook", "linkedin", "tiktok", "email", "whatsapp", "website", "other"]
MarketingPlanStatus = Literal["draft", "ready", "active", "completed", "archived"]
CampaignStatus = Literal["draft", "planned", "awaiting_approval", "approved", "scheduled", "active", "paused", "completed", "canceled"]
ChannelPlanStatus = Literal["draft", "ready", "approved", "scheduled", "active", "completed", "archived"]
ContentStatus = Literal["draft", "review", "approved", "scheduled", "ready_to_publish", "archived"]
TrendStatus = Literal["detected", "reviewed", "acted_on", "dismissed", "expired"]
ContentType = Literal["social_post", "ad_copy", "email_draft", "whatsapp_draft", "blog_draft", "landing_page_copy", "headline", "cta", "content_package"]
SafeSlug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
Money = Annotated[Decimal, Field(ge=0, le=Decimal("1000000000.00"), max_digits=14, decimal_places=2)]
Ratio = Annotated[Decimal, Field(ge=0, le=1, decimal_places=3)]


class MarketingSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MarketingRecord(MarketingSchema):
    id: UUID
    business_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class AudienceCreate(MarketingSchema):
    name: str = Field(min_length=1, max_length=160)
    countries: list[str] = Field(default_factory=list, max_length=25)
    regions: list[str] = Field(default_factory=list, max_length=50)
    min_age: int = Field(default=18, ge=18, le=100)
    max_age: int = Field(default=100, ge=18, le=100)
    languages: list[str] = Field(default_factory=list, max_length=20)
    customer_lifecycle: list[str] = Field(default_factory=list, max_length=20)
    crm_stages: list[str] = Field(default_factory=list, max_length=20)
    interests: list[str] = Field(default_factory=list, max_length=50)
    existing_customer_segment: str | None = Field(default=None, max_length=160)
    segment_description: str | None = Field(default=None, max_length=2000)

    @field_validator("countries")
    @classmethod
    def validate_countries(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values]
        if any(len(value) != 2 or not value.isalpha() for value in normalized):
            raise ValueError("countries must use ISO alpha-2 codes")
        if len(set(normalized)) != len(normalized):
            raise ValueError("countries cannot contain duplicates")
        return normalized

    @field_validator("regions", "languages", "customer_lifecycle", "crm_stages", "interests")
    @classmethod
    def validate_terms(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 80 for value in normalized):
            raise ValueError("audience terms must contain 1 to 80 characters")
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("audience terms cannot contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_age_range(self) -> "AudienceCreate":
        if self.max_age < self.min_age:
            raise ValueError("max_age cannot be less than min_age")
        return self


class AudienceResponse(AudienceCreate, MarketingRecord):
    created_by_user_id: UUID | None


class MarketingPlanCreate(MarketingSchema):
    audience_id: UUID | None = None
    title: str = Field(min_length=1, max_length=180)
    objective: str = Field(min_length=1, max_length=1000)
    target_audience: str = Field(min_length=1, max_length=2000)
    positioning: str = Field(min_length=1, max_length=3000)
    key_message: str = Field(min_length=1, max_length=3000)
    offer: str | None = Field(default=None, max_length=2000)
    channels: list[Channel] = Field(min_length=1, max_length=10)
    budget_guidance: Money | None = None
    period_start: date | None = None
    period_end: date | None = None
    content_strategy: str | None = Field(default=None, max_length=5000)
    measurement_goals: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("channels", "measurement_goals")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("values cannot contain duplicates")
        if any(len(value) > 160 for value in values):
            raise ValueError("value exceeds maximum length")
        return values

    @model_validator(mode="after")
    def valid_period(self) -> "MarketingPlanCreate":
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("period_end cannot precede period_start")
        return self


class MarketingPlanUpdate(MarketingSchema):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    objective: str | None = Field(default=None, min_length=1, max_length=1000)
    target_audience: str | None = Field(default=None, min_length=1, max_length=2000)
    positioning: str | None = Field(default=None, min_length=1, max_length=3000)
    key_message: str | None = Field(default=None, min_length=1, max_length=3000)
    offer: str | None = Field(default=None, max_length=2000)
    channels: list[Channel] | None = Field(default=None, min_length=1, max_length=10)
    budget_guidance: Money | None = None
    period_start: date | None = None
    period_end: date | None = None
    content_strategy: str | None = Field(default=None, max_length=5000)
    measurement_goals: list[str] | None = Field(default=None, max_length=20)

    @field_validator("channels", "measurement_goals")
    @classmethod
    def unique_values(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        if len(set(values)) != len(values):
            raise ValueError("values cannot contain duplicates")
        if any(len(value) > 160 for value in values):
            raise ValueError("value exceeds maximum length")
        return values


class MarketingPlanResponse(MarketingPlanCreate, MarketingRecord):
    currency: str
    status: MarketingPlanStatus
    generated_by: Literal["user", "ai"]
    created_by_user_id: UUID | None


class StatusUpdate(MarketingSchema):
    status: str = Field(min_length=1, max_length=32)


class CampaignCreate(MarketingSchema):
    marketing_plan_id: UUID | None = None
    audience_id: UUID | None = None
    name: str = Field(min_length=1, max_length=180)
    objective: str = Field(min_length=1, max_length=1000)
    description: str | None = Field(default=None, max_length=5000)
    offer: str | None = Field(default=None, max_length=2000)
    audience_definition: str = Field(min_length=1, max_length=3000)
    geographic_targeting: list[str] = Field(default_factory=list, max_length=50)
    channels: list[Channel] = Field(min_length=1, max_length=10)
    start_date: date | None = None
    end_date: date | None = None
    planned_budget: Money = Decimal("0.00")
    budget_mode: Literal["daily", "lifetime"] = "lifetime"

    @field_validator("channels", "geographic_targeting")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("values cannot contain duplicates")
        return values

    @model_validator(mode="after")
    def valid_period(self) -> "CampaignCreate":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot precede start_date")
        return self


class CampaignUpdate(MarketingSchema):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    objective: str | None = Field(default=None, min_length=1, max_length=1000)
    description: str | None = Field(default=None, max_length=5000)
    offer: str | None = Field(default=None, max_length=2000)
    audience_definition: str | None = Field(default=None, min_length=1, max_length=3000)
    geographic_targeting: list[str] | None = Field(default=None, max_length=50)
    channels: list[Channel] | None = Field(default=None, min_length=1, max_length=10)
    start_date: date | None = None
    end_date: date | None = None
    planned_budget: Money | None = None
    budget_mode: Literal["daily", "lifetime"] | None = None

    @field_validator("channels", "geographic_targeting")
    @classmethod
    def unique_values(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        if len(set(values)) != len(values):
            raise ValueError("values cannot contain duplicates")
        return values


class CampaignResponse(CampaignCreate, MarketingRecord):
    currency: str
    status: CampaignStatus
    created_by_user_id: UUID | None
    ai_generated: bool
    origin_type: str | None = None
    proposal_key: str | None = None
    proposal_reasoning: str | None = None
    creative_brief: str | None = None
    proposed_copy: str | None = None
    proposed_cta: str | None = None
    landing_destination: str | None = None
    measurement_plan: str | None = None
    assumptions: list[str] | None = None
    risks: list[str] | None = None
    required_integrations: list[str] | None = None
    source_evidence: list[dict[str, object]] | None = None
    audience_hypothesis_id: UUID | None = None


class ChannelConfiguration(MarketingSchema):
    placements: list[str] = Field(default_factory=list, max_length=20)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    call_to_action: str | None = Field(default=None, max_length=100)
    destination_path: str | None = Field(default=None, max_length=500)
    optimization_goal: Literal["awareness", "reach", "traffic", "engagement", "leads", "conversions", "sales"] | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ChannelPlanCreate(MarketingSchema):
    channel: Channel
    objective: str = Field(min_length=1, max_length=1000)
    budget_allocation: Money = Decimal("0.00")
    audience_strategy: str = Field(min_length=1, max_length=3000)
    messaging: str = Field(min_length=1, max_length=5000)
    planned_start: AwareDatetime | None = None
    planned_end: AwareDatetime | None = None
    safe_configuration: ChannelConfiguration = Field(default_factory=ChannelConfiguration)

    @model_validator(mode="after")
    def valid_period(self) -> "ChannelPlanCreate":
        if self.planned_start and self.planned_end and self.planned_end < self.planned_start:
            raise ValueError("planned_end cannot precede planned_start")
        return self


class ChannelPlanResponse(ChannelPlanCreate, MarketingRecord):
    campaign_id: UUID
    status: ChannelPlanStatus


class CampaignDetail(CampaignResponse):
    channel_plans: list[ChannelPlanResponse] = Field(default_factory=list)


class ContentCreate(MarketingSchema):
    campaign_id: UUID | None = None
    channel: Channel
    content_type: ContentType
    title: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1, max_length=20000)
    cta: str | None = Field(default=None, max_length=300)
    language: str = Field(default="en", pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


class ContentVersionCreate(MarketingSchema):
    title: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1, max_length=20000)
    cta: str | None = Field(default=None, max_length=300)


class ContentResponse(ContentCreate, MarketingRecord):
    status: ContentStatus
    ai_generated: bool
    version: int
    parent_content_id: UUID | None
    root_content_id: UUID
    created_by_user_id: UUID | None
    proposal_key: str | None = None
    creative_brief: str | None = None
    generation_reasoning: str | None = None
    recommended_for: str | None = None
    source_evidence: list[dict[str, object]] | None = None


class ContentGenerateRequest(MarketingSchema):
    prompt: str = Field(min_length=1, max_length=4000)
    campaign_id: UUID | None = None
    channel: Channel
    content_type: ContentType
    title: str | None = Field(default=None, max_length=180)
    language: str = Field(default="en", pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
    parent_content_id: UUID | None = None


class ScheduledContentProposal(MarketingSchema):
    """Named, review-only fields accepted from scheduled CMO generation."""

    title: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1, max_length=20000)
    cta: str | None = Field(default=None, max_length=300)
    creative_brief: str | None = Field(default=None, max_length=5000)
    recommended_channel: Channel
    generation_reasoning: str = Field(min_length=1, max_length=3000)
    evidence_source_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("evidence_source_ids")
    @classmethod
    def unique_evidence_source_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("evidence_source_ids cannot contain duplicates")
        if any(not value.strip() or len(value) > 255 for value in values):
            raise ValueError("evidence_source_ids contains an invalid reference")
        return values


class PlanGenerateRequest(MarketingSchema):
    goal: str = Field(min_length=1, max_length=4000)
    title: str | None = Field(default=None, max_length=180)
    target_audience: str = Field(min_length=1, max_length=2000)
    channels: list[Channel] = Field(min_length=1, max_length=10)
    budget_guidance: Money | None = None
    period_start: date | None = None
    period_end: date | None = None


class CampaignGenerateRequest(MarketingSchema):
    goal: str = Field(min_length=1, max_length=4000)
    name: str | None = Field(default=None, min_length=1, max_length=180)
    audience_definition: str | None = Field(default=None, min_length=1, max_length=3000)
    channels: list[Channel] = Field(default_factory=list, max_length=10)
    planned_budget: Money = Decimal("0.00")
    budget_mode: Literal["daily", "lifetime"] = "lifetime"
    start_date: date | None = None
    end_date: date | None = None


class CreativeBriefCreate(MarketingSchema):
    campaign_id: UUID | None = None
    content_id: UUID | None = None
    asset_type: Literal["social_square", "story_reel", "landscape_ad", "display_banner", "creative_brief", "other"]
    instructions: str = Field(min_length=1, max_length=5000)
    aspect_ratio: str | None = Field(default=None, max_length=16)
    width: int | None = Field(default=None, ge=1, le=20000)
    height: int | None = Field(default=None, ge=1, le=20000)
    alt_text: str | None = Field(default=None, max_length=1000)


class CreativeAssetResponse(MarketingRecord):
    campaign_id: UUID | None
    content_id: UUID | None
    asset_type: str
    source_type: Literal["manual", "import", "ai_brief", "future_provider"]
    instructions: str | None
    visual_direction: str | None
    generation_status: Literal["draft", "brief_ready", "provider_required", "ready", "failed", "archived"]
    storage_reference: str | None
    width: int | None
    height: int | None
    aspect_ratio: str | None
    alt_text: str | None


class ScheduleCreate(MarketingSchema):
    content_id: UUID
    scheduled_for: AwareDatetime


class ScheduleUpdate(MarketingSchema):
    scheduled_for: AwareDatetime


class ScheduleResponse(MarketingRecord):
    content_id: UUID
    campaign_id: UUID | None
    channel: Channel
    scheduled_for: AwareDatetime
    timezone: str
    status: Literal["scheduled", "unscheduled", "canceled", "ready_to_publish"]


class CompetitorCreate(MarketingSchema):
    name: str = Field(min_length=1, max_length=180)
    website_domain: str | None = Field(default=None, pattern=r"^[A-Za-z0-9.-]{1,253}$")
    description: str | None = Field(default=None, max_length=3000)
    notes: str | None = Field(default=None, max_length=4000)


class CompetitorUpdate(MarketingSchema):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    website_domain: str | None = Field(default=None, pattern=r"^[A-Za-z0-9.-]{1,253}$")
    description: str | None = Field(default=None, max_length=3000)
    active: bool | None = None
    notes: str | None = Field(default=None, max_length=4000)


class CompetitorResponse(CompetitorCreate, MarketingRecord):
    active: bool
    source_candidate_id: UUID | None = None
    confirmation_source: str | None = None


class CompetitorEvidenceResponse(MarketingRecord):
    candidate_id: UUID
    discovery_run_id: UUID
    source_type: Literal["provider_result", "public_url", "public_metadata", "ai_inference"]
    source_reference: str
    title: str
    excerpt: str
    observed_at: AwareDatetime
    safe_metadata: dict[str, object]
    fingerprint: str


class CompetitorCandidateResponse(MarketingRecord):
    discovery_run_id: UUID
    competitor_id: UUID | None
    name: str
    website_domain: str | None
    canonical_url: str | None
    source: str
    discovery_reason: str
    confidence: Decimal
    industry_relationship: str | None
    geographic_relationship: str | None
    status: Literal["suggested", "confirmed", "dismissed", "monitoring"]
    discovered_at: AwareDatetime
    last_seen_at: AwareDatetime


class CompetitorCandidateStatusUpdate(MarketingSchema):
    status: Literal["confirmed", "dismissed", "monitoring"]


class CompetitorDiscoveryRunResponse(MarketingRecord):
    trigger_type: Literal["onboarding", "brain_change", "scheduled", "manual_refresh"]
    provider_key: str | None
    brain_revision: str | None
    idempotency_key: str
    status: Literal[
        "queued", "running", "completed", "provider_unavailable",
        "blocked_entitlement", "failed",
    ]
    candidate_count: int
    results_processed: int = 0
    new_candidates: int = 0
    refreshed_candidates: int = 0
    evidence_added: int = 0
    failure_code: str | None
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None


class CompetitorDiscoveryStatusResponse(MarketingSchema):
    latest_run: CompetitorDiscoveryRunResponse | None
    provider_available: bool
    suggested_count: int
    monitored_count: int
    manual_refresh_available_at: AwareDatetime | None = None


class MarketingAutomationRunResponse(MarketingRecord):
    run_type: Literal["content_plan", "campaign_opportunities", "business_growth"]
    idempotency_key: str
    window_start: date
    window_end: date
    status: Literal[
        "queued", "running", "completed", "provider_unavailable",
        "blocked_entitlement", "failed",
    ]
    proposal_count: int
    failure_code: str | None
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None


class AudienceHypothesisResponse(MarketingRecord):
    # The campaign owns the optional one-to-one link. Endpoints populate this
    # compatibility field from Campaign.audience_hypothesis_id.
    campaign_id: UUID | None = None
    classification: Literal[
        "first_party_observed", "platform_supplied",
        "public_competitor_observation", "ai_inference",
    ]
    label: str
    summary: str
    confidence: Decimal
    evidence: list[dict[str, object]]
    segments: list[dict[str, object]]
    geographic_areas: list[str]
    interests: list[str]
    intent_signals: list[str]
    buyer_personas: list[str]
    likely_pain_points: list[str]
    preferred_channels: list[Channel]
    excluded_audiences: list[str]
    min_age: int | None = None
    max_age: int | None = None


class PrepareCampaignActionRequest(MarketingSchema):
    channel: Literal["meta", "google_ads"] | None = None


class PrepareContentActionRequest(MarketingSchema):
    channel: Literal["facebook", "instagram"] | None = None


class MarketingActionProposalResponse(MarketingRecord):
    entity_type: Literal["campaign", "content"]
    entity_id: UUID
    channel: str
    connector_type: str
    execution_id: UUID
    ai_action_id: UUID
    action_type: str
    action_status: str
    policy_decision: Literal["allow", "require_approval", "block"] | None
    policy_reason_code: str | None
    approval_id: UUID | None
    approval_status: Literal["pending", "approved", "rejected", "expired", "canceled"] | None
    connector_state: Literal["connection_required", "provider_disabled", "ready_after_approval"]
    connector_message: str


class CompetitorMetrics(MarketingSchema):
    price_min: Money | None = None
    price_max: Money | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    follower_count: int | None = Field(default=None, ge=0, le=2_000_000_000)
    engagement_rate: Ratio | None = None


class ObservationCreate(MarketingSchema):
    observed_at: AwareDatetime
    category: Literal["pricing", "product", "marketing", "content", "positioning", "promotion", "social", "website", "offer"]
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1, max_length=5000)
    source_type: Literal["manual", "import"] = "manual"
    source_reference: str | None = Field(default=None, max_length=1024)
    safe_metrics: CompetitorMetrics = Field(default_factory=CompetitorMetrics)


class ObservationResponse(ObservationCreate, MarketingRecord):
    competitor_id: UUID
    source_type: Literal["manual", "import", "ai_research"]


class CompetitorAnalysisResponse(MarketingRecord):
    competitor_id: UUID
    summary: str
    strengths: list[str]
    weaknesses: list[str]
    differences: list[str]
    positioning_gaps: list[str]
    content_gaps: list[str]
    campaign_opportunities: list[str]
    recommendations: list[str]
    source_observation_count: int
    generated_by: Literal["user", "ai"]


class TrendCreate(MarketingSchema):
    title: str = Field(min_length=1, max_length=180)
    category: SafeSlug
    description: str = Field(min_length=1, max_length=5000)
    source: Literal["manual", "import", "ai_research"] = "manual"
    source_reference: str | None = Field(default=None, max_length=1024)
    observed_at: AwareDatetime
    relevance_score: Ratio
    confidence: Ratio | None = None


class TrendResponse(TrendCreate, MarketingRecord):
    status: TrendStatus
    opportunity_id: UUID | None


class TrendOpportunityRequest(MarketingSchema):
    title: str | None = Field(default=None, max_length=180)
    description: str | None = Field(default=None, max_length=3000)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"


class PerformanceCreate(MarketingSchema):
    campaign_id: UUID
    content_id: UUID | None = None
    channel: Channel
    period_start: date
    period_end: date
    data_source: Literal["manual", "import"] = "manual"
    spend: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("1000000000"), max_digits=16, decimal_places=4)
    impressions: int = Field(default=0, ge=0, le=2_000_000_000)
    reach: int = Field(default=0, ge=0, le=2_000_000_000)
    clicks: int = Field(default=0, ge=0, le=2_000_000_000)
    leads: int = Field(default=0, ge=0, le=2_000_000_000)
    conversions: int = Field(default=0, ge=0, le=2_000_000_000)
    revenue: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("1000000000"), max_digits=16, decimal_places=4)

    @model_validator(mode="after")
    def validate_metrics(self) -> "PerformanceCreate":
        if self.period_end < self.period_start:
            raise ValueError("period_end cannot precede period_start")
        if self.clicks > self.impressions or self.conversions > self.clicks:
            raise ValueError("clicks/conversions cannot exceed their source totals")
        if self.reach > self.impressions:
            raise ValueError("reach cannot exceed impressions")
        return self


class PerformanceResponse(PerformanceCreate, MarketingRecord):
    data_source: Literal["manual", "import", "future_connector"]
    ctr: Decimal
    cpc: Decimal
    cpm: Decimal
    cpl: Decimal
    cpa: Decimal
    roas: Decimal


class AnalyticsBreakdown(MarketingSchema):
    label: str
    spend: Decimal
    impressions: int
    clicks: int
    leads: int
    conversions: int
    revenue: Decimal
    ctr: Decimal
    cpc: Decimal
    roas: Decimal


class MarketingTrendPoint(MarketingSchema):
    label: str
    spend: Decimal
    revenue: Decimal
    impressions: int
    clicks: int
    conversions: int


class TopContent(MarketingSchema):
    content_id: UUID
    title: str
    channel: Channel
    clicks: int
    conversions: int
    revenue: Decimal


class MarketingAnalyticsResponse(MarketingSchema):
    period_start: date
    period_end: date
    currency: str
    spend: Decimal
    impressions: int
    reach: int
    clicks: int
    leads: int
    conversions: int
    revenue: Decimal
    ctr: Decimal
    cpc: Decimal
    cpl: Decimal
    cpa: Decimal
    roas: Decimal
    campaigns: list[AnalyticsBreakdown]
    channels: list[AnalyticsBreakdown]
    top_content: list[TopContent]
    trends: list[MarketingTrendPoint]


class LearningResponse(MarketingSchema):
    created: bool
    conclusion: str | None = None
    memory_id: UUID | None = None


class AdvertisingSpendPolicyUpdate(MarketingSchema):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    max_single_campaign_budget: Money
    max_single_budget_change: Money
    daily_advertising_limit: Money | None = None
    monthly_ai_managed_limit: Money | None = None
    active: bool = True
    confirm_material_increase: bool = False

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_policy_currency(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class AdvertisingSpendPolicyResponse(MarketingRecord):
    currency: str
    max_single_campaign_budget: Decimal
    max_single_budget_change: Decimal
    daily_advertising_limit: Decimal | None
    monthly_ai_managed_limit: Decimal | None
    active: bool
    set_by_user_id: UUID | None
