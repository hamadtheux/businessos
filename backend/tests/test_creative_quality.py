from __future__ import annotations

import os
from dataclasses import replace
from io import BytesIO
from unittest import TestCase

from PIL import Image, ImageDraw

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.services.creative_compositor import (  # noqa: E402
    CreativeCompositionInput,
    CreativeCompositionResult,
    CreativeCompositor,
)
from app.services.creative_layout import analyze_visual, choose_text_side  # noqa: E402
from app.services.creative_quality import assess_creative_quality  # noqa: E402


def _visual(*, busy_side: str | None = None, noisy: bool = False) -> bytes:
    image = Image.new("RGB", (800, 600), (130, 130, 130))
    draw = ImageDraw.Draw(image)
    start = 0 if busy_side == "left" else 400
    end = 400 if busy_side == "left" else 800
    if noisy:
        start, end = 0, 800
    if busy_side or noisy:
        for y in range(0, 600, 8):
            for x in range(start, end, 8):
                fill = (248, 248, 248) if (x // 8 + y // 8) % 2 else (8, 8, 8)
                draw.rectangle((x, y, x + 7, y + 7), fill=fill)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _composition(raw_visual: bytes, *, offer: str | None = None):
    return CreativeCompositor().compose(
        CreativeCompositionInput(
            raw_visual=raw_visual,
            target_width=640,
            target_height=640,
            asset_type="social_square",
            headline="Run your business with an AI team",
            supporting_copy="Automate the work that slows your team down.",
            offer=offer,
            cta="Explore now",
            business_name="9D Brain",
            primary_color="#123456",
            secondary_color="#F4F0E8",
            accent_color="#D27D2D",
            negative_space="Reserve copy space on the right.",
            channel="instagram",
        )
    )


class CreativeLayoutAndQualityTests(TestCase):
    def test_pixel_analysis_overrides_requested_side_when_it_is_busy(self) -> None:
        with Image.open(BytesIO(_visual(busy_side="right"))) as image:
            analysis = analyze_visual(image)

        self.assertEqual(choose_text_side(analysis, requested="right"), "left")
        result = _composition(_visual(busy_side="right"))
        self.assertEqual(result.quality.text_side, "left")
        self.assertGreater(result.quality.selected_zone_quiet_score, 0.3)

    def test_offer_is_separate_exact_component_and_does_not_overlap(self) -> None:
        result = _composition(_visual(), offer="50% OFF")

        self.assertEqual(result.quality.rendered_text["offer"], "50% OFF")
        self.assertNotEqual(result.quality.rendered_text["headline"], "50% OFF")
        offer = result.quality.text_bounds["offer"]
        headline = result.quality.text_bounds["headline"]
        self.assertLessEqual(offer[3], headline[1])
        for name in ("offer", "cta"):
            text = result.quality.text_bounds[name]
            panel = result.quality.component_bounds[name]
            self.assertGreater(result.quality.font_sizes[name], 0)
            self.assertGreaterEqual(text[0], panel[0])
            self.assertGreaterEqual(text[1], panel[1])
            self.assertLessEqual(text[2], panel[2])
            self.assertLessEqual(text[3], panel[3])
        self.assertNotEqual(
            result.quality.font_sizes["cta"],
            round(min(result.width, result.height) * 0.025),
        )
        assessment = assess_creative_quality(result, threshold=82)
        self.assertTrue(assessment.approved_for_delivery)

    def test_candidates_are_ranked_by_post_render_evidence(self) -> None:
        candidates = CreativeCompositor().compose_candidates(
            CreativeCompositionInput(
                raw_visual=_visual(),
                target_width=640,
                target_height=640,
                asset_type="social_square",
                headline="A compact headline",
                supporting_copy=(
                    "A deliberately longer supporting explanation that makes actual "
                    "font fit and spacing differ across local layout families."
                ),
                offer="Save today",
                cta="Explore now",
                business_name="9D Brain",
                composition_direction="framed campaign with ordered information",
                negative_space="balanced working space",
                channel="instagram",
            )
        )

        self.assertGreaterEqual(len(candidates), 2)
        scores = [candidate.quality.layout_score for candidate in candidates]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(
            any(
                candidate.quality.layout_score
                != candidate.quality.pre_render_layout_score
                for candidate in candidates
            )
        )

    def test_second_best_pre_render_family_can_win_after_actual_composition(self) -> None:
        candidates = CreativeCompositor().compose_candidates(
            CreativeCompositionInput(
                raw_visual=_visual(),
                target_width=640,
                target_height=640,
                asset_type="social_square",
                headline=(
                    "A much longer campaign headline designed to test actual "
                    "hierarchy across each composition family"
                ),
                supporting_copy=(
                    "This detailed supporting message is deliberately long enough to "
                    "force meaningfully different wrapping and actual fitted type "
                    "sizes across the local composition families while staying within "
                    "the accepted exact-copy constraints."
                ),
                offer="Save today",
                cta="Explore now",
                business_name="9D Brain",
                composition_direction="framed campaign",
                negative_space="balanced",
                channel="instagram",
            )
        )

        self.assertGreaterEqual(len(candidates), 2)
        strongest_pre_render = max(
            candidates,
            key=lambda candidate: candidate.quality.pre_render_layout_score,
        )
        self.assertIsNot(candidates[0], strongest_pre_render)
        self.assertGreater(
            candidates[0].quality.layout_score,
            strongest_pre_render.quality.layout_score,
        )

    def test_full_cta_and_offer_panels_enforce_overlap_and_safe_area(self) -> None:
        result = _composition(_visual(), offer="50% OFF")
        headline = result.quality.text_bounds["headline"]
        for name in ("cta", "offer"):
            with self.subTest(name=name):
                components = dict(result.quality.component_bounds)
                components[name] = headline
                broken = assess_creative_quality(
                    replace(
                        result,
                        quality=replace(result.quality, component_bounds=components),
                    ),
                    threshold=82,
                )
                self.assertTrue(
                    any(name in failure and "overlap" in failure for failure in broken.hard_failures)
                )

        unsafe_components = dict(result.quality.component_bounds)
        cta = unsafe_components["cta"]
        unsafe_components["cta"] = (
            result.quality.safe_margin - 10,
            cta[1],
            cta[2],
            cta[3],
        )
        unsafe = assess_creative_quality(
            replace(
                result,
                quality=replace(
                    result.quality,
                    component_bounds=unsafe_components,
                ),
            ),
            threshold=82,
        )
        self.assertIn("component_outside_safe_area", unsafe.hard_failures)

    def test_weak_cta_and_spacing_are_layout_quality_not_raw_visual(self) -> None:
        result = _composition(_visual())
        font_sizes = dict(result.quality.font_sizes)
        font_sizes["cta"] = 2
        weak_report = replace(
            result.quality,
            font_sizes=font_sizes,
            cta_contrast_ratio=4.5,
            layout_score=60,
            content_centroid_offset=0.9,
        )
        assessment = assess_creative_quality(
            replace(result, quality=weak_report),
            threshold=90,
        )
        self.assertFalse(assessment.approved_for_delivery)
        self.assertEqual(assessment.failure_kind, "layout")

    def test_awkward_short_headline_wrap_is_a_layout_hard_failure(self) -> None:
        result = _composition(_visual())
        awkward = replace(
            result.quality,
            rendered_text={
                **result.quality.rendered_text,
                "headline": "Meet\n9D\nBrain.",
            },
            line_counts={**result.quality.line_counts, "headline": 3},
            headline_wrap_quality=0,
            headline_wrap_violations=(
                "excessive_lines_for_short_headline",
                "brand_or_product_name_split",
            ),
        )
        assessment = assess_creative_quality(
            replace(result, quality=awkward),
            threshold=82,
        )

        self.assertFalse(assessment.approved_for_delivery)
        self.assertEqual(assessment.failure_kind, "layout")
        self.assertIn("unnatural_headline_wrapping", assessment.hard_failures)
        self.assertLess(assessment.typography_quality, 60)

    def test_extreme_whitespace_requires_explicit_intent(self) -> None:
        result = _composition(_visual())
        extreme = replace(
            result.quality,
            occupied_area_ratio=0.03,
            largest_empty_edge_ratio=0.62,
            intentional_negative_space=False,
        )
        rejected = assess_creative_quality(
            replace(result, quality=extreme),
            threshold=82,
        )
        self.assertIn("extreme_meaningless_whitespace", rejected.hard_failures)

        intentional = replace(extreme, intentional_negative_space=True)
        accepted_geometry = assess_creative_quality(
            replace(result, quality=intentional),
            threshold=82,
        )
        self.assertNotIn(
            "extreme_meaningless_whitespace",
            accepted_geometry.hard_failures,
        )
        self.assertTrue(accepted_geometry.approved_for_delivery)

    def test_valid_composition_passes_and_overlap_is_a_hard_failure(self) -> None:
        result = _composition(_visual())
        valid = assess_creative_quality(result, threshold=82)
        self.assertTrue(valid.approved_for_delivery)

        broken_bounds = dict(result.quality.text_bounds)
        broken_bounds["cta"] = broken_bounds["headline"]
        broken_report = replace(result.quality, text_bounds=broken_bounds)
        broken_result = CreativeCompositionResult(
            content=result.content,
            width=result.width,
            height=result.height,
            selected_layout=result.selected_layout,
            quality=broken_report,
        )
        broken = assess_creative_quality(broken_result, threshold=82)
        self.assertFalse(broken.approved_for_delivery)
        self.assertTrue(any("overlap" in value for value in broken.hard_failures))

    def test_noisy_visual_is_classified_for_bounded_raw_regeneration(self) -> None:
        result = _composition(_visual(noisy=True))
        # The real compositor first repairs this fixture with a solid editorial
        # split. Force an overlay report to exercise the raw-visual failure
        # classification used only after layout candidates are exhausted.
        overlay_report = replace(
            result.quality,
            selected_layout="minimal_hero",
        )
        overlay_result = CreativeCompositionResult(
            content=result.content,
            width=result.width,
            height=result.height,
            selected_layout="minimal_hero",
            quality=overlay_report,
        )
        assessment = assess_creative_quality(overlay_result, threshold=82)

        self.assertFalse(assessment.approved_for_delivery)
        self.assertEqual(assessment.failure_kind, "raw_visual")
        self.assertIn("raw_visual_too_complex", assessment.hard_failures)
