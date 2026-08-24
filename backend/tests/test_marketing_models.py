from __future__ import annotations

import os
import asyncio
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Numeric

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.domain.marketing import CAMPAIGN_TRANSITIONS, CONTENT_TRANSITIONS, MARKETING_PLAN_TRANSITIONS, TREND_TRANSITIONS  # noqa: E402
from app.models.marketing import (  # noqa: E402
    Campaign,
    CampaignChannelPlan,
    Competitor,
    CompetitorAnalysis,
    CompetitorObservation,
    CreativeAsset,
    MarketingAudience,
    MarketingContent,
    MarketingPerformance,
    MarketingPlan,
    MarketingTrend,
    SocialSchedule,
)
from app.schemas.marketing import AudienceCreate, CampaignCreate, CampaignUpdate, ChannelConfiguration, MarketingPlanUpdate, PerformanceCreate, ScheduleCreate  # noqa: E402
from app.schemas.operations import ReportGenerateRequest  # noqa: E402
from app.services.action_registry import ACTION_REGISTRY  # noqa: E402
from app.services.creative_provider import CreativeGenerationRequest, CreativeProviderNotConfiguredError, UnavailableCreativeGenerationProvider  # noqa: E402


class MarketingModelTests(unittest.TestCase):
    def test_all_marketing_tables_are_registered_and_tenant_owned(self) -> None:
        expected = {
            MarketingAudience: "marketing_audiences", MarketingPlan: "marketing_plans",
            Campaign: "marketing_campaigns", CampaignChannelPlan: "campaign_channel_plans",
            MarketingContent: "marketing_content", CreativeAsset: "marketing_creative_assets",
            SocialSchedule: "social_content_schedules", Competitor: "marketing_competitors",
            CompetitorObservation: "competitor_observations", CompetitorAnalysis: "competitor_analyses",
            MarketingTrend: "marketing_trends", MarketingPerformance: "marketing_performance",
        }
        for model, table in expected.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(model.__tablename__, table)
                self.assertIn("business_id", model.__table__.columns)
                self.assertTrue(any(isinstance(item, CheckConstraint) for item in model.__table__.constraints))

    def test_cross_domain_marketing_references_are_composite_tenant_keys(self) -> None:
        for model in (MarketingPlan, Campaign, CampaignChannelPlan, MarketingContent, CreativeAsset, SocialSchedule, CompetitorObservation, CompetitorAnalysis, MarketingTrend, MarketingPerformance):
            with self.subTest(model=model.__name__):
                self.assertTrue(any(isinstance(item, ForeignKeyConstraint) and len(item.column_keys) == 2 for item in model.__table__.constraints))

    def test_money_and_derived_metrics_use_fixed_precision(self) -> None:
        for column in (Campaign.__table__.c.planned_budget, CampaignChannelPlan.__table__.c.budget_allocation):
            self.assertIsInstance(column.type, Numeric)
            self.assertEqual((column.type.precision, column.type.scale), (14, 2))
        for column in (MarketingPerformance.__table__.c.spend, MarketingPerformance.__table__.c.revenue):
            self.assertEqual((column.type.precision, column.type.scale), (16, 4))

    def test_models_exclude_secret_sensitive_clinical_and_payment_fields(self) -> None:
        forbidden = {"api_key", "access_token", "oauth_token", "authorization", "diagnosis", "medical_history", "prescription", "clinical_notes", "card_number", "payment_token"}
        for model in (MarketingAudience, MarketingPlan, Campaign, CampaignChannelPlan, MarketingContent, CreativeAsset, SocialSchedule, Competitor, CompetitorObservation, CompetitorAnalysis, MarketingTrend, MarketingPerformance):
            with self.subTest(model=model.__name__):
                self.assertTrue(forbidden.isdisjoint(model.__table__.columns.keys()))

    def test_lifecycles_are_explicit_and_terminal(self) -> None:
        self.assertEqual(CAMPAIGN_TRANSITIONS["completed"], frozenset())
        self.assertEqual(CAMPAIGN_TRANSITIONS["canceled"], frozenset())
        self.assertNotIn("active", CAMPAIGN_TRANSITIONS["draft"])
        self.assertEqual(CONTENT_TRANSITIONS["archived"], frozenset())
        self.assertEqual(MARKETING_PLAN_TRANSITIONS["archived"], frozenset())
        self.assertEqual(TREND_TRANSITIONS["dismissed"], frozenset())

    def test_future_external_action_types_already_exist_without_duplication(self) -> None:
        required = {
            "publish_social_post", "create_meta_campaign", "launch_meta_campaign",
            "create_google_ads_campaign", "launch_google_ads_campaign", "change_ad_budget",
            "pause_ad_campaign", "send_email", "send_whatsapp_message",
        }
        self.assertTrue(required.issubset(ACTION_REGISTRY.action_types))
        self.assertEqual(len(ACTION_REGISTRY.action_types), len(ACTION_REGISTRY.definitions))

    def test_creative_provider_foundation_defaults_to_explicitly_disabled(self) -> None:
        request = CreativeGenerationRequest(business_id=uuid4(), creative_asset_id=uuid4(), instructions="Create a grounded draft", width=1080, height=1080, aspect_ratio="1:1")

        async def execute() -> None:
            with self.assertRaises(CreativeProviderNotConfiguredError):
                await UnavailableCreativeGenerationProvider().generate_draft(request)

        asyncio.run(execute())


