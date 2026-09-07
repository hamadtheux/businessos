from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from pydantic import ValidationError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.services.creative_visual_review import (  # noqa: E402
    CreativeVisualReview,
    CreativeVisualReviewProviderError,
    CreativeVisualReviewRequest,
    build_visual_review_task,
    semantic_visual_quality_score,
    semantic_visual_review_meets_threshold,
    validate_visual_review_for_mode,
)
from app.services.creative_visual_review_openai import (  # noqa: E402
    OpenAICreativeVisualReviewProvider,
)


def _review(**updates: object) -> CreativeVisualReview:
    values: dict[str, object] = {
        "hierarchy": 91,
        "composition": 89,
        "brand_consistency": 88,
        "logo_identity_quality": 88,
        "readability": 94,
        "cta_clarity": 90,
        "offer_clarity": 90,
        "focal_relevance": 87,
        "product_relevance": 89,
        "business_specific_relevance": 88,
        "visual_storytelling": 86,
        "commercial_sophistication": 87,
        "originality": 86,
        "scroll_stopping_strength": 84,
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
    return CreativeVisualReview.model_validate(values)


def _review_at_score(score: int) -> CreativeVisualReview:
    return _review(
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


def _request(review_mode: str = "offering_proof") -> CreativeVisualReviewRequest:
    return CreativeVisualReviewRequest(
        final_png=b"\x89PNG\r\n\x1a\ntransient-test",
        campaign_objective="promotional offer",
        channel="instagram",
        concept_name="Cinematic Value Moment",
        concept_expectations="A product-led AI operations workspace showing coordinated automation.",
        expected_headline="Run your business with an AI team",
        expected_offer="50% OFF",
        expected_cta="Claim the offer",
        brand_expectations="Visible identity with controlled #123456 palette.",
        quality_threshold=82,
        review_mode=review_mode,  # type: ignore[arg-type]
    )


class CreativeVisualReviewSchemaTests(TestCase):
    def test_request_boundary_cannot_carry_business_brain_or_crm_dump(self) -> None:
        request = _request()
        fields = set(request.__dataclass_fields__)
        self.assertFalse(
            fields
            & {
                "business_brain",
                "crm",
                "customer_records",
                "research_urls",
                "raw_provider_response",
                "source_image",
            }
        )
        task = build_visual_review_task(request).casefold()
        self.assertNotIn("business brain", task)
        self.assertNotIn("customer record", task)

    def test_giant_duplicate_offer_is_classified_as_raw_visual(self) -> None:
        review = _review(
            approved=False,
            repair_class="raw_visual",
            duplicated_message=True,
            accidental_generated_text=True,
            hard_failures=("accidental_generated_text", "duplicated_message"),
            repair_instructions="Remove the giant accidental 50% OFF from the art.",
        )
        self.assertEqual(review.repair_class, "raw_visual")
        self.assertTrue(review.duplicated_message)

    def test_duplicate_only_is_local_layout_repair(self) -> None:
        review = _review(
            approved=False,
            repair_class="layout",
            duplicated_message=True,
            hard_failures=("duplicated_message",),
            repair_instructions="Suppress the repeated deterministic supporting line.",
        )
        self.assertEqual(review.repair_class, "layout")

    def test_accidental_raw_text_remains_raw_visual_repair(self) -> None:
        review = _review(
            approved=False,
            repair_class="raw_visual",
            accidental_generated_text=True,
            hard_failures=("accidental_generated_text",),
            repair_instructions="Regenerate the raw visual without generated text.",
        )
        self.assertEqual(review.repair_class, "raw_visual")

    def test_review_mode_is_explicit_and_invalid_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "mode is invalid"):
            _request("invalid")

    def test_brand_offer_task_forbids_invention_without_product_only_rules(self) -> None:
        task = build_visual_review_task(_request("brand_offer")).casefold()
        self.assertIn("review mode: brand_offer", task)
        self.assertIn("no_product_service_story must be false", task)
        self.assertIn("do not require or invent product/service proof", task)
        self.assertNotIn("without meaningful product/service storytelling", task)
        self.assertNotIn("supplied concept/product story", task)

    def test_brand_offer_rejects_mode_contradictory_typed_result(self) -> None:
        review = _review(
            approved=False,
            repair_class="raw_visual",
            no_product_service_story=True,
            hard_failures=("no_product_service_story",),
            repair_instructions="Invent a product story.",
        )
        with self.assertRaises(CreativeVisualReviewProviderError):
            validate_visual_review_for_mode(review, story_mode="brand_offer")
        self.assertIs(
            validate_visual_review_for_mode(review, story_mode="offering_proof"),
            review,
        )

    def test_inconsistent_approved_decision_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            _review(duplicated_message=True)

    def test_approved_review_keeps_fixed_dimension_floor_of_68(self) -> None:
        with self.assertRaises(ValidationError):
            _review(hierarchy=67)

    def test_exact_runtime_semantic_threshold_passes(self) -> None:
        review = _review_at_score(82)

        self.assertEqual(semantic_visual_quality_score(review), 82)
        self.assertTrue(
            semantic_visual_review_meets_threshold(review, threshold=82)
        )

    def test_approved_review_below_runtime_semantic_threshold_is_rejected(self) -> None:
        review = _review_at_score(81)

        self.assertEqual(semantic_visual_quality_score(review), 81)
        self.assertFalse(
            semantic_visual_review_meets_threshold(review, threshold=82)
        )

    def test_lower_configured_runtime_semantic_threshold_passes(self) -> None:
        review = _review_at_score(75)

        self.assertEqual(semantic_visual_quality_score(review), 75)
        self.assertTrue(
            semantic_visual_review_meets_threshold(review, threshold=75)
        )

    def test_runtime_semantic_threshold_is_bounded(self) -> None:
        review = _review_at_score(82)

        for threshold in (59, 96):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                semantic_visual_review_meets_threshold(
                    review,
                    threshold=threshold,
                )

    def test_generic_abstract_saas_visual_is_rejected_for_raw_repair(self) -> None:
        review = _review(
            approved=False,
            repair_class="raw_visual",
            focal_relevance=42,
            product_relevance=28,
            campaign_alignment=45,
            irrelevant_decorative_art=True,
            meaningless_focal_story=True,
            hard_failures=("irrelevant_decorative_art", "meaningless_focal_story"),
            repair_instructions="Regenerate a product-relevant operational story.",
        )
        self.assertEqual(review.repair_class, "raw_visual")

    def test_replaceable_brand_creative_requires_raw_regeneration(self) -> None:
        review = _review(
            approved=False,
            repair_class="raw_visual",
            business_specific_relevance=38,
            replaceable_brand_creative=True,
            decorative_abstraction_dominates=True,
            no_product_service_story=True,
            hard_failures=(
                "replaceable_brand_creative",
                "decorative_abstraction_dominates",
                "no_product_service_story",
            ),
            repair_instructions="Replace decoration with a specific product story.",
        )
        self.assertEqual(review.repair_class, "raw_visual")

    def test_awkward_headline_wrap_is_rejected_for_layout_repair(self) -> None:
        review = _review(
            approved=False,
            repair_class="layout",
            typography_quality=40,
            unnatural_headline_wrapping=True,
            hard_failures=("unnatural_headline_wrapping",),
            repair_instructions="Use a wider copy zone and a two-line headline.",
        )
        self.assertEqual(review.repair_class, "layout")


class OpenAIVisualReviewAdapterTests(IsolatedAsyncioTestCase):
    async def test_uses_typed_transient_image_input_without_storage(self) -> None:
        response = SimpleNamespace(
            status="completed",
            _request_id="req_visual_1",
            usage=SimpleNamespace(input_tokens=123, output_tokens=45),
            output=[
                SimpleNamespace(
                    type="message",
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            parsed=_review().model_dump(),
                        )
                    ],
                )
            ],
        )
        parse = AsyncMock(return_value=response)
        client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
        provider = OpenAICreativeVisualReviewProvider(
            client=client,
            model="gpt-test",
            max_output_tokens=1_600,
        )

        result = await provider.review(_request())

        self.assertTrue(result.review.approved)
        self.assertEqual(result.metadata.provider_request_id, "req_visual_1")
        kwargs = parse.await_args.kwargs
        self.assertFalse(kwargs["store"])
        self.assertIs(kwargs["text_format"], CreativeVisualReview)
        self.assertEqual(kwargs["max_output_tokens"], 1_600)
        user_content = kwargs["input"][1]["content"]
        self.assertEqual(user_content[1]["type"], "input_image")
        self.assertEqual(user_content[1]["detail"], "high")
        self.assertTrue(
            user_content[1]["image_url"].startswith("data:image/png;base64,")
        )
        self.assertNotIn(_request().final_png.decode("latin1"), str(kwargs))

    async def test_incomplete_or_ambiguous_provider_output_fails_closed(self) -> None:
        valid_message = SimpleNamespace(
            type="message",
            content=[
                SimpleNamespace(type="output_text", parsed=_review().model_dump())
            ],
        )
        responses = (
            SimpleNamespace(status="incomplete", output=()),
            SimpleNamespace(status="completed", output=()),
            SimpleNamespace(status="completed", output=[valid_message, valid_message]),
        )
        for response in responses:
            with self.subTest(response=response):
                provider = OpenAICreativeVisualReviewProvider(
                    client=SimpleNamespace(
                        responses=SimpleNamespace(
                            parse=AsyncMock(return_value=response)
                        )
                    ),
                    model="gpt-test",
                    max_output_tokens=1_600,
                )
                with self.assertRaises(CreativeVisualReviewProviderError):
                    await provider.review(_request())

