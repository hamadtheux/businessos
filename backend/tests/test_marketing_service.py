from __future__ import annotations

import json
import os
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from PIL import Image
from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.marketing import MarketingAIError, MarketingNotFoundError, MarketingPersistenceError, MarketingStateError, MarketingValidationError  # noqa: E402
from app.agents.provider import AIAgentProviderMetadata  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.business import Business  # noqa: E402
from app.models.business_branding import BusinessBranding  # noqa: E402
from app.models.catalog_item import CatalogItem  # noqa: E402
from app.models.marketing import Campaign, CampaignChannelPlan, Competitor, CompetitorObservation, CreativeAsset, MarketingContent, MarketingPlan, MarketingTrend, SocialSchedule  # noqa: E402
from app.models.opportunity import Opportunity  # noqa: E402
from app.schemas.ai_agent import (  # noqa: E402
    AIAgentProposedAction,
    MAX_AGENT_TASK_LENGTH,
)
from app.schemas.marketing import (
    CampaignCreate,
    CampaignGenerateRequest,
    CampaignUpdate,
    ChannelPlanCreate,
    ContentCreate,
    ContentGenerateRequest,
    ContentVersionCreate,
    CreativeStrategyProposal,
    PerformanceCreate,
    PlanGenerateRequest,
    ScheduleCreate,
    TrendOpportunityRequest,
)
from app.services.marketing import (
    _CTACapabilities,
    _CREATIVE_STRATEGY_RUNTIME_RULE_MARGIN,
    _CREATIVE_STRATEGY_TASK_BUDGET,
    _allocate_budget,
    _contains_creative_instruction_copy,
    _execute_creative_strategy,
    _creative_story_mode,
    _normalize_creative_display_cta,
    _normalize_generated_cta,
    _parse_owner_creative_intent,
    _page,
    _term,
    analyze_competitor,
    change_campaign_status,
    change_trend_status,
    create_channel_plan,
    create_campaign,
    create_content,
    create_content_version,
    create_schedule,
    derive_metrics,
    generate_campaign,
    generate_content,
    list_creative_assets,
    generate_plan,
    learn_from_performance,
    marketing_analytics,
    reschedule,
    trend_to_opportunity,
    unschedule,
    update_campaign,
    _run_cmo,
)
from app.storage.base import ObjectNotFoundError, ObjectStorage, StorageOperationError  # noqa: E402


BUSINESS_ID = uuid4()
USER_ID = uuid4()
NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


def _png_bytes(
    width: int = 1024,
    height: int = 1024,
    *,
    mode: str = "RGB",
) -> bytes:
    output = BytesIO()
    color = (100, 120, 140, 255) if mode == "RGBA" else (100, 120, 140)
    Image.new(mode, (width, height), color).save(output, format="PNG")
    return output.getvalue()


def _business_record() -> Business:
    return Business(
        id=BUSINESS_ID,
        name="Acme",
        slug="acme",
        business_type="retail",
        status="active",
        timezone="UTC",
        currency="USD",
        locale="en",
        created_at=NOW,
        updated_at=NOW,
    )


class _ObjectStorage:
    def __init__(
        self,
        *,
        get_content: bytes | None = None,
        put_error: Exception | None = None,
        public_url_error: Exception | None = None,
    ) -> None:
        self.get = AsyncMock(
            return_value=get_content if get_content is not None else _png_bytes(240, 80)
        )
        self.put = AsyncMock(side_effect=put_error)
        self.delete = AsyncMock()
        self.public_url_error = public_url_error

    def public_url(self, object_key: str) -> str:
        if self.public_url_error is not None:
            raise self.public_url_error
        return f"https://media.example.com/{object_key}"


