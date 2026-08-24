from __future__ import annotations

import os
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.marketing import MarketingStateError, MarketingValidationError  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.business import Business  # noqa: E402
from app.models.marketing import Campaign, CampaignChannelPlan, Competitor, CompetitorObservation, MarketingContent, MarketingPlan, MarketingTrend, SocialSchedule  # noqa: E402
from app.models.opportunity import Opportunity  # noqa: E402
from app.schemas.marketing import CampaignCreate, CampaignGenerateRequest, ChannelPlanCreate, ContentCreate, ContentVersionCreate, CreativeBriefCreate, PerformanceCreate, PlanGenerateRequest, ScheduleCreate, TrendOpportunityRequest  # noqa: E402
from app.services.marketing import (  # noqa: E402
    _allocate_budget,
    _page,
    _term,
    analyze_competitor,
    change_campaign_status,
    change_trend_status,
    create_channel_plan,
    create_campaign,
    create_content,
    create_content_version,
    create_creative_brief,
    create_schedule,
    derive_metrics,
    generate_campaign,
    generate_plan,
    learn_from_performance,
    marketing_analytics,
    reschedule,
    trend_to_opportunity,
    unschedule,
    _run_cmo,
)


BUSINESS_ID = uuid4()
USER_ID = uuid4()
NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


class MarketingServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_pagination_and_search_are_bounded(self) -> None:
        self.assertEqual(_page(3, 10), (20, 10))
        self.assertEqual(_term(" Summer "), "Summer")
        for page, size in ((0, 10), (1, 101)):
            with self.subTest(page=page, size=size), self.assertRaises(MarketingValidationError):
                _page(page, size)
        with self.assertRaises(MarketingValidationError):
            _term("x" * 101)

    def test_budget_allocation_is_decimal_and_preserves_total(self) -> None:
        values = _allocate_budget(Decimal("2000.00"), 3)
        self.assertEqual(sum(values), Decimal("2000.00"))
        self.assertEqual(values, [Decimal("666.67"), Decimal("666.67"), Decimal("666.66")])

    def test_performance_metrics_are_calculated_server_side(self) -> None:
        data = PerformanceCreate(campaign_id=uuid4(), channel="instagram", period_start=date(2026, 8, 1), period_end=date(2026, 8, 7), spend="100", impressions=10000, reach=8000, clicks=200, leads=20, conversions=10, revenue="500")
        result = derive_metrics(data)
        self.assertEqual(result["ctr"], Decimal("2.000000"))
        self.assertEqual(result["cpc"], Decimal("0.500000"))
        self.assertEqual(result["cpm"], Decimal("10.000000"))
        self.assertEqual(result["cpl"], Decimal("5.000000"))
        self.assertEqual(result["cpa"], Decimal("10.000000"))
        self.assertEqual(result["roas"], Decimal("5.000000"))

    async def test_campaign_uses_trusted_business_currency(self) -> None:
        business = Business(id=BUSINESS_ID, name="Acme", slug="acme", business_type="retail", status="active", timezone="UTC", currency="PKR", locale="en", created_at=NOW, updated_at=NOW)
        session = _ScalarSession([business])
        campaign = await create_campaign(session, business_id=BUSINESS_ID, actor_user_id=USER_ID, data=CampaignCreate(name="Summer", objective="Grow sales", audience_definition="Existing customers", channels=["instagram"], planned_budget="2000"))
        self.assertEqual(campaign.currency, "PKR")
        self.assertTrue(any(isinstance(item, AuditLog) for item in session.added))

    async def test_campaign_lifecycle_cannot_skip_approval_and_is_audited(self) -> None:
        campaign = _campaign("draft")
        with self.assertRaises(MarketingStateError):
            await change_campaign_status(_ScalarSession([campaign]), business_id=BUSINESS_ID, campaign_id=campaign.id, actor_user_id=USER_ID, status="active")
        session = _ScalarSession([campaign])
        changed = await change_campaign_status(session, business_id=BUSINESS_ID, campaign_id=campaign.id, actor_user_id=USER_ID, status="awaiting_approval")
        self.assertEqual(changed.status, "awaiting_approval")
        self.assertTrue(any(isinstance(item, AuditLog) for item in session.added))

    async def test_channel_allocation_cannot_exceed_server_campaign_budget(self) -> None:
        campaign = _campaign("draft")
        with self.assertRaises(MarketingValidationError):
            await create_channel_plan(_ScalarSession([campaign, Decimal("1990")]), business_id=BUSINESS_ID, campaign_id=campaign.id, actor_user_id=USER_ID, data=ChannelPlanCreate(channel="instagram", objective="Grow", budget_allocation="20", audience_strategy="Customers", messaging="Grounded"))

    async def test_content_edit_creates_new_version_without_overwrite(self) -> None:
        root_id = uuid4()
        parent = MarketingContent(id=root_id, business_id=BUSINESS_ID, campaign_id=None, channel="instagram", content_type="social_post", title="Original", body="Original body", cta=None, language="en", status="approved", ai_generated=False, version=1, parent_content_id=None, root_content_id=root_id, created_by_user_id=USER_ID, created_at=NOW, updated_at=NOW)
        session = _ScalarSession([parent, 1])
        child = await create_content_version(session, business_id=BUSINESS_ID, content_id=parent.id, actor_user_id=USER_ID, data=ContentVersionCreate(title="Edited", body="Edited body"))
        self.assertEqual(parent.body, "Original body")
        self.assertEqual(child.version, 2)
        self.assertEqual(child.parent_content_id, parent.id)
        self.assertEqual(child.root_content_id, parent.root_content_id)

    async def test_content_version_cannot_change_campaign_or_channel_identity(self) -> None:
        root_id = uuid4()
        parent = MarketingContent(id=root_id, business_id=BUSINESS_ID, campaign_id=uuid4(), channel="instagram", content_type="social_post", title="Original", body="Original body", cta=None, language="en", status="draft", ai_generated=False, version=1, parent_content_id=None, root_content_id=root_id, created_by_user_id=USER_ID, created_at=NOW, updated_at=NOW)
        with self.assertRaises(MarketingValidationError):
            await create_content(_ScalarSession([parent.campaign_id]), business_id=BUSINESS_ID, actor_user_id=USER_ID, parent_content_id=parent.id, parent_content=parent, data=ContentCreate(campaign_id=parent.campaign_id, channel="email", content_type="social_post", title="Changed", body="Changed"))

    async def test_ai_campaign_generation_preserves_budget_and_ignores_external_actions(self) -> None:
        business = Business(id=BUSINESS_ID, name="Acme", slug="acme", business_type="retail", status="active", timezone="UTC", currency="USD", locale="en", created_at=NOW, updated_at=NOW)
        session = _ScalarSession([business])
        output = SimpleNamespace(summary="Grounded plan", recommendations=["Instagram direction", "Email direction"], proposed_actions=[SimpleNamespace(action_type="launch_meta_campaign")])
        audience = SimpleNamespace(
            id=uuid4(), preferred_channels=["instagram", "email"], summary="Existing customers",
            evidence=[], confidence=Decimal("0.650"), geographic_areas=[], campaign_id=None,
        )
        with patch("app.services.marketing._run_cmo", new=AsyncMock(return_value=output)), patch(
            "app.services.marketing.build_audience_hypothesis", new=AsyncMock(return_value=audience),
        ):
            campaign = await generate_campaign(session, business_id=BUSINESS_ID, actor_user_id=USER_ID, data=CampaignGenerateRequest(goal="Grow", name="Summer", audience_definition="Existing customers", channels=["instagram", "email"], planned_budget="2000"), provider=SimpleNamespace())
        channel_plans = [item for item in session.added if isinstance(item, CampaignChannelPlan)]
        self.assertTrue(campaign.ai_generated)
        self.assertEqual(sum((item.budget_allocation for item in channel_plans), Decimal("0")), Decimal("2000"))
        self.assertEqual([item.channel for item in channel_plans], ["instagram", "email"])
        self.assertFalse(any(type(item).__name__ == "AIAction" for item in session.added))
        self.assertEqual(campaign.audience_hypothesis_id, audience.id)
        self.assertIn("External execution remains unavailable", campaign.risks[-1])

    async def test_plan_generation_uses_cmo_runtime_result_and_persists_only_conclusions(self) -> None:
        business = Business(id=BUSINESS_ID, name="Acme", slug="acme", business_type="retail", status="active", timezone="UTC", currency="USD", locale="en", created_at=NOW, updated_at=NOW)
        session = _ScalarSession([business])
        output = SimpleNamespace(summary="Grounded positioning", recommendations=["Lead with quality", "Track conversions"])
        with patch("app.services.marketing._run_cmo", new=AsyncMock(return_value=output)) as runtime:
            plan = await generate_plan(session, business_id=BUSINESS_ID, actor_user_id=USER_ID, data=PlanGenerateRequest(goal="Summer launch", target_audience="Existing customers", channels=["instagram"], budget_guidance="2000"), provider=SimpleNamespace())
        self.assertEqual(plan.generated_by, "ai")
        self.assertEqual(plan.positioning, "Grounded positioning")
        self.assertFalse(hasattr(plan, "reasoning"))
        self.assertIn("trusted Business Brain", runtime.await_args.args[2])

    async def test_cmo_generation_uses_existing_runtime_with_trusted_context_flags(self) -> None:
        output = SimpleNamespace(summary="Conclusion", recommendations=[], proposed_actions=[SimpleNamespace(action_type="launch_meta_campaign")])
        with patch("app.services.marketing.execute_ai_agent", new=AsyncMock(return_value=SimpleNamespace(output=output))) as runtime:
            result = await _run_cmo(SimpleNamespace(), BUSINESS_ID, "Prepare a draft", SimpleNamespace())
        request = runtime.await_args.args[2]
        self.assertEqual(request.role, "cmo")
        self.assertTrue(request.include_business_brain)
        self.assertTrue(request.include_memory)
        self.assertIs(result, output)

    async def test_social_schedule_uses_business_timezone_and_utc_instant(self) -> None:
        content_id = uuid4()
        content = MarketingContent(id=content_id, business_id=BUSINESS_ID, campaign_id=None, channel="instagram", content_type="social_post", title="Approved", body="Grounded", cta=None, language="en", status="approved", ai_generated=False, version=1, parent_content_id=None, root_content_id=content_id, created_by_user_id=USER_ID, created_at=NOW, updated_at=NOW)
        business = Business(id=BUSINESS_ID, name="Acme", slug="acme", business_type="retail", status="active", timezone="Asia/Karachi", currency="USD", locale="en", created_at=NOW, updated_at=NOW)
        session = _ScalarSession([content, business])
        value = await create_schedule(session, business_id=BUSINESS_ID, actor_user_id=USER_ID, data=ScheduleCreate(content_id=content.id, scheduled_for=datetime(2026, 8, 24, 10, tzinfo=UTC)))
        self.assertEqual(value.timezone, "Asia/Karachi")
        self.assertEqual(value.scheduled_for.utcoffset(), datetime.now(UTC).utcoffset())
        self.assertEqual(content.status, "scheduled")

    async def test_schedule_can_be_rescheduled_then_unscheduled_without_publishing(self) -> None:
        content_id = uuid4()
        content = MarketingContent(id=content_id, business_id=BUSINESS_ID, campaign_id=None, channel="instagram", content_type="social_post", title="Approved", body="Grounded", cta=None, language="en", status="scheduled", ai_generated=False, version=1, parent_content_id=None, root_content_id=content_id, created_by_user_id=USER_ID, created_at=NOW, updated_at=NOW)
        schedule = SocialSchedule(id=uuid4(), business_id=BUSINESS_ID, content_id=content.id, campaign_id=None, channel="instagram", scheduled_for=NOW, timezone="UTC", status="scheduled", created_at=NOW, updated_at=NOW)
        moved = await reschedule(_ScalarSession([schedule]), business_id=BUSINESS_ID, schedule_id=schedule.id, actor_user_id=USER_ID, scheduled_for=datetime(2026, 8, 25, 12, tzinfo=UTC))
        self.assertEqual(moved.scheduled_for, datetime(2026, 8, 25, 12, tzinfo=UTC))
        removed = await unschedule(_ScalarSession([schedule, content]), business_id=BUSINESS_ID, schedule_id=schedule.id, actor_user_id=USER_ID)
        self.assertEqual(removed.status, "unscheduled")
        self.assertEqual(content.status, "approved")

    async def test_creative_brief_rejects_content_from_another_campaign(self) -> None:
        campaign = _campaign("draft")
        content_id = uuid4()
        content = MarketingContent(id=content_id, business_id=BUSINESS_ID, campaign_id=uuid4(), channel="instagram", content_type="social_post", title="Draft", body="Grounded", cta=None, language="en", status="draft", ai_generated=False, version=1, parent_content_id=None, root_content_id=content_id, created_by_user_id=USER_ID, created_at=NOW, updated_at=NOW)
        with self.assertRaises(MarketingValidationError):
            await create_creative_brief(_ScalarSession([campaign, content]), business_id=BUSINESS_ID, actor_user_id=USER_ID, data=CreativeBriefCreate(campaign_id=campaign.id, content_id=content.id, asset_type="social_square", instructions="Use the brand palette"), provider=SimpleNamespace())

    async def test_competitor_analysis_uses_only_stored_observations(self) -> None:
        competitor = Competitor(id=uuid4(), business_id=BUSINESS_ID, name="Rival", website_domain="rival.test", description=None, active=True, notes=None, created_at=NOW, updated_at=NOW)
        observation = CompetitorObservation(id=uuid4(), business_id=BUSINESS_ID, competitor_id=competitor.id, observed_at=NOW, category="offer", title="Observed offer", summary="Public page described a bundle", source_type="manual", source_reference="https://rival.test/offer", safe_metrics={}, created_at=NOW, updated_at=NOW)
        output = SimpleNamespace(summary="Evidence-grounded conclusion", recommendations=[f"Recommendation {index}" for index in range(20)])
        with patch("app.services.marketing._run_cmo", new=AsyncMock(return_value=output)) as runtime:
            analysis = await analyze_competitor(_ScalarSession([competitor], rows=[[observation]]), business_id=BUSINESS_ID, competitor_id=competitor.id, actor_user_id=USER_ID, provider=SimpleNamespace())
        self.assertEqual(analysis.source_observation_count, 1)
        self.assertIn("Public page described a bundle", runtime.await_args.args[2])
        self.assertFalse(hasattr(analysis, "raw_provider_response"))

    async def test_marketing_analytics_maps_database_aggregates_without_fabrication(self) -> None:
        session = _AnalyticsSession()
        value = await marketing_analytics(session, business_id=BUSINESS_ID, period_start=date(2026, 8, 1), period_end=date(2026, 8, 7))
        self.assertEqual(value.spend, Decimal("100"))
        self.assertEqual(value.revenue, Decimal("500"))
        self.assertEqual(value.ctr, Decimal("2.000000"))
        self.assertEqual(value.roas, Decimal("5.000000"))
        self.assertEqual(value.channels[0].label, "instagram")
        self.assertEqual(value.campaigns[0].label, "Summer")
        self.assertEqual(value.top_content[0].title, "Post")
        self.assertTrue(all("business_id" in statement for statement in session.statements[1:]))

    async def test_trend_lifecycle_requires_review_before_action(self) -> None:
        trend = MarketingTrend(id=uuid4(), business_id=BUSINESS_ID, title="Demand", category="demand", description="Stored source", source="manual", source_reference=None, observed_at=NOW, relevance_score=Decimal("0.9"), confidence=Decimal("0.8"), status="detected", opportunity_id=None, created_at=NOW, updated_at=NOW)
        with self.assertRaises(MarketingStateError):
            await change_trend_status(_ScalarSession([trend]), business_id=BUSINESS_ID, trend_id=trend.id, actor_user_id=USER_ID, status="acted_on")
        reviewed = await change_trend_status(_ScalarSession([trend]), business_id=BUSINESS_ID, trend_id=trend.id, actor_user_id=USER_ID, status="reviewed")
        self.assertEqual(reviewed.status, "reviewed")

    async def test_reviewed_trend_converts_to_opportunity_without_ai_execution(self) -> None:
        trend = MarketingTrend(id=uuid4(), business_id=BUSINESS_ID, title="Demand", category="demand", description="Stored source", source="manual", source_reference=None, observed_at=NOW, relevance_score=Decimal("0.9"), confidence=Decimal("0.8"), status="reviewed", opportunity_id=None, created_at=NOW, updated_at=NOW)
        session = _ScalarSession([trend])
        opportunity = await trend_to_opportunity(session, business_id=BUSINESS_ID, trend_id=trend.id, actor_user_id=USER_ID, data=TrendOpportunityRequest())
        self.assertIsInstance(opportunity, Opportunity)
        self.assertEqual(trend.status, "acted_on")
        self.assertEqual(trend.opportunity_id, opportunity.id)

    async def test_marketing_learning_requires_sufficient_comparison_and_avoids_causal_claims(self) -> None:
        analytics = SimpleNamespace(impressions=5000, conversions=25, channels=[SimpleNamespace(label="email", roas=Decimal("3"), conversions=15, clicks=100), SimpleNamespace(label="instagram", roas=Decimal("2"), conversions=10, clicks=200)])
        memory_id = uuid4()
        with patch("app.services.marketing.marketing_analytics", new=AsyncMock(return_value=analytics)), patch("app.services.marketing.create_system_memory", new=AsyncMock(return_value=SimpleNamespace(id=memory_id))) as memory:
            result = await learn_from_performance(SimpleNamespace(), business_id=BUSINESS_ID, period_start=date(2026, 8, 1), period_end=date(2026, 8, 7))
        self.assertTrue(result.created)
        self.assertIn("not proof of causation", result.conclusion)
        self.assertEqual(memory.await_args.kwargs["memory_type"], "ai_learning")