class MarketingSchemaTests(unittest.TestCase):
    def test_audience_is_generic_bounded_and_rejects_sensitive_targeting_fields(self) -> None:
        value = AudienceCreate(name="Active customers", countries=["us"], languages=["en"], interests=["local food"])
        self.assertEqual(value.countries, ["US"])
        for payload in (
            {"name": "A", "min_age": 17}, {"name": "A", "min_age": 50, "max_age": 30},
            {"name": "A", "diagnosis": "diabetes"}, {"name": "A", "countries": ["USA"]},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                AudienceCreate.model_validate(payload)

    def test_campaign_currency_and_totals_are_server_owned(self) -> None:
        base = {"name": "Launch", "objective": "Reach buyers", "audience_definition": "Existing customers", "channels": ["instagram"], "planned_budget": "2000.00"}
        value = CampaignCreate.model_validate(base)
        self.assertEqual(value.planned_budget, Decimal("2000.00"))
        for extra in ({"currency": "EUR"}, {"total_allocated": "1"}, {"external_account_id": "secret"}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                CampaignCreate.model_validate({**base, **extra})

    def test_partial_plan_and_campaign_updates_reject_duplicate_channels(self) -> None:
        for schema in (MarketingPlanUpdate, CampaignUpdate):
            with self.subTest(schema=schema.__name__), self.assertRaises(ValidationError):
                schema.model_validate({"channels": ["instagram", "instagram"]})

    def test_controlled_channel_configuration_rejects_provider_credentials(self) -> None:
        ChannelConfiguration(keywords=["summer"], optimization_goal="sales")
        with self.assertRaises(ValidationError):
            ChannelConfiguration(api_key="private")

    def test_performance_rejects_derived_metrics_and_impossible_counts(self) -> None:
        base = {"campaign_id": "51000000-0000-0000-0000-000000000001", "channel": "instagram", "period_start": "2026-08-01", "period_end": "2026-08-07", "impressions": 100, "reach": 80, "clicks": 10, "conversions": 2}
        PerformanceCreate.model_validate(base)
        for update in ({"ctr": "99"}, {"clicks": 101}, {"reach": 101}, {"conversions": 11}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                PerformanceCreate.model_validate({**base, **update})

    def test_schedule_requires_timezone_aware_instant(self) -> None:
        with self.assertRaises(ValidationError):
            ScheduleCreate(content_id="51000000-0000-0000-0000-000000000001", scheduled_for=datetime(2026, 8, 24, 10))
        ScheduleCreate(content_id="51000000-0000-0000-0000-000000000001", scheduled_for=datetime(2026, 8, 24, 10, tzinfo=UTC))

    def test_marketing_report_reuses_existing_report_contract(self) -> None:
        value = ReportGenerateRequest(report_type="marketing", period_start=date(2026, 8, 1), period_end=date(2026, 8, 23))
        self.assertEqual(value.report_type, "marketing")