class _DurableCheckpointStorage(ObjectStorage):
    def __init__(self, *, read_error: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.read_error = read_error

    async def put(self, object_key: str, content: bytes, content_type: str) -> None:
        self.objects[object_key] = content

    async def get(self, object_key: str, *, max_bytes: int) -> bytes:
        if self.read_error:
            raise StorageOperationError("storage unavailable")
        try:
            content = self.objects[object_key]
        except KeyError:
            raise ObjectNotFoundError("not found") from None
        if len(content) > max_bytes:
            raise StorageOperationError("too large")
        return content

    async def delete(self, object_key: str) -> None:
        self.objects.pop(object_key, None)

    def public_url(self, object_key: str) -> str:
        return f"https://media.example.com/{object_key}"


def _creative_strategy() -> dict[str, object]:
    return {
        "marketing_goal": "Increase qualified product interest.",
        "target_audience": "Relevant customers.",
        "audience_insight": "Lead with a clear supported benefit.",
        "campaign_angle": "Premium product-first launch.",
        "hook": "Discover the product.",
        "headline": "Made for the moment",
        "supporting_message": "A grounded, confident product story.",
        "cta": "Explore now",
        "visual_concept": "Editorial product scene with a strong hero subject.",
        "composition_direction": "Hero subject left with clean open space right.",
        "subject_focus": "The supported catalog product.",
        "mood": "Premium and contemporary.",
        "lighting": "Soft directional studio lighting.",
        "negative_space": "Generous clear area on the right for typography.",
        "brand_treatment": "Use saved palette cues without drawing the logo.",
        "recommended_channel": "instagram",
        "pr_guardrails": ["Use only supported claims."],
        "prohibited_claims": ["No invented discounts."],
    }


def _creative_asset(
    *,
    status: str = "brief_ready",
    business_id=BUSINESS_ID,
    source_type: str = "ai_brief",
    visual_direction: str | None = None,
) -> CreativeAsset:
    return CreativeAsset(
        id=uuid4(),
        business_id=business_id,
        campaign_id=None,
        content_id=None,
        asset_type="social_square",
        media_type="image",
        source_type=source_type,
        instructions="create a grounded product post",
        visual_direction=(
            json.dumps(_creative_strategy())
            if visual_direction is None
            else visual_direction
        ),
        generation_status=status,
        storage_reference=(
            "https://media.example.com/existing.png" if status == "ready" else None
        ),
        width=640,
        height=640,
        aspect_ratio="1:1",
        alt_text="Product campaign creative",
        created_at=NOW,
        updated_at=NOW,
    )




def _solid_png(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (640, 640), color).save(output, format="PNG")
    return output.getvalue()








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

    def test_creative_production_instructions_cannot_pass_as_customer_copy(self) -> None:
        self.assertTrue(
            _contains_creative_instruction_copy(
                "Create a premium scroll-stopping social media visual with the logo top right."
            )
        )
        self.assertFalse(
            _contains_creative_instruction_copy(
                "A practical product story for teams ready to work with confidence."
            )
        )

    def test_generated_cta_is_canonical_and_requires_action_support(self) -> None:
        self.assertIsNone(_normalize_generated_cta("click here"))
        self.assertEqual(_normalize_generated_cta("buy Now"), "Learn More")
        self.assertEqual(_normalize_generated_cta("shop now"), "Learn More")
        self.assertEqual(_normalize_generated_cta("Start Trial"), "Learn More")
        self.assertEqual(_normalize_generated_cta("Subscribe"), "Learn More")
        self.assertEqual(
            _normalize_generated_cta(
                "buy Now",
                capabilities=_CTACapabilities(can_shop=True),
            ),
            "Shop Now",
        )
        self.assertEqual(
            _normalize_generated_cta(
                "book now",
                capabilities=_CTACapabilities(can_book=True),
            ),
            "Book Now",
        )

    def test_owner_display_cta_preserves_safe_campaign_language(self) -> None:
        self.assertEqual(
            _normalize_creative_display_cta("Run Smarter"),
            "Run Smarter",
        )
        # Fulfillment-like labels remain subject to the existing capability gate.
        self.assertEqual(
            _normalize_creative_display_cta("Shop Now"),
            "Learn More",
        )

    def test_owner_intent_extracts_only_explicit_copy_locks(self) -> None:
        intent = _parse_owner_creative_intent(
            """Headline:
Run Your Business With One Clear Rhythm
Supporting copy: Bring marketing, sales, support, and operations into one connected way of working.
CTA: Run Smarter
Owner visual direction:
Show a real owner sorting scattered work into one controlled rhythm.
Explicitly avoid:
- glowing AI hub
- fake dashboards
"""
        )
        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(
            intent.locked_headline,
            "Run Your Business With One Clear Rhythm",
        )
        self.assertEqual(
            intent.locked_supporting_copy,
            "Bring marketing, sales, support, and operations into one connected way of working.",
        )
        self.assertEqual(intent.locked_cta, "Run Smarter")
        self.assertIn("glowing AI hub", intent.visual_exclusions)
        self.assertNotIn("Run Smarter", intent.visual_direction or "")



    async def test_creative_story_mode_is_tenant_campaign_and_catalog_scoped(self) -> None:
        no_campaign = _ScalarSession([])
        self.assertEqual(
            await _creative_story_mode(
                no_campaign,
                business_id=BUSINESS_ID,
                campaign_id=None,
            ),
            "brand_offer",
        )
        self.assertEqual(no_campaign.scalar_statements, [])

        campaign_id = uuid4()
        no_selection = _ScalarSession([None])
        self.assertEqual(
            await _creative_story_mode(
                no_selection,
                business_id=BUSINESS_ID,
                campaign_id=campaign_id,
            ),
            "brand_offer",
        )
        statement_text = str(no_selection.scalar_statements[0]).casefold()
        for required in (
            "marketing_campaigns",
            "campaign_product_selections",
            "catalog_items",
            "marketing_campaigns.business_id",
            "campaign_product_selections.business_id",
            "catalog_items.business_id",
            "catalog_items.status !=",
            "catalog_items.item_type in",
        ):
            self.assertIn(required, statement_text)

        # A wrong-tenant selection or archived item produces no selected id
        # under the scoped SQL above, and therefore cannot switch modes.
        for label in ("wrong tenant", "archived item"):
            with self.subTest(label=label):
                self.assertEqual(
                    await _creative_story_mode(
                        _ScalarSession([None]),
                        business_id=BUSINESS_ID,
                        campaign_id=campaign_id,
                    ),
                    "brand_offer",
                )

        for item_type in ("product", "service"):
            with self.subTest(item_type=item_type):
                self.assertEqual(
                    await _creative_story_mode(
                        _ScalarSession([uuid4()]),
                        business_id=BUSINESS_ID,
                        campaign_id=campaign_id,
                    ),
                    "offering_proof",
                )

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

    async def test_content_version_supports_each_editable_field_and_empty_cta(self) -> None:
        cases = (
            ("Edited title", "Original body", "Explore now"),
            ("Original title", "Edited body", "Explore now"),
            ("Original title", "Original body", "Start today"),
            ("Original title", "Original body", None),
        )
        for title, body, cta in cases:
            with self.subTest(title=title, body=body, cta=cta):
                root_id = uuid4()
                parent = MarketingContent(
                    id=root_id, business_id=BUSINESS_ID, campaign_id=None,
                    channel="instagram", content_type="social_post",
                    title="Original title", body="Original body", cta="Explore now",
                    language="en", status="approved", ai_generated=True, version=1,
                    parent_content_id=None, root_content_id=root_id,
                    created_by_user_id=USER_ID, source_evidence=[],
                    created_at=NOW, updated_at=NOW,
                )
                child = await create_content_version(
                    _ScalarSession([parent, 1]),
                    business_id=BUSINESS_ID,
                    content_id=parent.id,
                    actor_user_id=USER_ID,
                    data=ContentVersionCreate(title=title, body=body, cta=cta),
                )
                self.assertEqual((child.title, child.body, child.cta), (title, body, cta))
                self.assertEqual(
                    (parent.title, parent.body, parent.cta),
                    ("Original title", "Original body", "Explore now"),
                )
                self.assertEqual(child.status, "draft")
                self.assertEqual(child.version, 2)

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

    async def test_campaign_offer_requires_server_authorization_before_provider_use(self) -> None:
        data = CampaignGenerateRequest(
            goal="Promote the seasonal offer",
            channels=["instagram"],
            offer="50% off",
            offer_authorized=True,
        )
        session = _ScalarSession([])
        provider = SimpleNamespace()
        with (
            patch(
                "app.services.marketing.build_audience_hypothesis",
                new=AsyncMock(),
            ) as audience,
            patch("app.services.marketing._run_cmo", new=AsyncMock()) as runtime,
        ):
            with self.assertRaises(MarketingValidationError):
                await generate_campaign(
                    session,
                    business_id=BUSINESS_ID,
                    actor_user_id=USER_ID,
                    data=data,
                    provider=provider,
                )

        audience.assert_not_awaited()
        runtime.assert_not_awaited()
        self.assertEqual(session.added, [])

    async def test_owner_and_admin_campaign_offers_are_server_authorized(self) -> None:
        for role in ("owner", "admin"):
            with self.subTest(role=role):
                business = _business_record()
                audience = SimpleNamespace(
                    id=uuid4(),
                    preferred_channels=["instagram"],
                    summary="Qualified business owners",
                    evidence=[],
                    confidence=Decimal("0.650"),
                    geographic_areas=[],
                    campaign_id=None,
                )
                output = SimpleNamespace(
                    summary="Grounded campaign plan",
                    recommendations=["Product-led creative direction"],
                    proposed_actions=[],
                )
                session = _ScalarSession([business])
                with (
                    patch(
                        "app.services.marketing.build_audience_hypothesis",
                        new=AsyncMock(return_value=audience),
                    ),
                    patch(
                        "app.services.marketing._run_cmo",
                        new=AsyncMock(return_value=output),
                    ),
                ):
                    campaign = await generate_campaign(
                        session,
                        business_id=BUSINESS_ID,
                        actor_user_id=USER_ID,
                        data=CampaignGenerateRequest(
                            goal="Promote the seasonal offer",
                            channels=["instagram"],
                            offer="50% off",
                            offer_authorized=True,
                        ),
                        provider=SimpleNamespace(),
                        offer_authorization_role=role,
                    )

                self.assertEqual(campaign.offer, "50% off")
                self.assertEqual(campaign.offer_source, "owner_authorized")
                self.assertTrue(campaign.offer_authorized)
                self.assertEqual(
                    campaign.normalized_proposal["offer"],
                    {
                        "description": "50% off",
                        "source": "owner_authorized",
                        "approved": True,
                    },
                )

    async def test_campaign_offer_edit_cannot_inherit_prior_authorization(self) -> None:
        campaign = _campaign("draft")
        campaign.offer = "50% off"
        campaign.offer_source = "owner_authorized"
        campaign.offer_authorized = True

        updated = await update_campaign(
            _ScalarSession([campaign, Decimal("0")]),
            business_id=BUSINESS_ID,
            campaign_id=campaign.id,
            actor_user_id=USER_ID,
            data=CampaignUpdate(offer="60% off"),
        )

        self.assertEqual(updated.offer, "60% off")
        self.assertEqual(updated.offer_source, "none")
        self.assertFalse(updated.offer_authorized)

    async def test_manual_campaign_offer_is_persisted_as_untrusted_input(self) -> None:
        campaign = await create_campaign(
            _ScalarSession([_business_record()]),
            business_id=BUSINESS_ID,
            actor_user_id=USER_ID,
            data=CampaignCreate(
                name="Seasonal campaign",
                objective="Promote the seasonal offer",
                offer="50% off",
                audience_definition="Existing customers",
                channels=["instagram"],
            ),
        )

        self.assertEqual(campaign.offer, "50% off")
        self.assertEqual(campaign.offer_source, "none")
        self.assertFalse(campaign.offer_authorized)

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


    async def test_creative_strategy_runtime_uses_provider_draft_schema(self) -> None:
        execution = SimpleNamespace(output=object(), provider_metadata=None)

        with patch(
            "app.services.marketing.execute_ai_agent_typed_with_metadata",
            new=AsyncMock(return_value=execution),
        ) as runtime:
            result = await _execute_creative_strategy(
                _ScalarSession([]),
                BUSINESS_ID,
                "Build a grounded creative strategy.",
                SimpleNamespace(),
                expected_channel="instagram",
            )

        self.assertIs(result, execution)
        output_type = runtime.await_args.args[4]
        self.assertEqual(
            output_type.__name__,
            "_CreativeStrategyProviderProposal",
        )
        # Governance-sensitive provider values remain visible so the server
        # can explicitly reject attempted provenance/actions instead of
        # silently deleting them.
        self.assertIn("offer", output_type.model_fields)
        self.assertIn("claim_source", output_type.model_fields)
        self.assertIn("evidence_source_ids", output_type.model_fields)
        self.assertIn("recommendations", output_type.model_fields)
        self.assertIn("proposed_actions", output_type.model_fields)
        self.assertIn("recommended_channel", output_type.model_fields)





    async def test_creative_strategy_request_validation_maps_to_marketing_ai_error(self) -> None:
        provider = SimpleNamespace(provider_name="test")

        with patch(
            "app.services.marketing.execute_ai_agent_typed_with_metadata",
            new=AsyncMock(),
        ) as runtime:
            with self.assertRaises(MarketingAIError):
                await _execute_creative_strategy(
                    SimpleNamespace(),
                    BUSINESS_ID,
                    "x" * (MAX_AGENT_TASK_LENGTH + 1),
                    provider,
                    expected_channel="instagram",
                )

        runtime.assert_not_awaited()


















































    async def test_legacy_unstructured_brief_remains_listable(self) -> None:
        asset = _creative_asset(visual_direction="Legacy visual direction")

        result = await list_creative_assets(
            _ScalarSession([], rows=[[asset]]),
            business_id=BUSINESS_ID,
            campaign_id=None,
            content_id=None,
        )

        self.assertEqual(result, [asset])










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

    async def test_generated_copy_cannot_self_authorize_transactional_cta(self) -> None:
        execution = SimpleNamespace(
            context_revision="7" * 64,
            business_brain_source_count=1,
            memory_source_count=0,
            output=SimpleNamespace(
                summary=json.dumps({
                    "title": "Buy products today",
                    "body": "Shop our products in our store.",
                    "cta": "buy Now",
                    "offer": None,
                    "creative_brief": "Use a grounded product-led composition.",
                    "recommended_channel": "instagram",
                    "generation_reasoning": "Lead with a concise product story.",
                    "evidence_source_ids": [],
                }),
                recommendations=[],
                proposed_actions=[],
            ),
        )

        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ):
            content = await generate_content(
                _ScalarSession([]),
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=ContentGenerateRequest(
                    prompt="Make me a Buy Now campaign",
                    channel="instagram",
                    content_type="social_post",
                ),
                provider=SimpleNamespace(),
            )

        self.assertEqual(content.cta, "Learn More")

    async def test_structured_storefront_capability_allows_shop_cta(self) -> None:
        execution = SimpleNamespace(
            context_revision="8" * 64,
            business_brain_source_count=1,
            memory_source_count=0,
            output=SimpleNamespace(
                summary=json.dumps({
                    "title": "Explore the supported collection",
                    "body": "See the active products available from the storefront.",
                    "cta": "buy Now",
                    "offer": None,
                    "creative_brief": "Use a grounded product-led composition.",
                    "recommended_channel": "instagram",
                    "generation_reasoning": "Lead with a concise product story.",
                    "evidence_source_ids": [],
                }),
                recommendations=[],
                proposed_actions=[],
            ),
        )
        campaign = _campaign("draft")
        session = _ScalarSession([campaign, True], rows=[[uuid4()]])

        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ):
            content = await generate_content(
                session,
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=ContentGenerateRequest(
                    prompt="Promote the collection",
                    campaign_id=campaign.id,
                    channel="instagram",
                    content_type="social_post",
                ),
                provider=SimpleNamespace(),
            )

        self.assertEqual(content.cta, "Shop Now")
        self.assertEqual(len(session.scalars_statements), 1)
        capability_statement = session.scalars_statements[0]
        compiled = capability_statement.compile()
        self.assertIn(BUSINESS_ID, compiled.params.values())
        self.assertIn(campaign.id, compiled.params.values())
        self.assertIn("campaign_product_selections", str(capability_statement))

    async def test_unrelated_tenant_product_cannot_authorize_campaign_shop_cta(self) -> None:
        execution = SimpleNamespace(
            context_revision="9" * 64,
            business_brain_source_count=1,
            memory_source_count=0,
            output=SimpleNamespace(
                summary=json.dumps({
                    "title": "Explore this service",
                    "body": "Learn how this service supports the working day.",
                    "cta": "Shop Now",
                    "offer": None,
                    "creative_brief": "Use a grounded service-led composition.",
                    "recommended_channel": "instagram",
                    "generation_reasoning": "Lead with a concise service story.",
                    "evidence_source_ids": [],
                }),
                recommendations=[],
                proposed_actions=[],
            ),
        )
        campaign = _campaign("draft")
        session = _ScalarSession([campaign, True], rows=[[]])

        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ):
            content = await generate_content(
                session,
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=ContentGenerateRequest(
                    prompt="Promote the service",
                    campaign_id=campaign.id,
                    channel="instagram",
                    content_type="social_post",
                ),
                provider=SimpleNamespace(),
            )

        self.assertEqual(content.cta, "Learn More")
        capability_statement = session.scalars_statements[0]
        self.assertIn(campaign.id, capability_statement.compile().params.values())
        self.assertIn("campaign_product_selections", str(capability_statement))



    async def test_content_offer_requires_server_authorization_before_ai_use(self) -> None:
        session = _ScalarSession([])
        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(),
        ) as runtime:
            with self.assertRaises(MarketingValidationError):
                await generate_content(
                    session,
                    business_id=BUSINESS_ID,
                    actor_user_id=USER_ID,
                    data=ContentGenerateRequest(
                        prompt="Create an Instagram post",
                        channel="instagram",
                        content_type="social_post",
                        offer="50% off",
                        offer_authorized=True,
                    ),
                    provider=SimpleNamespace(),
                )

        runtime.assert_not_awaited()
        self.assertFalse(
            any(isinstance(item, MarketingContent) for item in session.added)
        )

    async def test_admin_offer_uses_role_accurate_server_provenance(self) -> None:
        execution = SimpleNamespace(
            context_revision="9" * 64,
            business_brain_source_count=1,
            memory_source_count=0,
            output=SimpleNamespace(
                summary=json.dumps({
                    "title": "Coordinate your business",
                    "body": "Bring daily operations into one focused system.",
                    "cta": "Explore 9D Brain",
                    "offer": "50% off",
                    "creative_brief": "Use a premium product-led scene.",
                    "recommended_channel": "instagram",
                    "generation_reasoning": "Lead with the authorized campaign offer.",
                    "evidence_source_ids": [],
                }),
                recommendations=[],
                proposed_actions=[],
            ),
        )
        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=execution),
        ):
            content = await generate_content(
                _ScalarSession([]),
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=ContentGenerateRequest(
                    prompt="Create an Instagram post",
                    channel="instagram",
                    content_type="social_post",
                    offer="50% off",
                    offer_authorized=True,
                ),
                provider=SimpleNamespace(),
                offer_authorization_role="admin",
            )

        claim = next(
            item for item in content.source_evidence
            if item.get("classification") == "claim_provenance"
        )
        self.assertEqual(
            claim["source_type"],
            "authenticated_authorized_business_input",
        )
        self.assertEqual(claim["authorization_role"], "admin")
        self.assertNotEqual(claim["source_type"], "authenticated_owner_input")
        self.assertTrue(claim["requires_approval"])

    async def test_ai_invented_discount_without_classified_source_is_rejected(self) -> None:
        execution = SimpleNamespace(
            context_revision="0" * 64,
            business_brain_source_count=1,
            memory_source_count=0,
            output=SimpleNamespace(
                summary=json.dumps({
                    "title": "Save today",
                    "body": "Get 50% off today.",
                    "cta": "Claim offer",
                    "offer": "50% off",
                    "creative_brief": "Promotional visual.",
                    "recommended_channel": "instagram",
                    "generation_reasoning": "Lead with urgency.",
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
                        prompt="Create a product post",
                        channel="instagram",
                        content_type="social_post",
                    ),
                    provider=SimpleNamespace(),
                )
        self.assertFalse(any(isinstance(item, MarketingContent) for item in session.added))

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
        self.commit_calls = 0
        self.rollback_calls = 0
        self.scalar_statements = []
        self.scalars_statements = []

    async def scalar(self, statement):
        self.scalar_statements.append(statement)
        return self.values.pop(0) if self.values else None

    async def scalars(self, statement):
        self.scalars_statements.append(statement)
        return _ScalarRows(self.rows.pop(0) if self.rows else [])

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_calls += 1

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


class _FailingFlushSession(_ScalarSession):
    async def flush(self):
        self.flush_calls += 1
        raise SQLAlchemyError("database unavailable")


class _FailingCommitSession(_ScalarSession):
    def __init__(self, values, *, fail_on_commit: int):
        super().__init__(values)
        self.fail_on_commit = fail_on_commit

    async def commit(self):
        self.commit_calls += 1
        if self.commit_calls == self.fail_on_commit:
            raise SQLAlchemyError("database unavailable")


class _RegenerationSession(_ScalarSession):
    async def scalar(self, statement):
        if self.values:
            return await super().scalar(statement)
        self.scalar_statements.append(statement)
        return next(
            value
            for value in reversed(self.added)
            if isinstance(value, CreativeAsset)
        )


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
