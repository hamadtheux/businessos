from __future__ import annotations

import os
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

from PIL import Image, ImageDraw

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.services.creative_compositor import (  # noqa: E402
    MAX_FINAL_PNG_BYTES,
    CreativeCompositionError,
    CreativeCompositionInput,
    CreativeCompositor,
    resolve_final_dimensions,
)
from app.services import creative_compositor as compositor_module  # noqa: E402


def _image_bytes(
    width: int = 800,
    height: int = 600,
    color: tuple[int, int, int] = (128, 128, 128),
) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


def _logo_bytes(*, transparent: bool = True) -> bytes:
    image = Image.new("RGBA", (240, 80), (0, 0, 0, 0) if transparent else "white")
    ImageDraw.Draw(image).rounded_rectangle((40, 12, 200, 68), radius=16, fill=(220, 40, 40, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _dark_logo_bytes() -> bytes:
    image = Image.new("RGBA", (240, 80), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((30, 12, 210, 68), fill=(8, 8, 8, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _mixed_image_bytes() -> bytes:
    image = Image.new("RGB", (800, 600), (20, 20, 20))
    draw = ImageDraw.Draw(image)
    cell_width = image.width // 8
    cell_height = image.height // 8
    for row in range(8):
        for column in range(8):
            if (row + column) % 2 == 0:
                draw.rectangle(
                    (
                        column * cell_width,
                        row * cell_height,
                        (column + 1) * cell_width,
                        (row + 1) * cell_height,
                    ),
                    fill=(240, 240, 240),
                )
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _input(**overrides: object) -> CreativeCompositionInput:
    values: dict[str, object] = {
        "raw_visual": _image_bytes(),
        "target_width": 640,
        "target_height": 640,
        "asset_type": "social_square",
        "headline": "Made for the moment",
        "supporting_copy": "A considered product story with clear, grounded value.",
        "cta": "Explore the collection",
        "business_name": "Acme Studio",
        "primary_color": "#123456",
        "secondary_color": "#F4F0E8",
        "accent_color": "#D27D2D",
        "composition_direction": "Keep the hero subject on the left.",
        "negative_space": "Reserve negative space on the right.",
        "channel": "instagram",
    }
    values.update(overrides)
    return CreativeCompositionInput(**values)  # type: ignore[arg-type]


class CreativeCompositorTests(TestCase):
    def test_format_defaults_are_centralized_and_exact(self) -> None:
        self.assertEqual(resolve_final_dimensions("social_square", None, None, None), (1080, 1080))
        self.assertEqual(resolve_final_dimensions("story_reel", None, None, None), (1080, 1920))
        self.assertEqual(resolve_final_dimensions("landscape_ad", None, None, None), (1200, 628))
        self.assertEqual(resolve_final_dimensions("other", 800, 900, None), (800, 900))
        with self.assertRaises(CreativeCompositionError):
            resolve_final_dimensions("creative_brief", None, None, None)

    def test_square_composition_is_valid_exact_bounded_png(self) -> None:
        result = CreativeCompositor().compose(_input())

        self.assertEqual((result.width, result.height), (640, 640))
        self.assertEqual(result.selected_layout, "minimal_hero")
        self.assertTrue(result.quality.valid_png)
        self.assertTrue(result.quality.exact_dimensions)
        self.assertLess(len(result.content), MAX_FINAL_PNG_BYTES)
        with Image.open(BytesIO(result.content)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (640, 640))

    def test_story_uses_vertical_safe_layout(self) -> None:
        result = CreativeCompositor().compose(
            _input(
                target_width=640,
                target_height=1136,
                asset_type="story_reel",
            )
        )

        self.assertEqual(result.selected_layout, "vertical_story")
        self.assertGreater(result.quality.text_bounds["headline"][1], 113)
        self.assertLess(result.quality.text_bounds["cta"][3], 1136 - 147)

    def test_landscape_uses_editorial_split_without_distortion(self) -> None:
        result = CreativeCompositor().compose(
            _input(
                raw_visual=_image_bytes(900, 450),
                target_width=960,
                target_height=500,
                asset_type="landscape_ad",
            )
        )

        self.assertEqual(result.selected_layout, "editorial_split")
        self.assertEqual(result.quality.source_dimensions, (900, 450))
        self.assertEqual(result.quality.source_aspect_ratio, 2.0)
        self.assertNotEqual(result.quality.rendered_aspect_ratio, 2.0)

    def test_framed_campaign_is_reachable_and_valid(self) -> None:
        result = CreativeCompositor().compose(
            _input(
                raw_visual=_image_bytes(color=(30, 30, 30)),
                headline="A considered campaign message for every meaningful moment",
            )
        )

        self.assertEqual(result.selected_layout, "framed_campaign")
        self.assertTrue(result.quality.valid_png)
        self.assertEqual((result.width, result.height), (640, 640))

    def test_cinematic_overlay_is_reachable_and_valid(self) -> None:
        result = CreativeCompositor().compose(
            _input(
                raw_visual=_image_bytes(color=(130, 130, 130)),
                headline="A considered campaign message for the moments that matter",
            )
        )

        self.assertEqual(result.selected_layout, "cinematic_overlay")
        self.assertIn(
            result.quality.contrast_treatment,
            {"controlled_gradient", "gradient_and_scrim"},
        )
        self.assertGreaterEqual(result.quality.minimum_contrast_ratio, 4.5)

    def test_minimal_hero_places_copy_on_right_for_right_negative_space(self) -> None:
        result = CreativeCompositor().compose(
            _input(negative_space="Generous negative space on the right.")
        )

        headline = result.quality.text_bounds["headline"]
        self.assertEqual(result.selected_layout, "minimal_hero")
        self.assertEqual(result.quality.text_side, "right")
        self.assertGreater((headline[0] + headline[2]) / 2, result.width / 2)

    def test_minimal_hero_places_copy_on_left_for_left_negative_space(self) -> None:
        result = CreativeCompositor().compose(
            _input(negative_space="Generous negative space on the left.")
        )

        headline = result.quality.text_bounds["headline"]
        self.assertEqual(result.selected_layout, "minimal_hero")
        self.assertEqual(result.quality.text_side, "left")
        self.assertLess((headline[0] + headline[2]) / 2, result.width / 2)

    def test_cinematic_gradient_is_strongest_on_right_side_copy(self) -> None:
        result = CreativeCompositor().compose(
            _input(
                raw_visual=_image_bytes(color=(130, 130, 130)),
                headline="A considered campaign message for the moments that matter",
                negative_space="Keep generous negative space on the right.",
            )
        )

        with Image.open(BytesIO(result.content)) as image:
            weak_side = image.getpixel((170, 200))
            strong_side = image.getpixel((600, 200))
        self.assertEqual(result.selected_layout, "cinematic_overlay")
        self.assertEqual(result.quality.text_side, "right")
        self.assertGreater(sum(strong_side), sum(weak_side))

    def test_text_and_cta_remain_inside_safe_bounds(self) -> None:
        result = CreativeCompositor().compose(_input())
        margin = result.quality.safe_margin
        for bounds in result.quality.text_bounds.values():
            self.assertGreaterEqual(bounds[0], margin)
            self.assertGreaterEqual(bounds[1], margin)
            self.assertLessEqual(bounds[2], result.width - margin)
            self.assertLessEqual(bounds[3], result.height - margin)

    def test_headline_wraps_without_changing_exact_copy(self) -> None:
        headline = (
            "A premium everyday experience designed with thoughtful detail, "
            "clear purpose, and confidence for every important moment"
        )
        result = CreativeCompositor().compose(_input(headline=headline))

        actual = result.quality.rendered_text["headline"]
        self.assertIn("\n", actual)
        self.assertNotEqual(actual, headline)
        self.assertEqual(_normalize(actual), _normalize(headline))
        headline_bounds = result.quality.text_bounds["headline"]
        self.assertLessEqual(headline_bounds[2], result.width - result.quality.safe_margin)

    def test_headline_integrity_gate_rejects_a_dropped_character(self) -> None:
        headline = "Made for every important moment"
        original_wrap = compositor_module._wrap_text

        def corrupt(draw, text, font, max_width):
            lines = original_wrap(draw, text, font, max_width)
            if text == headline:
                lines[-1] = lines[-1][:-1]
            return lines

        with patch.object(compositor_module, "_wrap_text", side_effect=corrupt):
            with self.assertRaises(CreativeCompositionError):
                CreativeCompositor().compose(_input(headline=headline))

    def test_cta_integrity_gate_rejects_a_dropped_character(self) -> None:
        cta = "Explore every option today"
        original_wrap = compositor_module._wrap_text

        def corrupt(draw, text, font, max_width):
            lines = original_wrap(draw, text, font, max_width)
            if text == cta:
                lines[-1] = lines[-1][:-1]
            return lines

        with patch.object(compositor_module, "_wrap_text", side_effect=corrupt):
            with self.assertRaises(CreativeCompositionError):
                CreativeCompositor().compose(_input(cta=cta))

    def test_impossible_headline_fails_instead_of_truncating(self) -> None:
        with self.assertRaises(CreativeCompositionError):
            CreativeCompositor().compose(
                _input(
                    target_width=320,
                    target_height=320,
                    headline="W" * 180,
                    supporting_copy="S" * 600,
                    cta="C" * 300,
                    business_name="B" * 180,
                )
            )

    def test_mid_tone_visual_receives_accessible_contrast_treatment(self) -> None:
        result = CreativeCompositor().compose(
            _input(raw_visual=_image_bytes(color=(130, 130, 130)))
        )

        self.assertIn(
            result.quality.contrast_treatment,
            {"controlled_gradient", "gradient_and_scrim"},
        )
        self.assertGreaterEqual(result.quality.minimum_contrast_ratio, 4.5)

    def test_mixed_light_and_dark_visual_enforces_conservative_contrast(self) -> None:
        result = CreativeCompositor().compose(
            _input(
                raw_visual=_mixed_image_bytes(),
                headline="A considered campaign message for the moments that matter",
            )
        )

        self.assertEqual(result.selected_layout, "cinematic_overlay")
        self.assertGreaterEqual(result.quality.minimum_contrast_ratio, 4.5)
        self.assertIn(
            result.quality.contrast_treatment,
            {"controlled_gradient", "gradient_and_scrim"},
        )

    def test_logo_aspect_ratio_and_transparency_are_preserved(self) -> None:
        result = CreativeCompositor().compose(
            _input(
                target_width=960,
                target_height=500,
                asset_type="landscape_ad",
                logo_content=_logo_bytes(),
            )
        )

        self.assertIsNotNone(result.quality.logo_bounds)
        self.assertAlmostEqual(result.quality.logo_source_aspect_ratio or 0, 3.0, places=2)
        self.assertAlmostEqual(result.quality.logo_rendered_aspect_ratio or 0, 3.0, places=1)
        left, top, right, bottom = result.quality.logo_bounds or (0, 0, 0, 0)
        with Image.open(BytesIO(result.content)) as image:
            corner = image.getpixel((left + 1, top + 1))
            center = image.getpixel(((left + right) // 2, (top + bottom) // 2))
        self.assertNotEqual(corner, center)
        self.assertGreater(center[0], center[1])

    def test_dark_logo_gets_visibility_plate_without_recoloring(self) -> None:
        result = CreativeCompositor().compose(
            _input(
                raw_visual=_image_bytes(color=(12, 12, 12)),
                logo_content=_dark_logo_bytes(),
                negative_space="Generous negative space on the right.",
            )
        )

        left, top, right, bottom = result.quality.logo_bounds or (0, 0, 0, 0)
        with Image.open(BytesIO(result.content)) as image:
            logo_center = image.getpixel(((left + right) // 2, (top + bottom) // 2))
            plate = image.getpixel((left - 3, (top + bottom) // 2))
        self.assertEqual(result.quality.logo_treatment, "contrast_plate")
        self.assertEqual(logo_center, (8, 8, 8))
        self.assertGreater(sum(plate), sum(logo_center))

    def test_missing_logo_uses_exact_business_name(self) -> None:
        result = CreativeCompositor().compose(_input(logo_content=None))
        self.assertIn("business_name", result.quality.text_bounds)
        self.assertIsNone(result.quality.logo_bounds)
        self.assertEqual(
            _normalize(result.quality.rendered_text["business_name"]),
            "Acme Studio",
        )

    def test_corrupt_raw_image_and_logo_fail_closed(self) -> None:
        with self.assertRaises(CreativeCompositionError):
            CreativeCompositor().compose(_input(raw_visual=b"corrupt-image"))
        with self.assertRaises(CreativeCompositionError):
            CreativeCompositor().compose(_input(logo_content=b"corrupt-logo"))

    def test_unsupported_source_dimensions_fail_closed(self) -> None:
        with self.assertRaises(CreativeCompositionError):
            CreativeCompositor().compose(
                _input(raw_visual=_image_bytes(4097, 320))
            )

    def test_unsupported_typography_fails_instead_of_rendering_missing_glyphs(self) -> None:
        with self.assertRaisesRegex(CreativeCompositionError, "does not support"):
            CreativeCompositor().compose(_input(headline="مرحبا بالعالم"))

    def test_crop_focal_direction_does_not_change_output_dimensions(self) -> None:
        result = CreativeCompositor().compose(
            _input(
                raw_visual=_image_bytes(1200, 500),
                negative_space="Reserve negative space on the left.",
            )
        )
        self.assertEqual((result.width, result.height), (640, 640))
        self.assertEqual(result.quality.source_dimensions, (1200, 500))