class _ScalarSession:
    def __init__(self, values, rows=None):
        self.values = list(values)
        self.rows = list(rows or [])
        self.added = []
        self.flush_calls = 0

    async def scalar(self, _statement):
        return self.values.pop(0) if self.values else None

    async def scalars(self, _statement):
        return _ScalarRows(self.rows.pop(0) if self.rows else [])

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_calls += 1


class _ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def one(self):
        return self.rows[0]

    def all(self):
        return self.rows


class _AnalyticsSession:
    def __init__(self):
        self.statements = []
        self.results = [
            _Rows([(Decimal("100"), 10000, 8000, 200, 20, 10, Decimal("500"))]),
            _Rows([("instagram", Decimal("100"), 10000, 200, 20, 10, Decimal("500"))]),
            _Rows([("Summer", Decimal("100"), 10000, 200, 20, 10, Decimal("500"))]),
            _Rows([(date(2026, 8, 1), Decimal("100"), 10000, 200, 10, Decimal("500"))]),
            _Rows([(uuid4(), "Post", "instagram", 200, 10, Decimal("500"))]),
        ]

    async def scalar(self, statement):
        self.statements.append(str(statement))
        return Business(id=BUSINESS_ID, name="Acme", slug="acme", business_type="retail", status="active", timezone="UTC", currency="USD", locale="en", created_at=NOW, updated_at=NOW)

    async def execute(self, statement):
        self.statements.append(str(statement))
        return self.results.pop(0)


def _campaign(status: str) -> Campaign:
    return Campaign(id=uuid4(), business_id=BUSINESS_ID, marketing_plan_id=None, audience_id=None, name="Summer", objective="Grow sales", description=None, offer=None, audience_definition="Customers", geographic_targeting=[], channels=["instagram"], start_date=None, end_date=None, planned_budget=Decimal("2000"), currency="USD", budget_mode="lifetime", status=status, created_by_user_id=USER_ID, ai_generated=False, created_at=NOW, updated_at=NOW)
