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
from app.schemas.marketing import CampaignCreate, CampaignGenerateRequest, CampaignUpdate, ChannelPlanCreate, ContentCreate, ContentGenerateRequest, ContentVersionCreate, CreativeBriefCreate, CreativeStrategyProposal, PerformanceCreate, PlanGenerateRequest, ScheduleCreate, TrendOpportunityRequest  # noqa: E402
from app.services.creative_provider import (
    CreativeGenerationResult,
    CreativeProviderGenerationError,
    CreativeProviderNotConfiguredError,
)  # noqa: E402
from app.services.creative_compositor import CreativeCompositionResult  # noqa: E402
from app.services.creative_visual_review import (  # noqa: E402
    CreativeVisualReview,
    CreativeVisualReviewResult,
)
from app.services.marketing import (  # noqa: E402
    _CTACapabilities,
    _CREATIVE_STRATEGY_RUNTIME_RULE_MARGIN,
    _CREATIVE_STRATEGY_TASK_BUDGET,
    _allocate_budget,
    _contains_creative_instruction_copy,
    _execute_creative_strategy,
    _creative_story_mode,
    _creative_variation_direction,
    _normalize_generated_cta,
    _raw_visual_regeneration_correction,
    _supporting_copy_for_composition,
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
    queue_creative_asset_generation,
    queue_creative_asset_regeneration,
    regenerate_creative_asset,
    run_queued_creative_asset_generation,
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


def _composed_candidate(layout: str, *, color: tuple[int, int, int]):
    return CreativeCompositionResult(
        content=_solid_png(color),
        width=640,
        height=640,
        selected_layout=layout,
        quality=SimpleNamespace(),
    )


def _solid_png(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (640, 640), color).save(output, format="PNG")
    return output.getvalue()


def _visual_review(**updates: object) -> CreativeVisualReview:
    values: dict[str, object] = {
        "hierarchy": 90,
        "composition": 90,
        "brand_consistency": 88,
        "logo_identity_quality": 88,
        "readability": 94,
        "cta_clarity": 90,
        "offer_clarity": 90,
        "focal_relevance": 87,
        "product_relevance": 89,
        "business_specific_relevance": 88,
        "visual_storytelling": 87,
        "commercial_sophistication": 86,
        "originality": 86,
        "scroll_stopping_strength": 85,
        "message_coherence": 90,
        "whitespace_balance": 88,
        "typography_quality": 91,
        "visual_sophistication": 87,
        "campaign_alignment": 90,
        "visual_polish": 89,
        "generic_template_risk": 20,
        "accidental_generated_text": False,
        "duplicated_message": False,
        "excessive_whitespace": False,
        "overcrowding": False,
        "irrelevant_visual": False,
        "irrelevant_decorative_art": False,
        "meaningless_focal_story": False,
        "replaceable_brand_creative": False,
        "decorative_abstraction_dominates": False,
        "no_product_service_story": False,
        "commercially_weak": False,
        "unnatural_headline_wrapping": False,
        "generic_template_output": False,
        "weak_brand_cta": False,
        "excessive_dead_panel_space": False,
        "hard_failures": (),
        "approved": True,
        "repair_class": "none",
        "repair_instructions": "No repair required.",
    }
    values.update(updates)
    values["hard_failures"] = tuple(
        name
        for name in (
            "accidental_generated_text",
            "duplicated_message",
            "excessive_whitespace",
            "overcrowding",
            "irrelevant_visual",
            "irrelevant_decorative_art",
                "meaningless_focal_story",
                "replaceable_brand_creative",
                "decorative_abstraction_dominates",
                "no_product_service_story",
                "commercially_weak",
            "unnatural_headline_wrapping",
            "generic_template_output",
            "weak_brand_cta",
            "excessive_dead_panel_space",
        )
        if values[name]
    )
    return CreativeVisualReview.model_validate(values)


def _visual_review_at_score(score: int) -> CreativeVisualReview:
    return _visual_review(
        hierarchy=score,
        composition=score,
        brand_consistency=score,
        logo_identity_quality=score,
        readability=score,
        cta_clarity=score,
        offer_clarity=score,
        focal_relevance=score,
        product_relevance=score,
        business_specific_relevance=score,
        visual_storytelling=score,
        commercial_sophistication=score,
        originality=score,
        scroll_stopping_strength=score,
        message_coherence=score,
        whitespace_balance=score,
        typography_quality=score,
        visual_sophistication=score,
        campaign_alignment=score,
        visual_polish=score,
    )


def _visual_result(review: CreativeVisualReview) -> CreativeVisualReviewResult:
    return CreativeVisualReviewResult(
        review=review,
        metadata=AIAgentProviderMetadata(
            provider_request_id="req_visual_test",
            input_tokens=120,
            output_tokens=40,
        ),
    )


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

    def test_supporting_copy_deduplication_preserves_exact_primary_values(self) -> None:
        headline = "50% Off"
        offer = "50% off"
        supporting = "Enjoy your special offer: 50% off."
        self.assertIsNone(
            _supporting_copy_for_composition(
                supporting,
                headline=headline,
                offer=offer,
            )
        )
        self.assertEqual(headline, "50% Off")
        self.assertEqual(offer, "50% off")
        useful = "Build a calmer week while keeping every customer handoff visible."
        self.assertEqual(
            _supporting_copy_for_composition(
                useful,
                headline=headline,
                offer=offer,
            ),
            useful,
        )

    def test_variation_and_retry_directions_are_mode_safe(self) -> None:
        alternate = _creative_variation_direction(
            {"variation_mode": "alternate_composition"},
            story_mode="brand_offer",
        )
        self.assertIsNotNone(alternate)
        self.assertIn("spatial rhythm", alternate or "")
        self.assertIn("camera framing", alternate or "")
        self.assertIn("hero placement", alternate or "")
        self.assertIn("copy corridor", alternate or "")

        product_led = _creative_variation_direction(
            {"variation_mode": "product_led"},
            story_mode="brand_offer",
        )
        outcome_led = _creative_variation_direction(
            {"variation_mode": "outcome_led"},
            story_mode="brand_offer",
        )
        self.assertIn("without inventing a product or service", product_led or "")
        self.assertIn("unsupported outcome", outcome_led or "")
        self.assertNotIn("supported product", product_led or "")

        offering = _creative_variation_direction(
            {"variation_mode": "product_led"},
            story_mode="offering_proof",
        )
        self.assertIn("supported product or service", offering or "")

        brand_retry = _raw_visual_regeneration_correction(
            _visual_review(
                approved=False,
                repair_class="raw_visual",
                commercially_weak=True,
                hard_failures=("commercially_weak",),
                repair_instructions="Provider-authored text must not leak.",
            ),
            story_mode="brand_offer",
        )
        self.assertIn("Do not invent a product", brand_retry)
        self.assertNotIn("supported product", brand_retry.casefold())
        self.assertNotIn("product moment", brand_retry.casefold())
        self.assertNotIn("service moment", brand_retry.casefold())

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

    async def test_creative_strategy_canonicalizes_instagram_offer_before_domain_validation(self) -> None:
        authorized_offer = "50% off"

        content = MarketingContent(
            id=uuid4(),
            business_id=BUSINESS_ID,
            campaign_id=None,
            channel="instagram",
            content_type="social_post",
            title="50% Off",
            body="A grounded automation product story.",
            cta="Explore now",
            language="en",
            status="draft",
            ai_generated=True,
            version=1,
            parent_content_id=None,
            root_content_id=uuid4(),
            created_by_user_id=USER_ID,
            creative_brief="Show the supported product value clearly.",
            source_evidence=[
                {
                    "classification": "claim_provenance",
                    "claim_type": "offer",
                    "claim_source": "owner_provided_campaign_input",
                    "claim_value": authorized_offer,
                }
            ],
            created_at=NOW,
            updated_at=NOW,
        )

        provider_output = _creative_strategy()
        for server_owned in (
            "offer",
            "claim_source",
            "evidence_source_ids",
            "recommendations",
            "proposed_actions",
        ):
            provider_output.pop(server_owned, None)

        # Reproduce the production failure shape:
        # provider repeats the offer as headline and varies channel casing.
        provider_output.update(
            {
                "headline": "50% off",
                "hook": "Put your business workflow on a smarter operating system.",
                "recommended_channel": "Instagram",
            }
        )

        execution = SimpleNamespace(
            provider_metadata=SimpleNamespace(
                provider_request_id="req-creative-offer-channel",
            ),
            output=provider_output,
        )

        with patch(
            "app.services.marketing._execute_creative_strategy",
            new=AsyncMock(return_value=execution),
        ):
            asset = await create_creative_brief(
                _ScalarSession([content]),
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=CreativeBriefCreate(
                    content_id=content.id,
                    asset_type="social_square",
                    instructions="make a strong instagram post",
                    aspect_ratio="1:1",
                ),
                provider=SimpleNamespace(),
            )

        strategy = json.loads(asset.visual_direction)

        self.assertEqual(asset.generation_status, "brief_ready")
        self.assertEqual(strategy["recommended_channel"], "instagram")
        self.assertEqual(strategy["offer"], authorized_offer)
        self.assertEqual(
            strategy["claim_source"],
            "owner_provided_campaign_input",
        )
        self.assertEqual(
            strategy["headline"],
            "Put your business workflow on a smarter operating system.",
        )
        self.assertNotEqual(
            " ".join(strategy["headline"].casefold().split()),
            " ".join(authorized_offer.casefold().split()),
        )

    async def test_creative_intelligence_turns_weak_request_into_structured_strategy(self) -> None:
        execution = SimpleNamespace(
            provider_metadata=SimpleNamespace(
                provider_request_id="req-creative-success",
            ),
            output=CreativeStrategyProposal(
                marketing_goal=(
                    "Increase qualified interest in the featured product."
                ),
                target_audience=(
                    "Customers interested in the business's active product range."
                ),
                audience_insight=(
                    "Lead with clear product value rather than unsupported urgency."
                ),
                campaign_angle=(
                    "A polished product-first introduction grounded in the saved brand."
                ),
                hook=(
                    "Meet the product designed for your next everyday upgrade."
                ),
                headline="Made to stand out.",
                supporting_message=(
                    "Present the product clearly with confident, concise "
                    "brand-led messaging."
                ),
                cta="Explore the product",
                visual_concept=(
                    "Premium editorial product scene with a strong central "
                    "subject and clean environmental styling."
                ),
                composition_direction=(
                    "Place the product as the dominant visual anchor and "
                    "reserve a clean text zone opposite the subject."
                ),
                subject_focus=(
                    "The supported catalog product is the hero."
                ),
                mood="Premium, confident and contemporary.",
                lighting=(
                    "Soft directional studio light with controlled contrast."
                ),
                negative_space=(
                    "Reserve generous uncluttered space for exact brand "
                    "typography and CTA."
                ),
                brand_treatment=(
                    "Use the saved brand palette as compositional accents; "
                    "the real logo is added later by the application."
                ),
                recommended_channel="instagram",
                pr_guardrails=[
                    "Use only supported product benefits.",
                    "Avoid fabricated scarcity or social proof.",
                ],
                prohibited_claims=[
                    "No invented discounts.",
                    "No unsupported performance claims.",
                ],
                evidence_source_ids=[],
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
            "app.services.marketing._execute_creative_strategy",
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
        self.assertEqual(
            strategy["recommended_channel"],
            "instagram",
        )
        self.assertEqual(
            strategy["headline"],
            "Made to stand out.",
        )
        self.assertIn(
            "negative_space",
            strategy,
        )
        self.assertIn(
            "pr_guardrails",
            strategy,
        )
        self.assertNotIn(
            "evidence_source_ids",
            strategy,
        )
        self.assertNotIn(
            "recommendations",
            strategy,
        )
        self.assertNotIn(
            "proposed_actions",
            strategy,
        )

        prompt = runtime.await_args.args[2]

        self.assertIn(
            "may provide a very short, vague",
            prompt,
        )
        self.assertIn(
            "do not require expert prompting",
            prompt.lower(),
        )
        self.assertIn(
            "Trusted content context with server-tracked provenance",
            prompt,
        )
        self.assertIn(
            "A grounded product launch post.",
            prompt,
        )
        self.assertIn(
            "Owner request: make post for my product",
            prompt,
        )
        self.assertIn(
            "- Title: Product launch",
            prompt,
        )
        self.assertIn(
            "- Existing creative brief: Premium product-led creative.",
            prompt,
        )
        self.assertIn(
            "Never include hidden reasoning",
            prompt,
        )
        self.assertEqual(
            runtime.await_args.kwargs["expected_channel"],
            "instagram",
        )

    async def test_creative_strategy_task_bounds_long_dynamic_context(self) -> None:
        authorized_offer = "50% off annual plan"
        owner_instructions = (
            "Build a premium launch around trusted business automation. "
            + "Detailed owner intent and visual preference. " * 90
        ).strip()
        content = MarketingContent(
            id=uuid4(),
            business_id=BUSINESS_ID,
            campaign_id=None,
            channel="instagram",
            content_type="social_post",
            title="Critical launch title",
            body="Trusted long-form body evidence. " * 700,
            cta="Explore the platform",
            language="en",
            status="draft",
            ai_generated=True,
            version=1,
            parent_content_id=None,
            root_content_id=uuid4(),
            created_by_user_id=USER_ID,
            creative_brief="Existing detailed visual direction. " * 300,
            source_evidence=[
                {
                    "classification": "claim_provenance",
                    "claim_type": "offer",
                    "claim_source": "owner_provided_campaign_input",
                    "claim_value": authorized_offer,
                }
            ],
            created_at=NOW,
            updated_at=NOW,
        )
        strategy_values = _creative_strategy()
        strategy_values.update({"offer": None, "claim_source": "none"})
        execution = SimpleNamespace(
            provider_metadata=SimpleNamespace(
                provider_request_id="req-creative-long-context",
            ),
            output=CreativeStrategyProposal.model_validate(strategy_values),
        )

        with patch(
            "app.services.marketing._execute_creative_strategy",
            new=AsyncMock(return_value=execution),
        ) as runtime:
            asset = await create_creative_brief(
                _ScalarSession([content]),
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=CreativeBriefCreate(
                    content_id=content.id,
                    asset_type="social_square",
                    instructions=owner_instructions,
                    aspect_ratio="1:1",
                ),
                provider=SimpleNamespace(),
            )

        task = runtime.await_args.args[2]
        self.assertEqual(asset.generation_status, "brief_ready")
        self.assertLessEqual(len(task), _CREATIVE_STRATEGY_TASK_BUDGET)
        self.assertLessEqual(
            len(task) + _CREATIVE_STRATEGY_RUNTIME_RULE_MARGIN,
            MAX_AGENT_TASK_LENGTH,
        )
        self.assertIn("GROUNDING RULES:", task)
        self.assertIn(
            "Only the server-classified offer above may be used as an offer.",
            task,
        )
        self.assertIn("Never invent testimonials", task)
        self.assertIn("MARKETING + PR STANDARD:", task)
        self.assertIn("OUTPUT CONTRACT:", task)
        self.assertIn("Return exactly one creative strategy draft", task)
        self.assertIn(
            "server can reject attempted provenance or actions before persistence",
            task,
        )
        self.assertIn("Never include hidden reasoning or chain-of-thought.", task)
        self.assertIn(f"- Offer: {authorized_offer}", task)
        self.assertIn("- Channel: instagram", task)
        self.assertIn("- Content type: social_post", task)
        self.assertIn("- Title: Critical launch title", task)
        self.assertIn("Requested asset type: social_square", task)
        self.assertIn("Requested aspect ratio: 1:1", task)
        self.assertIn(
            "Build a premium launch around trusted business automation.",
            task,
        )
        self.assertNotIn(owner_instructions, task)
        self.assertNotIn(content.body, task)
        self.assertNotIn(content.creative_brief, task)
        self.assertIn("…", task)
        self.assertEqual(
            runtime.await_args.kwargs["expected_channel"],
            "instagram",
        )
        strategy = json.loads(asset.visual_direction)
        self.assertEqual(strategy["offer"], authorized_offer)
        self.assertEqual(
            strategy["claim_source"],
            "owner_provided_campaign_input",
        )

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


    async def test_creative_intelligence_rejects_wrong_content_channel(self) -> None:
        execution = SimpleNamespace(
            provider_metadata=SimpleNamespace(
                provider_request_id="req-creative-wrong-channel",
            ),
            output=CreativeStrategyProposal(
                marketing_goal="Promote the product.",
                target_audience="Relevant customers.",
                audience_insight="Keep the message clear.",
                campaign_angle="Product-first.",
                hook="Discover it.",
                headline="Discover more.",
                supporting_message="Grounded supporting copy.",
                cta="Explore",
                visual_concept="Clean product scene.",
                composition_direction=(
                    "Product left, copy area right."
                ),
                subject_focus="Product.",
                mood="Premium.",
                lighting="Soft studio lighting.",
                negative_space="Clear copy area.",
                brand_treatment="Use saved brand accents.",
                recommended_channel="facebook",
                pr_guardrails=[],
                prohibited_claims=[],
                evidence_source_ids=[],
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
            "app.services.marketing._execute_creative_strategy",
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
            provider_metadata=SimpleNamespace(
                provider_request_id="req-creative-evidence",
            ),
            output=CreativeStrategyProposal(
                marketing_goal="Promote the business.",
                target_audience="Relevant customers.",
                audience_insight="Use trusted context.",
                campaign_angle="Brand-led.",
                hook="Explore.",
                headline="Explore.",
                supporting_message="Supported copy.",
                cta=None,
                visual_concept="Clean commercial scene.",
                composition_direction="Balanced composition.",
                subject_focus="Supported business offering.",
                mood="Professional.",
                lighting="Natural soft light.",
                negative_space="Reserve copy space.",
                brand_treatment="Use saved brand direction.",
                recommended_channel="other",
                pr_guardrails=[],
                prohibited_claims=[],
                evidence_source_ids=["invented-source"],
                recommendations=[],
                proposed_actions=[],
            ),
        )

        with patch(
            "app.services.marketing._execute_creative_strategy",
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
            provider_metadata=SimpleNamespace(
                provider_request_id="req-creative-action",
            ),
            output=CreativeStrategyProposal(
                marketing_goal="Promote the business.",
                target_audience="Relevant customers.",
                audience_insight=(
                    "Use a clear benefit-led message."
                ),
                campaign_angle="Brand-led.",
                hook="Discover more.",
                headline="Discover more.",
                supporting_message="Grounded supporting copy.",
                cta="Explore",
                visual_concept="Premium commercial scene.",
                composition_direction=(
                    "Strong hero subject and copy zone."
                ),
                subject_focus="Supported offering.",
                mood="Confident.",
                lighting="Soft directional light.",
                negative_space="Reserve clean overlay space.",
                brand_treatment="Use trusted brand colors.",
                recommended_channel="other",
                pr_guardrails=[],
                prohibited_claims=[],
                evidence_source_ids=[],
                recommendations=[],
                proposed_actions=[
                    AIAgentProposedAction(
                        action_type="publish_social_post",
                        description=(
                            "Attempt to publish generated social content."
                        ),
                        risk_level="high",
                        requires_approval=True,
                        action_payload=None,
                    )
                ],
            ),
        )

        with patch(
            "app.services.marketing._execute_creative_strategy",
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
            media_type="image",
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
            content=_png_bytes(),
            width=1024,
            height=1024,
            provider_request_id="req_123",
        )
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(return_value=result),
        )

        business = Business(
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
        storage = _ObjectStorage()
        generated = await generate_creative_asset(
            _ScalarSession([asset, business, None, asset]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=storage,
        )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(generated.source_type, "future_provider")
        self.assertEqual(
            generated.storage_reference,
            storage.public_url(storage.put.await_args.args[0]),
        )
        self.assertEqual(generated.width, 1080)
        self.assertEqual(generated.height, 1080)
        storage.put.assert_awaited_once()
        object_key, final_bytes, content_type = storage.put.await_args.args
        self.assertTrue(
            object_key.startswith(
                f"businesses/{BUSINESS_ID}/marketing/creatives/{asset.id}/final/"
            )
        )
        self.assertEqual(content_type, "image/png")
        with Image.open(BytesIO(final_bytes)) as final_image:
            self.assertEqual(final_image.size, (1080, 1080))
            self.assertEqual(final_image.format, "PNG")

        request = provider.generate_draft.await_args.args[0]
        self.assertEqual(request.business_id, BUSINESS_ID)
        self.assertEqual(request.creative_asset_id, asset.id)
        # With no authoritative campaign/catalog selection this is brand mode:
        # product-centric AI strategy prose is not forwarded as offering proof.
        self.assertIn("campaign-specific category scene", request.instructions)
        self.assertNotIn("Editorial product scene", request.instructions)
        self.assertNotIn("Premium product-first launch", request.instructions)

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
            "recommended_channel": "other",
            "pr_guardrails": [],
            "prohibited_claims": [],
        }

        asset = CreativeAsset(
            id=uuid4(),
            business_id=BUSINESS_ID,
            campaign_id=None,
            content_id=None,
            asset_type="social_square",
            media_type="image",
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
            _ScalarSession([asset, _business_record(), None]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=_ObjectStorage(),
        )

        self.assertEqual(generated.generation_status, "provider_required")
        self.assertIsNone(generated.storage_reference)
        self.assertEqual(generated.source_type, "ai_brief")

    async def test_creative_composition_runs_through_worker_thread_boundary(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        composed = _composed_candidate(
            "minimal_hero",
            color=(100, 120, 140),
        )
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )

        async def dispatch(function, *args):
            return function(*args)

        with (
            patch(
                "app.services.marketing.asyncio.to_thread",
                new=AsyncMock(side_effect=dispatch),
            ) as to_thread,
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(composed,),
            ) as compose_candidates,
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=provider,
                storage=storage,
            )

        self.assertEqual(generated.generation_status, "ready")
        to_thread.assert_awaited_once()
        self.assertIs(to_thread.await_args.args[0], compose_candidates)
        compose_candidates.assert_called_once_with(to_thread.await_args.args[1])

    async def test_duplicate_supporting_copy_is_suppressed_before_composition(self) -> None:
        strategy = _creative_strategy()
        strategy.update(
            {
                "headline": "A better season starts here",
                "supporting_message": "Enjoy your special offer: 50% off.",
                "offer": "50% off",
                "claim_source": "owner_provided_campaign_input",
            }
        )
        asset = _creative_asset(visual_direction=json.dumps(strategy))
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        candidate = _composed_candidate("minimal_hero", color=(10, 20, 30))
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(candidate,),
            ) as compose,
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                storage=_ObjectStorage(),
            )

        self.assertEqual(generated.generation_status, "ready")
        composition_input = compose.call_args.args[0]
        self.assertIsNone(composition_input.supporting_copy)
        self.assertEqual(composition_input.headline, "A better season starts here")
        self.assertEqual(composition_input.offer, "50% off")
        self.assertEqual(image_provider.generate_draft.await_count, 1)

    async def test_duplicate_only_semantic_rejection_never_buys_second_image(self) -> None:
        asset = _creative_asset()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        candidate = _composed_candidate("minimal_hero", color=(10, 20, 30))
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                return_value=_visual_result(
                    _visual_review(
                        approved=False,
                        repair_class="layout",
                        duplicated_message=True,
                        hard_failures=("duplicated_message",),
                        repair_instructions="Suppress repeated deterministic copy.",
                    )
                )
            ),
        )
        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(candidate,),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=_ObjectStorage(),
                max_image_attempts=2,
            )

        self.assertEqual(generated.generation_status, "failed")
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        self.assertEqual(reviewer.review.await_count, 1)
        self.assertEqual(reviewer.review.await_args.args[0].review_mode, "brand_offer")

    async def test_selected_catalog_offering_reaches_visual_review_as_offering_proof(self) -> None:
        asset = _creative_asset()
        asset.campaign_id = uuid4()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(return_value=_visual_result(_visual_review())),
        )
        candidate = _composed_candidate("minimal_hero", color=(10, 20, 30))
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(candidate,),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession(
                    [asset, _business_record(), None, uuid4(), asset]
                ),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=_ObjectStorage(),
                require_semantic_review=True,
            )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(
            reviewer.review.await_args.args[0].review_mode,
            "offering_proof",
        )

    async def test_brand_review_mode_contradiction_fails_closed_without_raw_retry(self) -> None:
        asset = _creative_asset()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                return_value=_visual_result(
                    _visual_review(
                        approved=False,
                        repair_class="raw_visual",
                        no_product_service_story=True,
                        hard_failures=("no_product_service_story",),
                        repair_instructions="Invent a missing offering.",
                    )
                )
            ),
        )
        candidate = _composed_candidate("minimal_hero", color=(10, 20, 30))
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(candidate,),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=_ObjectStorage(),
                max_image_attempts=2,
                require_semantic_review=True,
            )

        self.assertEqual(generated.generation_status, "failed")
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        self.assertEqual(reviewer.review.await_count, 1)

    async def test_director_output_budget_cannot_exceed_four_thousand(self) -> None:
        asset = _creative_asset()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(),
        )
        with self.assertRaises(MarketingValidationError):
            await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                storage=_ObjectStorage(),
                director_max_output_tokens=4_001,
            )
        image_provider.generate_draft.assert_not_awaited()

    async def test_visual_layout_rejection_uses_next_local_candidate_without_new_image(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        first = _composed_candidate("minimal_hero", color=(10, 20, 30))
        second = _composed_candidate("framed_campaign", color=(30, 40, 50))
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                side_effect=[
                    _visual_result(
                        _visual_review(
                            approved=False,
                            repair_class="layout",
                            excessive_whitespace=True,
                            repair_instructions="Use the next local layout.",
                        )
                    ),
                    _visual_result(_visual_review()),
                ]
            ),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(first, second),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
            )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        self.assertEqual(reviewer.review.await_count, 2)
        self.assertEqual(storage.put.await_args.args[1], second.content)

    async def test_semantic_threshold_miss_uses_next_local_candidate_without_new_image(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        first = _composed_candidate("minimal_hero", color=(10, 20, 30))
        second = _composed_candidate("framed_campaign", color=(30, 40, 50))
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                side_effect=[
                    _visual_result(_visual_review_at_score(81)),
                    _visual_result(_visual_review_at_score(82)),
                ]
            ),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(first, second),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
                quality_threshold=82,
            )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        self.assertEqual(reviewer.review.await_count, 2)
        storage.put.assert_awaited_once()
        self.assertEqual(storage.put.await_args.args[1], second.content)

    async def test_semantic_review_receives_only_ranked_technically_valid_candidates(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        score_73 = _composed_candidate("cinematic_overlay", color=(10, 20, 30))
        hard_failure = _composed_candidate("editorial_split", color=(30, 40, 50))
        score_75 = _composed_candidate("framed_campaign", color=(50, 60, 70))
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                side_effect=[
                    _visual_result(
                        _visual_review(
                            approved=False,
                            repair_class="layout",
                            excessive_whitespace=True,
                            repair_instructions="Try the next local layout.",
                        )
                    ),
                    _visual_result(_visual_review_at_score(82)),
                ]
            ),
        )
        assessments = [
            SimpleNamespace(
                eligible_for_semantic_review=True,
                approved_for_delivery=False,
                failure_kind="layout",
                overall_score=73,
            ),
            SimpleNamespace(
                eligible_for_semantic_review=False,
                approved_for_delivery=False,
                hard_failures=("unnatural_headline_wrapping",),
                failure_kind="layout",
                overall_score=73,
            ),
            SimpleNamespace(
                eligible_for_semantic_review=True,
                approved_for_delivery=False,
                failure_kind="layout",
                overall_score=75,
            ),
        ]

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(score_73, hard_failure, score_75),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                side_effect=assessments,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
                quality_threshold=82,
                require_semantic_review=True,
            )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        self.assertEqual(reviewer.review.await_count, 2)
        reviewed_images = [
            call.args[0].final_png for call in reviewer.review.await_args_list
        ]
        self.assertEqual(reviewed_images, [score_75.content, score_73.content])
        self.assertNotIn(hard_failure.content, reviewed_images)
        self.assertEqual(storage.put.await_args.args[1], score_73.content)

    async def test_below_deterministic_threshold_semantic_failure_is_not_deliverable(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        candidate = _composed_candidate("framed_campaign", color=(10, 20, 30))
        below_threshold = SimpleNamespace(
            eligible_for_semantic_review=True,
            approved_for_delivery=False,
            failure_kind="layout",
            overall_score=75,
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                return_value=_visual_result(_visual_review_at_score(81))
            ),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(candidate,),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=below_threshold,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
                quality_threshold=82,
                require_semantic_review=True,
            )

        self.assertEqual(generated.generation_status, "failed")
        self.assertIsNone(generated.storage_reference)
        self.assertEqual(reviewer.review.await_count, 1)
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        storage.put.assert_not_awaited()

    async def test_nonsemantic_path_keeps_strict_deterministic_threshold(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        candidate = _composed_candidate("framed_campaign", color=(10, 20, 30))
        below_threshold = SimpleNamespace(
            eligible_for_semantic_review=True,
            approved_for_delivery=False,
            failure_kind="layout",
            overall_score=75,
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(candidate,),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=below_threshold,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=None,
                storage=storage,
                quality_threshold=82,
                require_semantic_review=False,
            )

        self.assertEqual(generated.generation_status, "failed")
        self.assertIsNone(generated.storage_reference)
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        storage.put.assert_not_awaited()

    async def test_optional_semantic_outage_cannot_promote_below_threshold_candidate(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        candidate = _composed_candidate("framed_campaign", color=(10, 20, 30))
        below_threshold = SimpleNamespace(
            eligible_for_semantic_review=True,
            approved_for_delivery=False,
            failure_kind="layout",
            overall_score=75,
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(side_effect=TimeoutError("review timed out")),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(candidate,),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=below_threshold,
            ),
            self.assertLogs("aibos.marketing", level="WARNING"),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
                quality_threshold=82,
                require_semantic_review=False,
            )

        self.assertEqual(generated.generation_status, "failed")
        self.assertIsNone(generated.storage_reference)
        self.assertEqual(reviewer.review.await_count, 1)
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        storage.put.assert_not_awaited()

    async def test_all_semantic_candidates_below_runtime_threshold_fail_quality(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        candidates = (
            _composed_candidate("minimal_hero", color=(10, 20, 30)),
            _composed_candidate("framed_campaign", color=(30, 40, 50)),
        )
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                side_effect=[
                    _visual_result(_visual_review_at_score(81)),
                    _visual_result(_visual_review_at_score(81)),
                ]
            ),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=candidates,
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
                quality_threshold=82,
            )

        self.assertEqual(generated.generation_status, "failed")
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        self.assertEqual(reviewer.review.await_count, 2)
        storage.put.assert_not_awaited()

    async def test_threshold_rejected_candidate_is_not_revived_by_budget_degradation(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        rejected = _composed_candidate("minimal_hero", color=(10, 20, 30))
        unreviewed = _composed_candidate("framed_campaign", color=(30, 40, 50))
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                return_value=_visual_result(_visual_review_at_score(81))
            ),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(rejected, unreviewed),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
            self.assertLogs("aibos.marketing", level="WARNING") as captured,
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
                max_visual_review_calls=1,
                quality_threshold=82,
            )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        self.assertEqual(reviewer.review.await_count, 1)
        storage.put.assert_awaited_once()
        self.assertEqual(storage.put.await_args.args[1], unreviewed.content)
        self.assertNotEqual(storage.put.await_args.args[1], rejected.content)
        logs = " ".join(captured.output)
        self.assertIn("creative_visual_review_degraded", logs)
        self.assertIn("reason=budget_exhausted", logs)

    async def test_deterministic_layout_failure_uses_next_local_candidate(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        first = _composed_candidate("minimal_hero", color=(10, 20, 30))
        second = _composed_candidate("framed_campaign", color=(30, 40, 50))
        layout_failure = SimpleNamespace(
            approved_for_delivery=False,
            failure_kind="layout",
            overall_score=70,
        )
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(first, second),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                side_effect=[layout_failure, approved],
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                storage=storage,
            )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        self.assertEqual(storage.put.await_args.args[1], second.content)

    async def test_visual_raw_duplicate_gets_exactly_one_image_regeneration(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                side_effect=[
                    CreativeGenerationResult(
                        content=_png_bytes(),
                        width=1024,
                        height=1024,
                    ),
                    CreativeGenerationResult(
                        content=_png_bytes(),
                        width=1024,
                        height=1024,
                    ),
                ]
            ),
        )
        first = _composed_candidate("minimal_hero", color=(10, 20, 30))
        second = _composed_candidate("framed_campaign", color=(30, 40, 50))
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                side_effect=[
                    _visual_result(
                        _visual_review(
                            approved=False,
                            repair_class="raw_visual",
                            accidental_generated_text=True,
                            duplicated_message=True,
                            hard_failures=(
                                "accidental_generated_text",
                                "duplicated_message",
                            ),
                            repair_instructions=(
                                "LEAK_THIS_PROVIDER_TEXT: remove giant 50% OFF."
                            ),
                        )
                    ),
                    _visual_result(_visual_review()),
                ]
            ),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                side_effect=[(first,), (second,)],
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
                max_image_attempts=2,
                max_visual_review_calls=2,
            )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(image_provider.generate_draft.await_count, 2)
        self.assertEqual(reviewer.review.await_count, 2)
        corrected = image_provider.generate_draft.await_args_list[1].args[0].instructions
        self.assertIn("no letters, words, numbers", corrected)
        self.assertNotIn("LEAK_THIS_PROVIDER_TEXT", corrected)

    async def test_required_visual_review_missing_fails_before_image_generation(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )

        generated = await generate_creative_asset(
            _ScalarSession([asset, _business_record(), None]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=image_provider,
            visual_review_provider=None,
            storage=storage,
            require_semantic_review=True,
        )

        self.assertEqual(generated.generation_status, "failed")
        self.assertEqual(generated.source_type, "ai_brief")
        self.assertIsNone(generated.storage_reference)

        # Required semantic capability is checked before paying for image work.
        image_provider.generate_draft.assert_not_awaited()
        storage.put.assert_not_awaited()


    async def test_required_visual_reviewer_failure_never_marks_ready(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        candidate = _composed_candidate(
            "minimal_hero",
            color=(10, 20, 30),
        )
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                side_effect=RuntimeError(
                    "sk-proj-secret provider payload"
                )
            ),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(candidate,),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
            self.assertLogs(
                "aibos.marketing",
                level="WARNING",
            ) as captured,
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
                require_semantic_review=True,
            )

        self.assertEqual(generated.generation_status, "failed")
        self.assertIsNone(generated.storage_reference)

        self.assertEqual(
            image_provider.generate_draft.await_count,
            1,
        )
        self.assertEqual(
            reviewer.review.await_count,
            1,
        )
        storage.put.assert_not_awaited()

        # Provider exception text/secrets are never exposed through logs.
        self.assertNotIn(
            "sk-proj-secret",
            " ".join(captured.output),
        )


    async def test_required_visual_review_budget_exhaustion_never_marks_ready(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                side_effect=[
                    CreativeGenerationResult(
                        content=_png_bytes(),
                        width=1024,
                        height=1024,
                    ),
                    CreativeGenerationResult(
                        content=_png_bytes(),
                        width=1024,
                        height=1024,
                    ),
                ]
            ),
        )

        first = _composed_candidate(
            "minimal_hero",
            color=(10, 20, 30),
        )
        second = _composed_candidate(
            "framed_campaign",
            color=(30, 40, 50),
        )
        regenerated = _composed_candidate(
            "editorial_split",
            color=(50, 60, 70),
        )

        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )

        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                side_effect=[
                    _visual_result(
                        _visual_review(
                            approved=False,
                            repair_class="layout",
                            excessive_whitespace=True,
                            repair_instructions=(
                                "Try another local composition."
                            ),
                        )
                    ),
                    _visual_result(
                        _visual_review(
                            approved=False,
                            repair_class="raw_visual",
                            accidental_generated_text=True,
                            repair_instructions=(
                                "Regenerate the raw visual."
                            ),
                        )
                    ),
                ]
            ),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                side_effect=[
                    (first, second),
                    (regenerated,),
                ],
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
            self.assertLogs(
                "aibos.marketing",
                level="WARNING",
            ) as captured,
        ):
            generated = await generate_creative_asset(
                _ScalarSession(
                    [asset, _business_record(), None]
                ),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
                max_image_attempts=2,
                max_visual_review_calls=2,
                require_semantic_review=True,
            )

        self.assertEqual(
            generated.generation_status,
            "failed",
        )
        self.assertIsNone(
            generated.storage_reference,
        )

        self.assertEqual(
            image_provider.generate_draft.await_count,
            2,
        )
        self.assertEqual(
            reviewer.review.await_count,
            2,
        )

        storage.put.assert_not_awaited()

        logs = " ".join(captured.output)
        self.assertIn(
            "reason=budget_exhausted",
            logs,
        )


    async def test_required_visual_review_pass_marks_ready(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )

        candidate = _composed_candidate(
            "minimal_hero",
            color=(10, 20, 30),
        )

        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )

        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                return_value=_visual_result(
                    _visual_review()
                )
            ),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(candidate,),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession(
                    [
                        asset,
                        _business_record(),
                        None,
                        asset,
                    ]
                ),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
                quality_threshold=82,
                require_semantic_review=True,
            )

        self.assertEqual(
            generated.generation_status,
            "ready",
        )
        self.assertEqual(
            reviewer.review.await_count,
            1,
        )
        self.assertEqual(
            image_provider.generate_draft.await_count,
            1,
        )

        storage.put.assert_awaited_once()
        self.assertEqual(
            storage.put.await_args.args[1],
            candidate.content,
        )


    async def test_regenerated_image_uses_deterministic_qa_when_review_budget_is_exhausted(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                side_effect=[
                    CreativeGenerationResult(
                        content=_png_bytes(),
                        width=1024,
                        height=1024,
                    ),
                    CreativeGenerationResult(
                        content=_png_bytes(),
                        width=1024,
                        height=1024,
                    ),
                ]
            ),
        )
        first = _composed_candidate("minimal_hero", color=(10, 20, 30))
        second = _composed_candidate("framed_campaign", color=(30, 40, 50))
        regenerated = _composed_candidate(
            "editorial_split",
            color=(50, 60, 70),
        )
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                side_effect=[
                    _visual_result(
                        _visual_review(
                            approved=False,
                            repair_class="layout",
                            excessive_whitespace=True,
                            repair_instructions="Try another local composition.",
                        )
                    ),
                    _visual_result(
                        _visual_review(
                            approved=False,
                            repair_class="raw_visual",
                            accidental_generated_text=True,
                            repair_instructions="Regenerate the raw visual.",
                        )
                    ),
                ]
            ),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                side_effect=[(first, second), (regenerated,)],
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
            self.assertLogs("aibos.marketing", level="WARNING") as captured,
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
                max_image_attempts=2,
                max_visual_review_calls=2,
            )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(image_provider.generate_draft.await_count, 2)
        self.assertEqual(reviewer.review.await_count, 2)
        storage.put.assert_awaited_once()
        self.assertEqual(storage.put.await_args.args[1], regenerated.content)
        logs = " ".join(captured.output)
        self.assertIn("creative_visual_review_degraded", logs)
        self.assertIn("reason=budget_exhausted", logs)

    async def test_visual_reviewer_failure_degrades_to_deterministic_candidate(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        candidate = _composed_candidate("minimal_hero", color=(10, 20, 30))
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(
                side_effect=RuntimeError("sk-proj-secret provider payload")
            ),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(candidate,),
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
            self.assertLogs("aibos.marketing", level="WARNING") as captured,
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                storage=storage,
            )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        self.assertNotIn("sk-proj-secret", " ".join(captured.output))

    async def test_visual_review_call_budget_never_exceeds_two(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        image_provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        candidates = tuple(
            _composed_candidate(layout, color=color)
            for layout, color in (
                ("minimal_hero", (10, 20, 30)),
                ("framed_campaign", (30, 40, 50)),
                ("editorial_split", (50, 60, 70)),
            )
        )
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        layout_review = _visual_result(
            _visual_review(
                approved=False,
                repair_class="layout",
                excessive_whitespace=True,
                repair_instructions="Try another local composition.",
            )
        )
        reviewer = SimpleNamespace(
            provider_name="test_vision",
            review=AsyncMock(return_value=layout_review),
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=candidates,
            ),
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
        ):
            generated = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=image_provider,
                visual_review_provider=reviewer,
                max_visual_review_calls=2,
                storage=storage,
            )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(reviewer.review.await_count, 2)
        self.assertEqual(image_provider.generate_draft.await_count, 1)
        storage.put.assert_awaited_once()

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
            media_type="image",
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
            _ScalarSession([asset, _business_record(), None]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=_ObjectStorage(),
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
            media_type="image",
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
                storage=_ObjectStorage(),
            )

        provider.generate_draft.assert_not_awaited()
        self.assertEqual(
            asset.storage_reference,
            "https://media.example.com/existing.png",
        )

    async def test_creative_composition_failure_persists_failed_state(self) -> None:
        asset = _creative_asset()
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=b"not-an-image",
                    width=640,
                    height=640,
                )
            ),
        )
        storage = _ObjectStorage()

        generated = await generate_creative_asset(
            _ScalarSession([asset, _business_record(), None]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=storage,
        )

        self.assertEqual(generated.generation_status, "failed")
        self.assertIsNone(generated.storage_reference)
        storage.put.assert_not_awaited()

    async def test_raw_visual_failure_gets_one_bounded_corrective_regeneration(self) -> None:
        asset = _creative_asset()
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                side_effect=[
                    CreativeGenerationResult(
                        content=b"not-an-image",
                        width=640,
                        height=640,
                    ),
                    CreativeGenerationResult(
                        content=_png_bytes(),
                        width=1024,
                        height=1024,
                    ),
                ]
            ),
        )
        storage = _ObjectStorage()

        generated = await generate_creative_asset(
            _ScalarSession([asset, _business_record(), None, asset]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=storage,
            max_image_attempts=2,
        )

        self.assertEqual(generated.generation_status, "ready")
        self.assertEqual(provider.generate_draft.await_count, 2)
        corrected = provider.generate_draft.await_args_list[1].args[0].instructions
        self.assertIn("Correction for this attempt", corrected)
        self.assertIn("keep all typography absent", corrected)

    async def test_image_attempt_limit_is_never_exceeded(self) -> None:
        asset = _creative_asset()
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=b"not-an-image",
                    width=640,
                    height=640,
                )
            ),
        )

        generated = await generate_creative_asset(
            _ScalarSession([asset, _business_record(), None]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=_ObjectStorage(),
            max_image_attempts=2,
        )

        self.assertEqual(generated.generation_status, "failed")
        self.assertEqual(provider.generate_draft.await_count, 2)

    async def test_unsupported_typography_persists_failed_state(self) -> None:
        strategy = _creative_strategy()
        strategy["headline"] = "مرحبا بالعالم"
        asset = _creative_asset(visual_direction=json.dumps(strategy))
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        storage = _ObjectStorage()

        generated = await generate_creative_asset(
            _ScalarSession([asset, _business_record(), None]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=storage,
        )

        self.assertEqual(generated.generation_status, "failed")
        self.assertIsNone(generated.storage_reference)
        storage.put.assert_not_awaited()

    async def test_creative_storage_failure_never_marks_asset_ready(self) -> None:
        asset = _creative_asset()
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        storage = _ObjectStorage(
            put_error=StorageOperationError("storage unavailable")
        )

        generated = await generate_creative_asset(
            _ScalarSession([asset, _business_record(), None, asset]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=storage,
        )

        self.assertEqual(generated.generation_status, "failed")
        self.assertIsNone(generated.storage_reference)
        storage.delete.assert_awaited_once_with(storage.put.await_args.args[0])

    async def test_public_reference_failure_deletes_stored_creative(self) -> None:
        asset = _creative_asset()
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        storage = _ObjectStorage(
            public_url_error=StorageOperationError("reference unavailable")
        )

        generated = await generate_creative_asset(
            _ScalarSession([asset, _business_record(), None, asset]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=storage,
        )

        self.assertEqual(generated.generation_status, "failed")
        self.assertIsNone(generated.storage_reference)
        storage.put.assert_awaited_once()
        storage.delete.assert_awaited_once_with(storage.put.await_args.args[0])

    async def test_finalization_recheck_preserves_concurrent_ready_winner(self) -> None:
        initial = _creative_asset()
        winner = _creative_asset(status="ready", source_type="future_provider")
        winner.id = initial.id
        winner.storage_reference = "https://media.example.com/winning-final.png"
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        composed = _composed_candidate(
            "minimal_hero",
            color=(100, 120, 140),
        )
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=88,
        )
        storage = _ObjectStorage()
        session = _ScalarSession(
            [initial, _business_record(), None, winner]
        )

        with (
            patch(
                "app.services.marketing.CreativeCompositor.compose_candidates",
                return_value=(composed,),
            ) as compose_candidates,
            patch(
                "app.services.marketing.assess_creative_quality",
                return_value=approved,
            ),
        ):
            generated = await generate_creative_asset(
                session,
                business_id=BUSINESS_ID,
                creative_asset_id=initial.id,
                actor_user_id=USER_ID,
                provider=provider,
                storage=storage,
            )

        self.assertIs(generated, winner)
        self.assertEqual(winner.generation_status, "ready")
        self.assertEqual(
            winner.storage_reference,
            "https://media.example.com/winning-final.png",
        )
        provider.generate_draft.assert_awaited_once()
        compose_candidates.assert_called_once()
        storage.put.assert_not_awaited()
        storage.delete.assert_not_awaited()
        finalization_statement = session.scalar_statements[-1]
        self.assertIn("FOR UPDATE", str(finalization_statement))
        self.assertIn("creative_assets.business_id", str(finalization_statement))
        self.assertTrue(
            finalization_statement.get_execution_options()["populate_existing"]
        )

    async def test_creative_reads_real_logo_from_private_tenant_key(self) -> None:
        asset = _creative_asset()
        logo_key = f"businesses/{BUSINESS_ID}/branding/logo/brand.png"
        branding = BusinessBranding(
            business_id=BUSINESS_ID,
            logo_url="https://public.example.com/logo.png",
            logo_storage_key=logo_key,
            primary_color="#123456",
            secondary_color="#F4F0E8",
            accent_color="#D27D2D",
        )
        transparent_logo = BytesIO()
        Image.new("RGBA", (240, 80), (220, 20, 20, 120)).save(
            transparent_logo,
            format="PNG",
        )
        storage = _ObjectStorage(get_content=transparent_logo.getvalue())
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )

        generated = await generate_creative_asset(
            _ScalarSession([asset, _business_record(), branding, asset]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=storage,
        )

        self.assertEqual(generated.generation_status, "ready")
        storage.get.assert_awaited_once_with(logo_key, max_bytes=5_000_000)
        provider_request = provider.generate_draft.await_args.args[0]
        self.assertNotIn(logo_key, provider_request.instructions)
        self.assertNotIn(branding.logo_url, provider_request.instructions)

    async def test_invalid_private_logo_is_omitted_without_failing_creative(self) -> None:
        asset = _creative_asset()
        logo_key = f"businesses/{BUSINESS_ID}/branding/logo/invalid.png"
        branding = BusinessBranding(
            business_id=BUSINESS_ID,
            logo_url=None,
            logo_storage_key=logo_key,
            primary_color=None,
            secondary_color=None,
            accent_color=None,
        )
        storage = _ObjectStorage(get_content=b"invalid-logo")
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )

        generated = await generate_creative_asset(
            _ScalarSession([asset, _business_record(), branding, asset]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=storage,
        )

        self.assertEqual(generated.generation_status, "ready")
        storage.get.assert_awaited_once()

    async def test_cross_tenant_logo_key_is_never_read(self) -> None:
        asset = _creative_asset()
        branding = BusinessBranding(
            business_id=BUSINESS_ID,
            logo_url="https://public.example.com/logo.png",
            logo_storage_key=f"businesses/{uuid4()}/branding/logo/brand.png",
            primary_color="#123456",
            secondary_color="#F4F0E8",
            accent_color="#D27D2D",
        )
        storage = _ObjectStorage()
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )

        generated = await generate_creative_asset(
            _ScalarSession([asset, _business_record(), branding, asset]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=storage,
        )

        self.assertEqual(generated.generation_status, "ready")
        storage.get.assert_not_awaited()

    async def test_cross_tenant_creative_is_rejected_before_generation(self) -> None:
        asset = _creative_asset(business_id=uuid4())
        provider = SimpleNamespace(provider_name="test", generate_draft=AsyncMock())

        with self.assertRaises(MarketingNotFoundError):
            await generate_creative_asset(
                _ScalarSession([asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=provider,
                storage=_ObjectStorage(),
            )
        provider.generate_draft.assert_not_awaited()

    async def test_legacy_unstructured_brief_fails_safely_and_remains_unchanged(self) -> None:
        asset = _creative_asset(visual_direction="Use a blue background")
        provider = SimpleNamespace(provider_name="test", generate_draft=AsyncMock())

        with self.assertRaises(MarketingValidationError):
            await generate_creative_asset(
                _ScalarSession([asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=provider,
                storage=_ObjectStorage(),
            )

        self.assertEqual(asset.visual_direction, "Use a blue background")
        self.assertEqual(asset.generation_status, "brief_ready")
        provider.generate_draft.assert_not_awaited()

    async def test_legacy_unstructured_brief_remains_listable(self) -> None:
        asset = _creative_asset(visual_direction="Legacy visual direction")

        result = await list_creative_assets(
            _ScalarSession([], rows=[[asset]]),
            business_id=BUSINESS_ID,
            campaign_id=None,
            content_id=None,
        )

        self.assertEqual(result, [asset])

    async def test_ready_regeneration_creates_new_asset_and_preserves_original(self) -> None:
        original = _creative_asset(status="ready", source_type="future_provider")
        original_reference = original.storage_reference
        storage = _ObjectStorage()
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )
        session = _RegenerationSession([original, _business_record(), None])

        revision = await regenerate_creative_asset(
            session,
            business_id=BUSINESS_ID,
            creative_asset_id=original.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=storage,
        )

        self.assertIsNot(revision, original)
        self.assertNotEqual(revision.id, original.id)
        self.assertEqual(revision.generation_status, "ready")
        self.assertEqual(original.generation_status, "ready")
        self.assertEqual(original.storage_reference, original_reference)
        self.assertNotEqual(revision.storage_reference, original_reference)
        self.assertEqual(revision.creative_metadata["revision_of"], str(original.id))
        self.assertNotIn("variation_mode", revision.creative_metadata)

    async def test_creative_generation_enqueue_is_fast_durable_and_idempotent(self) -> None:
        asset = _creative_asset()
        enqueue = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
        with patch("app.services.marketing.enqueue_job", new=enqueue):
            first = await queue_creative_asset_generation(
                _ScalarSession([asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
            )
            first_key = enqueue.await_args.kwargs["idempotency_key"]
            second = await queue_creative_asset_generation(
                _ScalarSession([asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
            )
            second_key = enqueue.await_args.kwargs["idempotency_key"]

        self.assertIs(first, asset)
        self.assertIs(second, asset)
        self.assertEqual(asset.generation_status, "queued")
        self.assertEqual(asset.creative_metadata["image_generation_epoch"], 1)
        self.assertEqual(
            asset.creative_metadata["generation_requested_by_user_id"],
            str(USER_ID),
        )
        self.assertEqual(first_key, second_key)
        self.assertIn(f"creative-generation:{asset.id}:epoch:1", first_key)
        self.assertEqual(enqueue.await_args.kwargs["creative_asset_id"], asset.id)

    async def test_queued_regeneration_is_immutable_and_variation_scoped(self) -> None:
        original = _creative_asset(status="ready", source_type="future_provider")
        original_reference = original.storage_reference
        session = _ScalarSession([original])
        enqueue = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
        with patch("app.services.marketing.enqueue_job", new=enqueue):
            revision = await queue_creative_asset_regeneration(
                session,
                business_id=BUSINESS_ID,
                creative_asset_id=original.id,
                actor_user_id=USER_ID,
                variation_mode="alternate_metaphor",
            )

        self.assertNotEqual(revision.id, original.id)
        self.assertEqual(revision.generation_status, "queued")
        self.assertEqual(revision.creative_metadata["revision_of"], str(original.id))
        self.assertEqual(
            revision.creative_metadata["variation_mode"],
            "alternate_metaphor",
        )
        self.assertIn(
            "variation:alternate_metaphor",
            enqueue.await_args.kwargs["idempotency_key"],
        )
        self.assertEqual(original.generation_status, "ready")
        self.assertEqual(original.storage_reference, original_reference)

    async def test_checkpoint_recovery_reuses_paid_raw_image_after_crash(self) -> None:
        asset = _creative_asset(status="generating")
        asset.creative_metadata = {
            "image_generation_epoch": 1,
            "image_generation_version": 1,
            "image_generation_started_attempt": 0,
            "generation_requested_by_user_id": str(USER_ID),
        }
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(return_value=CreativeGenerationResult(
                content=_png_bytes(), width=1024, height=1024,
            )),
        )
        storage = _DurableCheckpointStorage()
        with patch(
            "app.services.marketing.CreativeCompositor.compose_candidates",
            side_effect=RuntimeError("simulated worker crash"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated worker crash"):
                await generate_creative_asset(
                    _ScalarSession([asset, _business_record(), None]),
                    business_id=BUSINESS_ID,
                    creative_asset_id=asset.id,
                    actor_user_id=USER_ID,
                    provider=provider,
                    storage=storage,
                    generation_epoch=1,
                )

        checkpoint_keys = [key for key in storage.objects if "/raw/" in key]
        self.assertEqual(len(checkpoint_keys), 1)

        composed = _composed_candidate("minimal_hero", color=(100, 120, 140))
        approved = SimpleNamespace(
            approved_for_delivery=True,
            failure_kind=None,
            overall_score=90,
        )
        with patch(
            "app.services.marketing.CreativeCompositor.compose_candidates",
            return_value=(composed,),
        ), patch(
            "app.services.marketing.assess_creative_quality",
            return_value=approved,
        ):
            recovered = await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=provider,
                storage=storage,
                generation_epoch=1,
            )

        self.assertEqual(provider.generate_draft.await_count, 1)
        self.assertEqual(recovered.generation_status, "ready")
        self.assertIsNotNone(recovered.storage_reference)
        self.assertNotIn("/raw/", recovered.storage_reference or "")

    async def test_worker_commits_attempt_marker_before_paid_provider_call(self) -> None:
        asset = _creative_asset(status="queued")
        asset.creative_metadata = {
            "image_generation_epoch": 1,
            "image_generation_version": 1,
            "image_generation_started_attempt": 0,
            "generation_requested_by_user_id": str(USER_ID),
        }
        session = _ScalarSession([asset, _business_record(), None, asset])

        async def generate_after_commit(_request):
            self.assertGreaterEqual(session.commit_calls, 2)
            self.assertEqual(
                asset.creative_metadata["image_generation_started_attempt"],
                1,
            )
            return CreativeGenerationResult(
                content=_png_bytes(),
                width=1024,
                height=1024,
            )

        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(side_effect=generate_after_commit),
        )
        generated = await run_queued_creative_asset_generation(
            session,  # type: ignore[arg-type]
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            provider=provider,
            storage=_DurableCheckpointStorage(),
            require_semantic_review=False,
        )

        self.assertEqual(generated.generation_status, "ready")
        provider.generate_draft.assert_awaited_once()

    async def test_attempt_marker_database_failure_prevents_provider_call(self) -> None:
        asset = _creative_asset(status="queued")
        asset.creative_metadata = {
            "image_generation_epoch": 1,
            "image_generation_version": 1,
            "image_generation_started_attempt": 0,
            "generation_requested_by_user_id": str(USER_ID),
        }
        session = _FailingCommitSession(
            [asset, _business_record(), None],
            fail_on_commit=2,
        )
        provider = SimpleNamespace(provider_name="test", generate_draft=AsyncMock())

        with self.assertRaises(MarketingPersistenceError):
            await run_queued_creative_asset_generation(
                session,  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                provider=provider,
                storage=_DurableCheckpointStorage(),
                require_semantic_review=False,
            )

        provider.generate_draft.assert_not_awaited()

    async def test_checkpoint_read_failure_never_calls_image_provider(self) -> None:
        asset = _creative_asset(status="generating")
        asset.creative_metadata = {
            "image_generation_epoch": 1,
            "image_generation_version": 1,
            "image_generation_started_attempt": 0,
            "generation_requested_by_user_id": str(USER_ID),
        }
        provider = SimpleNamespace(provider_name="test", generate_draft=AsyncMock())

        with self.assertRaises(MarketingPersistenceError):
            await generate_creative_asset(
                _ScalarSession([asset, _business_record(), None]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=provider,
                storage=_DurableCheckpointStorage(read_error=True),
                generation_epoch=1,
            )

        provider.generate_draft.assert_not_awaited()
        self.assertEqual(
            asset.creative_metadata["image_generation_failure_stage"],
            "checkpoint_read",
        )

    async def test_ambiguous_started_provider_attempt_is_never_repurchased(self) -> None:
        asset = _creative_asset(status="generating")
        asset.creative_metadata = {
            "image_generation_epoch": 1,
            "image_generation_version": 1,
            "image_generation_started_attempt": 1,
            "generation_requested_by_user_id": str(USER_ID),
        }
        provider = SimpleNamespace(provider_name="test", generate_draft=AsyncMock())

        recovered = await generate_creative_asset(
            _ScalarSession([asset, _business_record(), None]),
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
            storage=_DurableCheckpointStorage(),
            generation_epoch=1,
        )

        provider.generate_draft.assert_not_awaited()
        self.assertEqual(recovered.generation_status, "failed")
        self.assertEqual(
            recovered.creative_metadata["image_generation_failure_stage"],
            "provider_outcome_uncertain",
        )

    async def test_final_storage_is_compensated_when_database_flush_fails(self) -> None:
        asset = _creative_asset()
        storage = _ObjectStorage()
        provider = SimpleNamespace(
            provider_name="test",
            generate_draft=AsyncMock(
                return_value=CreativeGenerationResult(
                    content=_png_bytes(),
                    width=1024,
                    height=1024,
                )
            ),
        )

        with self.assertRaises(MarketingPersistenceError):
            await generate_creative_asset(
                _FailingFlushSession([asset, _business_record(), None, asset]),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=provider,
                storage=storage,
            )

        storage.put.assert_awaited_once()
        storage.delete.assert_awaited_once_with(storage.put.await_args.args[0])
        self.assertEqual(asset.generation_status, "brief_ready")
        self.assertIsNone(asset.storage_reference)

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

    async def test_existing_malformed_cta_is_revalidated_for_creative_strategy(self) -> None:
        campaign = _campaign("draft")
        content_id = uuid4()
        content = MarketingContent(
            id=content_id,
            business_id=BUSINESS_ID,
            campaign_id=campaign.id,
            channel="instagram",
            content_type="social_post",
            title="Supported collection",
            body="A grounded product story.",
            cta="buy Now",
            language="en",
            status="approved",
            ai_generated=True,
            version=1,
            parent_content_id=None,
            root_content_id=content_id,
            created_by_user_id=USER_ID,
            source_evidence=[],
            created_at=NOW,
            updated_at=NOW,
        )
        strategy_values = _creative_strategy()
        strategy_values["cta"] = "buy Now"
        execution = SimpleNamespace(
            provider_metadata=SimpleNamespace(provider_request_id="req-cta"),
            output=CreativeStrategyProposal.model_validate(strategy_values),
        )
        session = _ScalarSession([campaign, content], rows=[[uuid4()]])

        with patch(
            "app.services.marketing._execute_creative_strategy",
            new=AsyncMock(return_value=execution),
        ) as runtime:
            asset = await create_creative_brief(
                session,
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=CreativeBriefCreate(
                    campaign_id=campaign.id,
                    content_id=content.id,
                    asset_type="social_square",
                    instructions="Create a product-led visual.",
                    aspect_ratio="1:1",
                ),
                provider=SimpleNamespace(),
            )

        self.assertEqual(json.loads(asset.visual_direction)["cta"], "Shop Now")
        self.assertIn("Shop Now", runtime.await_args.args[2])
        self.assertNotIn("buy Now", runtime.await_args.args[2])

    async def test_owner_authorized_offer_survives_content_and_creative_strategy(self) -> None:
        content_execution = SimpleNamespace(
            context_revision="f" * 64,
            business_brain_source_count=1,
            memory_source_count=0,
            output=SimpleNamespace(
                summary=json.dumps({
                    "title": "Run your business with an AI team",
                    "body": "Coordinate marketing, sales, and customer operations.",
                    "cta": "Explore 9D Brain",
                    "offer": "50% off",
                    "creative_brief": "Premium product-led AI operations story.",
                    "recommended_channel": "instagram",
                    "generation_reasoning": "Use a conversion-focused product story.",
                    "evidence_source_ids": [],
                }),
                recommendations=[],
                proposed_actions=[],
            ),
        )
        content_session = _ScalarSession([])
        with patch(
            "app.services.marketing._execute_cmo",
            new=AsyncMock(return_value=content_execution),
        ) as content_runtime:
            content = await generate_content(
                content_session,
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=ContentGenerateRequest(
                    prompt="Create a premium conversion-focused AI Business OS post",
                    channel="instagram",
                    content_type="social_post",
                    offer="50% off",
                    offer_authorized=True,
                ),
                provider=SimpleNamespace(),
                offer_authorization_role="owner",
            )

        self.assertEqual(content.status, "draft")
        self.assertIn("50% off", content.body)
        claim = next(
            item for item in content.source_evidence
            if item.get("classification") == "claim_provenance"
        )
        self.assertEqual(claim["claim_source"], "owner_provided_campaign_input")
        self.assertEqual(claim["claim_value"], "50% off")
        self.assertTrue(claim["requires_approval"])
        self.assertEqual(
            claim["source_type"],
            "authenticated_authorized_business_input",
        )
        self.assertEqual(claim["authorization_role"], "owner")
        self.assertIn("Authenticated campaign offer: 50% off", content_runtime.await_args.args[2])

        strategy_values = _creative_strategy()
        strategy_values.update({"offer": None, "claim_source": "none"})
        strategy_execution = SimpleNamespace(
            provider_metadata=SimpleNamespace(provider_request_id="req-owner-offer"),
            output=CreativeStrategyProposal.model_validate(strategy_values),
        )
        with patch(
            "app.services.marketing._execute_creative_strategy",
            new=AsyncMock(return_value=strategy_execution),
        ):
            asset = await create_creative_brief(
                _ScalarSession([content]),
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=CreativeBriefCreate(
                    content_id=content.id,
                    asset_type="social_square",
                    instructions="Create the premium campaign visual.",
                    aspect_ratio="1:1",
                ),
                provider=SimpleNamespace(),
            )
        strategy = json.loads(asset.visual_direction)
        self.assertEqual(strategy["offer"], "50% off")
        self.assertEqual(strategy["claim_source"], "owner_provided_campaign_input")

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
        self.commit_calls = 0
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
