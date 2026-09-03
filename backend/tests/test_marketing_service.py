from __future__ import annotations

import json
import os
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.marketing import MarketingAIError, MarketingStateError, MarketingValidationError  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.business import Business  # noqa: E402
from app.models.catalog_item import CatalogItem  # noqa: E402
from app.models.marketing import Campaign, CampaignChannelPlan, Competitor, CompetitorObservation, CreativeAsset, MarketingContent, MarketingPlan, MarketingTrend, SocialSchedule  # noqa: E402
from app.models.opportunity import Opportunity  # noqa: E402
from app.schemas.marketing import CampaignCreate, CampaignGenerateRequest, ChannelPlanCreate, ContentCreate, ContentGenerateRequest, ContentVersionCreate, CreativeBriefCreate, PerformanceCreate, PlanGenerateRequest, ScheduleCreate, TrendOpportunityRequest  # noqa: E402
from app.services.creative_provider import (
    CreativeGenerationResult,
    CreativeProviderGenerationError,
    CreativeProviderNotConfiguredError,
)  # noqa: E402
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
    generate_content,
    generate_creative_asset,
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
        parent = MarketingContent(
            id=root_id,
            business_id=BUSINESS_ID,
            campaign_id=None,
            channel="instagram",
            content_type="social_post",
            title="Original",
            body="Original body",
            cta="Shop now",
            language="en",
            status="approved",
            ai_generated=True,
            version=1,
            parent_content_id=None,
            root_content_id=root_id,
            created_by_user_id=USER_ID,
            creative_brief="Use the saved blue and gold brand direction.",
            generation_reasoning="Lead with the strongest supported product benefit.",
            recommended_for="Instagram product launch",
            source_evidence=[
                {
                    "classification": "trusted_context_assembly",
                    "source_type": "business_brain_and_permitted_memory",
                    "source_id": "a" * 64,
                    "summary": "Runtime assembled trusted business context.",
                    "provenance_role": "provided_to_model",
                }
            ],
            proposal_key="original-proposal",
            created_at=NOW,
            updated_at=NOW,
        )
        session = _ScalarSession([parent, 1])

        child = await create_content_version(
            session,
            business_id=BUSINESS_ID,
            content_id=parent.id,
            actor_user_id=USER_ID,
            data=ContentVersionCreate(
                title="Edited",
                body="Edited body",
                cta="Explore now",
            ),
        )

        # Prior version remains immutable.
        self.assertEqual(parent.title, "Original")
        self.assertEqual(parent.body, "Original body")
        self.assertEqual(parent.cta, "Shop now")

        # Manual edit becomes a distinct version in the same lineage.
        self.assertEqual(child.title, "Edited")
        self.assertEqual(child.body, "Edited body")
        self.assertEqual(child.cta, "Explore now")
        self.assertEqual(child.version, 2)
        self.assertEqual(child.parent_content_id, parent.id)
        self.assertEqual(child.root_content_id, parent.root_content_id)
        self.assertFalse(child.ai_generated)

        # Trusted CMO context survives the edit.
        self.assertEqual(child.creative_brief, parent.creative_brief)
        self.assertEqual(
            child.generation_reasoning,
            parent.generation_reasoning,
        )
        self.assertEqual(child.recommended_for, parent.recommended_for)
        self.assertEqual(child.source_evidence, parent.source_evidence)
        self.assertIsNot(child.source_evidence, parent.source_evidence)

        # Idempotency identity must never be inherited by a new version.
        self.assertIsNone(child.proposal_key)

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
        with patch("app.services.marketing._run_cmo", new=AsyncMock(return_value=output)) as runtime, patch(
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

    async def test_ai_campaign_persists_selected_product_context_without_lazy_loading(self) -> None:
        business = Business(id=BUSINESS_ID, name="Acme", slug="acme", business_type="retail", status="active", timezone="UTC", currency="USD", locale="en", created_at=NOW, updated_at=NOW)
        product = CatalogItem(
            id=uuid4(), business_id=BUSINESS_ID, item_type="product",
            name="Premium Farm Eggs", status="active", source="shopify",
            sync_state="in_sync", availability="in_stock", published=True,
        )
        session = _ScalarSession([business], rows=[[product]])
        output = SimpleNamespace(summary="Grounded plan", recommendations=["Lead with observed quality"], proposed_actions=[])
        audience = SimpleNamespace(
            id=uuid4(), preferred_channels=["instagram"], summary="Observed buyers",
            evidence=[], confidence=Decimal("0.650"), geographic_areas=[], campaign_id=None,
        )
        with patch("app.services.marketing._run_cmo", new=AsyncMock(return_value=output)) as runtime, patch(
            "app.services.marketing.build_audience_hypothesis", new=AsyncMock(return_value=audience),
        ):
            campaign = await generate_campaign(
                session, business_id=BUSINESS_ID, actor_user_id=USER_ID,
                data=CampaignGenerateRequest(
                    goal="Promote selected product", catalog_item_ids=[product.id],
                    channels=["instagram"],
                ),
                provider=SimpleNamespace(),
            )
        self.assertEqual(campaign.catalog_item_ids, [product.id])
        self.assertEqual(
            [item.catalog_item_id for item in campaign.product_selections],
            [product.id],
        )
        self.assertIn("source=shopify", runtime.await_args.args[2])
        self.assertIn("availability=in_stock", runtime.await_args.args[2])

    async def test_creative_intelligence_turns_weak_request_into_structured_strategy(self) -> None:
        execution = SimpleNamespace(
            context_revision="b" * 64,
            business_brain_source_count=4,
            memory_source_count=1,
            output=SimpleNamespace(
                summary=json.dumps(
                    {
                        "marketing_goal": "Increase qualified interest in the featured product.",
                        "target_audience": "Customers interested in the business's active product range.",
                        "audience_insight": "Lead with clear product value rather than unsupported urgency.",
                        "campaign_angle": "A polished product-first introduction grounded in the saved brand.",
                        "hook": "Meet the product designed for your next everyday upgrade.",
                        "headline": "Made to stand out.",
                        "supporting_message": "Present the product clearly with confident, concise brand-led messaging.",
                        "cta": "Explore the product",
                        "visual_concept": "Premium editorial product scene with a strong central subject and clean environmental styling.",
                        "composition_direction": "Place the product as the dominant visual anchor and reserve a clean text zone opposite the subject.",
                        "subject_focus": "The supported catalog product is the hero.",
                        "mood": "Premium, confident and contemporary.",
                        "lighting": "Soft directional studio light with controlled contrast.",
                        "negative_space": "Reserve generous uncluttered space for exact brand typography and CTA.",
                        "brand_treatment": "Use the saved brand palette as compositional accents; the real logo is added later by the application.",
                        "recommended_channel": "instagram",
                        "pr_guardrails": [
                            "Use only supported product benefits.",
                            "Avoid fabricated scarcity or social proof.",
                        ],
                        "prohibited_claims": [
                            "No invented discounts.",
                            "No unsupported performance claims.",
                        ],
                        "evidence_source_ids": [],
                    }
                ),
                recommendations=[],
                proposed_actions=[],
            ),
        )

        content = MarketingContent(
            id=uuid4(),
            business_id=BUSINESS_ID,
            campaign_id=None,
            channel="instagram",
            content_type="social_post",
            title="Product launch",
            body="A grounded product launch post.",
            cta="Explore now",
            language="en",
            status="draft",
            ai_generated=True,
            version=1,
            parent_content_id=None,
            root_content_id=uuid4(),
            created_by_user_id=USER_ID,
            creative_brief="Premium product-led creative.",
            created_at=NOW,
            updated_at=NOW,
        )

        session = _ScalarSession([content])

        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ) as runtime:
            asset = await create_creative_brief(
                session,
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=CreativeBriefCreate(
                    content_id=content.id,
                    asset_type="social_square",
                    instructions="make post for my product",
                    aspect_ratio="1:1",
                ),
                provider=SimpleNamespace(),
            )

        self.assertIsInstance(asset, CreativeAsset)
        self.assertEqual(asset.source_type, "ai_brief")
        self.assertEqual(asset.generation_status, "brief_ready")
        self.assertIsNone(asset.storage_reference)

        strategy = json.loads(asset.visual_direction)
        self.assertEqual(
            strategy["marketing_goal"],
            "Increase qualified interest in the featured product.",
        )
        self.assertEqual(strategy["recommended_channel"], "instagram")
        self.assertEqual(strategy["headline"], "Made to stand out.")
        self.assertIn("negative_space", strategy)
        self.assertIn("pr_guardrails", strategy)
        self.assertNotIn("evidence_source_ids", strategy)

        prompt = runtime.await_args.args[2]
        self.assertIn("may provide a very short, vague", prompt)
        self.assertIn("do not require expert prompting", prompt.lower())
        self.assertIn("Authoritative content context", prompt)
        self.assertIn("A grounded product launch post.", prompt)
        self.assertIn("Never include hidden reasoning", prompt)

    async def test_creative_intelligence_rejects_wrong_content_channel(self) -> None:
        execution = SimpleNamespace(
            context_revision="c" * 64,
            business_brain_source_count=2,
            memory_source_count=0,
            output=SimpleNamespace(
                summary=json.dumps(
                    {
                        "marketing_goal": "Promote the product.",
                        "target_audience": "Relevant customers.",
                        "audience_insight": "Keep the message clear.",
                        "campaign_angle": "Product-first.",
                        "hook": "Discover it.",
                        "headline": "Discover more.",
                        "supporting_message": "Grounded supporting copy.",
                        "cta": "Explore",
                        "visual_concept": "Clean product scene.",
                        "composition_direction": "Product left, copy area right.",
                        "subject_focus": "Product.",
                        "mood": "Premium.",
                        "lighting": "Soft studio lighting.",
                        "negative_space": "Clear copy area.",
                        "brand_treatment": "Use saved brand accents.",
                        "recommended_channel": "facebook",
                        "pr_guardrails": [],
                        "prohibited_claims": [],
                        "evidence_source_ids": [],
                    }
                ),
                recommendations=[],
                proposed_actions=[],
            ),
        )

        content = MarketingContent(
            id=uuid4(),
            business_id=BUSINESS_ID,
            campaign_id=None,
            channel="instagram",
            content_type="social_post",
            title="Launch",
            body="Grounded body",
            cta=None,
            language="en",
            status="draft",
            ai_generated=True,
            version=1,
            parent_content_id=None,
            root_content_id=uuid4(),
            created_by_user_id=USER_ID,
            created_at=NOW,
            updated_at=NOW,
        )

        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ):
            with self.assertRaises(MarketingAIError):
                await create_creative_brief(
                    _ScalarSession([content]),
                    business_id=BUSINESS_ID,
                    actor_user_id=USER_ID,
                    data=CreativeBriefCreate(
                        content_id=content.id,
                        asset_type="social_square",
                        instructions="make it beautiful",
                    ),
                    provider=SimpleNamespace(),
                )

    async def test_creative_intelligence_rejects_model_invented_evidence(self) -> None:
        execution = SimpleNamespace(
            context_revision="d" * 64,
            business_brain_source_count=1,
            memory_source_count=0,
            output=SimpleNamespace(
                summary=json.dumps(
                    {
                        "marketing_goal": "Promote the business.",
                        "target_audience": "Relevant customers.",
                        "audience_insight": "Use trusted context.",
                        "campaign_angle": "Brand-led.",
                        "hook": "Explore.",
                        "headline": "Explore.",
                        "supporting_message": "Supported copy.",
                        "cta": None,
                        "visual_concept": "Clean commercial scene.",
                        "composition_direction": "Balanced composition.",
                        "subject_focus": "Supported business offering.",
                        "mood": "Professional.",
                        "lighting": "Natural soft light.",
                        "negative_space": "Reserve copy space.",
                        "brand_treatment": "Use saved brand direction.",
                        "recommended_channel": "channel_agnostic",
                        "pr_guardrails": [],
                        "prohibited_claims": [],
                        "evidence_source_ids": ["invented-source"],
                    }
                ),
                recommendations=[],
                proposed_actions=[],
            ),
        )

        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ):
            with self.assertRaises(MarketingAIError):
                await create_creative_brief(
                    _ScalarSession([]),
                    business_id=BUSINESS_ID,
                    actor_user_id=USER_ID,
                    data=CreativeBriefCreate(
                        asset_type="social_square",
                        instructions="make a post",
                    ),
                    provider=SimpleNamespace(),
                )

    async def test_creative_intelligence_rejects_actions_from_model(self) -> None:
        execution = SimpleNamespace(
            context_revision="e" * 64,
            business_brain_source_count=2,
            memory_source_count=0,
            output=SimpleNamespace(
                summary=json.dumps(
                    {
                        "marketing_goal": "Promote the business.",
                        "target_audience": "Relevant customers.",
                        "audience_insight": "Use a clear benefit-led message.",
                        "campaign_angle": "Brand-led.",
                        "hook": "Discover more.",
                        "headline": "Discover more.",
                        "supporting_message": "Grounded supporting copy.",
                        "cta": "Explore",
                        "visual_concept": "Premium commercial scene.",
                        "composition_direction": "Strong hero subject and copy zone.",
                        "subject_focus": "Supported offering.",
                        "mood": "Confident.",
                        "lighting": "Soft directional light.",
                        "negative_space": "Reserve clean overlay space.",
                        "brand_treatment": "Use trusted brand colors.",
                        "recommended_channel": "channel_agnostic",
                        "pr_guardrails": [],
                        "prohibited_claims": [],
                        "evidence_source_ids": [],
                    }
                ),
                recommendations=[],
                proposed_actions=[SimpleNamespace(action_type="publish_social_post")],
            ),
        )

        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ):
            with self.assertRaises(MarketingAIError):
                await create_creative_brief(
                    _ScalarSession([]),
                    business_id=BUSINESS_ID,
                    actor_user_id=USER_ID,
                    data=CreativeBriefCreate(
                        asset_type="social_square",
                        instructions="promote my business",
                    ),
                    provider=SimpleNamespace(),
                )

    async def test_creative_generation_persists_ready_only_after_provider_success(self) -> None:
        strategy = {
            "marketing_goal": "Increase qualified product interest.",
            "target_audience": "Relevant customers.",
            "audience_insight": "Lead with a clear supported benefit.",
            "campaign_angle": "Premium product-first launch.",
            "hook": "Discover the product.",
            "headline": "Exact headline must not enter raw image generation.",
            "supporting_message": "Exact supporting copy.",
            "cta": "Explore now",
            "visual_concept": "Editorial product scene with a strong hero subject.",
            "composition_direction": "Hero subject left with clean open space right.",
            "subject_focus": "The supported catalog product.",
            "mood": "Premium and contemporary.",
            "lighting": "Soft directional studio lighting.",
            "negative_space": "Generous clear area for later typography.",
            "brand_treatment": "Use saved palette cues without drawing the logo.",
            "recommended_channel": "instagram",
            "pr_guardrails": ["Use only supported claims."],
            "prohibited_claims": ["No invented discounts."],
        }

        asset = CreativeAsset(
            id=uuid4(),
            business_id=BUSINESS_ID,
            campaign_id=None,
            content_id=None,
            asset_type="social_square",
            source_type="ai_brief",
            instructions="make a post",
            visual_direction=json.dumps(strategy),
            generation_status="brief_ready",
            storage_reference=None,
            width=1080,
            height=1080,
            aspect_ratio="1:1",
            alt_text=None,
            created_at=NOW,
            updated_at=NOW,
        )

        result = CreativeGenerationResult(
            storage_reference="https://media.example.com/generated.png",
            width=1024,
            height=1024,
            provider_request_id="req_123",
        )
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(return_value=result),
        )

        generated = await generate_creative_asset(
            _ScalarSession([asset]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
        )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(generated.source_type, "future_provider")
        self.assertEqual(
            generated.storage_reference,
            "https://media.example.com/generated.png",
        )
        self.assertEqual(generated.width, 1024)
        self.assertEqual(generated.height, 1024)

        request = provider.generate_draft.await_args.args[0]
        self.assertEqual(request.business_id, BUSINESS_ID)
        self.assertEqual(request.creative_asset_id, asset.id)
        self.assertIn("Editorial product scene", request.instructions)
        self.assertIn("Premium product-first launch", request.instructions)

        # Exact customer-facing typography is deliberately withheld from the
        # raw image model and will be placed by the deterministic compositor.
        self.assertNotIn(strategy["headline"], request.instructions)
        self.assertNotIn(strategy["supporting_message"], request.instructions)
        self.assertNotIn(strategy["cta"], request.instructions)

    async def test_creative_generation_persists_provider_required_when_unconfigured(self) -> None:
        strategy = {
            "marketing_goal": "Promote the business.",
            "target_audience": "Relevant customers.",
            "audience_insight": "Keep the direction trustworthy.",
            "campaign_angle": "Brand-led awareness.",
            "hook": "Discover more.",
            "headline": "Discover more",
            "supporting_message": "Grounded supporting copy.",
            "cta": None,
            "visual_concept": "Clean commercial brand scene.",
            "composition_direction": "Balanced subject with open copy space.",
            "subject_focus": "Supported business offering.",
            "mood": "Professional.",
            "lighting": "Soft natural light.",
            "negative_space": "Open right-side area.",
            "brand_treatment": "Use saved brand palette cues.",
            "recommended_channel": "channel_agnostic",
            "pr_guardrails": [],
            "prohibited_claims": [],
        }

        asset = CreativeAsset(
            id=uuid4(),
            business_id=BUSINESS_ID,
            campaign_id=None,
            content_id=None,
            asset_type="social_square",
            source_type="ai_brief",
            instructions="promote business",
            visual_direction=json.dumps(strategy),
            generation_status="brief_ready",
            storage_reference=None,
            width=None,
            height=None,
            aspect_ratio="1:1",
            alt_text=None,
            created_at=NOW,
            updated_at=NOW,
        )

        provider = SimpleNamespace(
            provider_name="unconfigured",
            generate_draft=AsyncMock(
                side_effect=CreativeProviderNotConfiguredError(
                    "not configured"
                )
            ),
        )

        generated = await generate_creative_asset(
            _ScalarSession([asset]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
        )

        self.assertEqual(generated.generation_status, "provider_required")
        self.assertIsNone(generated.storage_reference)
        self.assertEqual(generated.source_type, "ai_brief")

    async def test_creative_generation_persists_failed_provider_result(self) -> None:
        strategy = {
            "marketing_goal": "Promote the business.",
            "target_audience": "Relevant customers.",
            "audience_insight": "Use defensible messaging.",
            "campaign_angle": "Product-first awareness.",
            "hook": "Explore.",
            "headline": "Explore",
            "supporting_message": "Supported copy.",
            "cta": "Learn more",
            "visual_concept": "Premium minimal product environment.",
            "composition_direction": "Hero subject with strong negative space.",
            "subject_focus": "Supported product.",
            "mood": "Premium.",
            "lighting": "Controlled soft light.",
            "negative_space": "Clear typography zone.",
            "brand_treatment": "Use saved palette cues only.",
            "recommended_channel": "instagram",
            "pr_guardrails": [],
            "prohibited_claims": [],
        }

        asset = CreativeAsset(
            id=uuid4(),
            business_id=BUSINESS_ID,
            campaign_id=None,
            content_id=None,
            asset_type="social_square",
            source_type="ai_brief",
            instructions="create visual",
            visual_direction=json.dumps(strategy),
            generation_status="brief_ready",
            storage_reference=None,
            width=1024,
            height=1024,
            aspect_ratio="1:1",
            alt_text=None,
            created_at=NOW,
            updated_at=NOW,
        )

        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                side_effect=CreativeProviderGenerationError(
                    "provider failure"
                )
            ),
        )

        generated = await generate_creative_asset(
            _ScalarSession([asset]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
        )

        self.assertEqual(generated.generation_status, "failed")
        self.assertIsNone(generated.storage_reference)
        self.assertEqual(generated.source_type, "ai_brief")

    async def test_ready_creative_cannot_be_overwritten_by_generation_retry(self) -> None:
        asset = CreativeAsset(
            id=uuid4(),
            business_id=BUSINESS_ID,
            campaign_id=None,
            content_id=None,
            asset_type="social_square",
            source_type="future_provider",
            instructions="already generated",
            visual_direction="{}",
            generation_status="ready",
            storage_reference="https://media.example.com/existing.png",
            width=1024,
            height=1024,
            aspect_ratio="1:1",
            alt_text=None,
            created_at=NOW,
            updated_at=NOW,
        )

        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(),
        )

        with self.assertRaises(MarketingStateError):
            await generate_creative_asset(
                _ScalarSession([asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=provider,
            )

        provider.generate_draft.assert_not_awaited()
        self.assertEqual(
            asset.storage_reference,
            "https://media.example.com/existing.png",
        )

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

    async def test_plan_generation_bounds_and_deduplicates_ai_measurement_goals(self) -> None:
        business = Business(id=BUSINESS_ID, name="Acme", slug="acme", business_type="retail", status="active", timezone="UTC", currency="USD", locale="en", created_at=NOW, updated_at=NOW)
        session = _ScalarSession([business])
        long_goal = "Measure qualified conversions against the campaign objective and report only observed results. " * 3
        output = SimpleNamespace(
            summary="Grounded positioning",
            recommendations=[f"Strategy {index}" for index in range(8)] + [long_goal, long_goal],
        )
        with patch("app.services.marketing._run_cmo", new=AsyncMock(return_value=output)):
            plan = await generate_plan(
                session,
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=PlanGenerateRequest(
                    goal="Summer launch",
                    target_audience="Existing customers",
                    channels=["instagram"],
                    budget_guidance="2000",
                ),
                provider=SimpleNamespace(),
            )
        self.assertEqual(len(plan.measurement_goals), 1)
        self.assertLessEqual(len(plan.measurement_goals[0]), 160)

    async def test_ai_content_generation_persists_structured_grounded_metadata(self) -> None:
        execution = SimpleNamespace(
            context_revision="a" * 64,
            business_brain_source_count=3,
            memory_source_count=2,
            output=SimpleNamespace(
                summary=json.dumps({
                    "title": "Generated launch title",
                    "body": "A grounded Instagram post based on trusted business context.",
                    "cta": "Explore the collection",
                    "creative_brief": "Use the saved brand palette with a clean product-led composition.",
                    "recommended_channel": "instagram",
                    "generation_reasoning": "Lead with the product benefit while preserving the established brand tone.",
                    "evidence_source_ids": [],
                }),
                recommendations=[],
                proposed_actions=[],
            ),
        )
        session = _ScalarSession([])

        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ) as runtime:
            content = await generate_content(
                session,
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=ContentGenerateRequest(
                    prompt="Promote our new collection",
                    channel="instagram",
                    content_type="social_post",
                    title="Owner launch title",
                    language="en",
                ),
                provider=SimpleNamespace(),
            )

        self.assertTrue(content.ai_generated)
        self.assertEqual(content.version, 1)
        self.assertEqual(content.title, "Owner launch title")
        self.assertEqual(
            content.body,
            "A grounded Instagram post based on trusted business context.",
        )
        self.assertEqual(content.cta, "Explore the collection")
        self.assertEqual(
            content.creative_brief,
            "Use the saved brand palette with a clean product-led composition.",
        )
        self.assertEqual(
            content.generation_reasoning,
            "Lead with the product benefit while preserving the established brand tone.",
        )
        self.assertEqual(content.recommended_for, "instagram social post")

        self.assertEqual(len(content.source_evidence), 1)
        evidence = content.source_evidence[0]
        self.assertEqual(evidence["classification"], "trusted_context_assembly")
        self.assertEqual(
            evidence["source_type"],
            "business_brain_and_permitted_memory",
        )
        self.assertEqual(evidence["source_id"], "a" * 64)
        self.assertEqual(evidence["provenance_role"], "provided_to_model")
        self.assertIn("3 Business Brain", evidence["summary"])
        self.assertIn("2 permitted memory", evidence["summary"])

        task = runtime.await_args.args[2]
        self.assertIn("trusted Business Brain and permitted memory", task)
        self.assertIn("recommended_channel must be exactly instagram", task)
        self.assertIn(
            "Do not send, schedule, approve, or publish anything",
            task,
        )

        self.assertFalse(
            any(type(item).__name__ == "AIAction" for item in session.added)
        )
        self.assertFalse(
            any(isinstance(item, SocialSchedule) for item in session.added)
        )

    async def test_ai_content_generation_rejects_wrong_generated_channel(self) -> None:
        execution = SimpleNamespace(
            context_revision="b" * 64,
            business_brain_source_count=2,
            memory_source_count=1,
            output=SimpleNamespace(
                summary=json.dumps({
                    "title": "Generated title",
                    "body": "Grounded copy.",
                    "cta": None,
                    "creative_brief": "Use the brand identity.",
                    "recommended_channel": "facebook",
                    "generation_reasoning": "A short user-visible rationale.",
                    "evidence_source_ids": [],
                }),
                recommendations=[],
                proposed_actions=[],
            ),
        )
        session = _ScalarSession([])

        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ):
            with self.assertRaises(MarketingAIError):
                await generate_content(
                    session,
                    business_id=BUSINESS_ID,
                    actor_user_id=USER_ID,
                    data=ContentGenerateRequest(
                        prompt="Create an Instagram post",
                        channel="instagram",
                        content_type="social_post",
                    ),
                    provider=SimpleNamespace(),
                )

        self.assertFalse(
            any(isinstance(item, MarketingContent) for item in session.added)
        )

    async def test_ai_content_generation_rejects_malformed_structured_output(self) -> None:
        execution = SimpleNamespace(
            context_revision="c" * 64,
            business_brain_source_count=1,
            memory_source_count=0,
            output=SimpleNamespace(
                summary="This is not structured JSON.",
                recommendations=[],
                proposed_actions=[],
            ),
        )
        session = _ScalarSession([])

        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ):
            with self.assertRaises(MarketingAIError):
                await generate_content(
                    session,
                    business_id=BUSINESS_ID,
                    actor_user_id=USER_ID,
                    data=ContentGenerateRequest(
                        prompt="Create a post",
                        channel="instagram",
                        content_type="social_post",
                    ),
                    provider=SimpleNamespace(),
                )

        self.assertFalse(
            any(isinstance(item, MarketingContent) for item in session.added)
        )

    async def test_ai_content_generation_rejects_untrusted_evidence_ids(self) -> None:
        execution = SimpleNamespace(
            context_revision="d" * 64,
            business_brain_source_count=2,
            memory_source_count=0,
            output=SimpleNamespace(
                summary=json.dumps({
                    "title": "Generated title",
                    "body": "Grounded copy.",
                    "cta": None,
                    "creative_brief": "Use the brand identity.",
                    "recommended_channel": "instagram",
                    "generation_reasoning": "A short user-visible rationale.",
                    "evidence_source_ids": ["model-invented-source"],
                }),
                recommendations=[],
                proposed_actions=[],
            ),
        )
        session = _ScalarSession([])

        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ):
            with self.assertRaises(MarketingAIError):
                await generate_content(
                    session,
                    business_id=BUSINESS_ID,
                    actor_user_id=USER_ID,
                    data=ContentGenerateRequest(
                        prompt="Create a post",
                        channel="instagram",
                        content_type="social_post",
                    ),
                    provider=SimpleNamespace(),
                )

        self.assertFalse(
            any(isinstance(item, MarketingContent) for item in session.added)
        )

    async def test_ai_content_generation_rejects_model_proposed_actions(self) -> None:
        execution = SimpleNamespace(
            context_revision="e" * 64,
            business_brain_source_count=2,
            memory_source_count=0,
            output=SimpleNamespace(
                summary=json.dumps({
                    "title": "Generated title",
                    "body": "Grounded copy.",
                    "cta": None,
                    "creative_brief": "Use the brand identity.",
                    "recommended_channel": "instagram",
                    "generation_reasoning": "A short user-visible rationale.",
                    "evidence_source_ids": [],
                }),
                recommendations=[],
                proposed_actions=[
                    SimpleNamespace(action_type="publish_social_post"),
                ],
            ),
        )
        session = _ScalarSession([])

        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ):
            with self.assertRaises(MarketingAIError):
                await generate_content(
                    session,
                    business_id=BUSINESS_ID,
                    actor_user_id=USER_ID,
                    data=ContentGenerateRequest(
                        prompt="Create a post",
                        channel="instagram",
                        content_type="social_post",
                    ),
                    provider=SimpleNamespace(),
                )

        self.assertFalse(
            any(isinstance(item, MarketingContent) for item in session.added)
        )

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
        self.assertEqual(opportunity.source_entity_type, "marketing_trend")
        self.assertEqual(opportunity.source_entity_id, trend.id)
        self.assertEqual(opportunity.confidence, trend.confidence)
        self.assertEqual(opportunity.suggested_action, "generate_campaign_proposal")
        self.assertEqual(opportunity.provenance[0]["source_id"], str(trend.id))

    async def test_descriptive_marketing_totals_cannot_create_durable_learning(self) -> None:
        analytics = SimpleNamespace(impressions=5000, conversions=25, channels=[SimpleNamespace(label="email", roas=Decimal("3"), conversions=15, clicks=100), SimpleNamespace(label="instagram", roas=Decimal("2"), conversions=10, clicks=200)])
        with patch("app.services.marketing.marketing_analytics", new=AsyncMock(return_value=analytics)) as aggregate:
            result = await learn_from_performance(SimpleNamespace(), business_id=BUSINESS_ID, period_start=date(2026, 8, 1), period_end=date(2026, 8, 7))
        self.assertFalse(result.created)
        self.assertIsNone(result.memory_id)
        self.assertIn("descriptive only", result.conclusion)
        self.assertIn("governed growth experiment", result.conclusion)
        aggregate.assert_awaited_once()


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