def test_maximum_valid_visual_review_request_stays_within_task_budget() -> None:
    """
    Every individually valid request field must fit inside the fixed semantic
    critic policy boundary without truncating campaign truth or review rules.
    """
    from io import BytesIO

    from PIL import Image

    from app.services.creative_visual_review import (
        CreativeVisualReviewRequest,
        build_visual_review_task,
    )

    image = Image.new("RGB", (1, 1), (255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    for review_mode in ("offering_proof", "brand_offer"):
        request = CreativeVisualReviewRequest(
            final_png=buffer.getvalue(),
            campaign_objective="O" * 120,
            channel="C" * 40,
            concept_name="N" * 100,
            concept_expectations="E" * 600,
            expected_headline="H" * 180,
            expected_offer="F" * 160,
            expected_cta="T" * 300,
            brand_expectations="B" * 400,
            quality_threshold=95,
            review_mode=review_mode,
        )

        task = build_visual_review_task(request)

        assert len(task) <= 9000

        # Formatting must reach the model as actual structure rather than escaped
        # backslash+n text.
        assert "\\n" not in task

        for marker in (
            "CAMPAIGN EXPECTATIONS:",
            "SCORING STANDARD:",
            "FOUR NON-NEGOTIABLE REVIEW LAYERS:",
            "MANDATORY FAILURE MAPPING:",
            "APPROVAL TEST:",
        ):
            assert marker in task
