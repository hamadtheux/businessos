from __future__ import annotations

import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal
from unicodedata import category

from PIL import (
    Image,
    ImageChops,
    ImageColor,
    ImageDraw,
    ImageFont,
    ImageOps,
    ImageStat,
    UnidentifiedImageError,
)


LayoutFamily = Literal[
    "editorial_split",
    "cinematic_overlay",
    "minimal_hero",
    "framed_campaign",
    "vertical_story",
]
TextSide = Literal["left", "right"]
LogoTreatment = Literal["none", "contrast_plate"]

DEFAULT_CREATIVE_DIMENSIONS: dict[str, tuple[int, int]] = {
    "social_square": (1080, 1080),
    "story_reel": (1080, 1920),
    "landscape_ad": (1200, 628),
    "display_banner": (1200, 628),
    "other": (1080, 1080),
}

MAX_RAW_VISUAL_BYTES = 30 * 1024 * 1024
MAX_FINAL_PNG_BYTES = 30 * 1024 * 1024
MAX_LOGO_BYTES = 5_000_000
MAX_RENDER_EDGE = 4096
MAX_RENDER_PIXELS = 16_000_000
_SUPPORTED_SOURCE_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})

Box = tuple[int, int, int, int]
RGB = tuple[int, int, int]


class CreativeCompositionError(RuntimeError):
    """The raw visual could not be converted into a safe final creative."""


@dataclass(frozen=True, slots=True)
class CreativeCompositionInput:
    raw_visual: bytes
    target_width: int
    target_height: int
    asset_type: str
    headline: str
    supporting_copy: str
    cta: str | None
    business_name: str
    primary_color: str | None = None
    secondary_color: str | None = None
    accent_color: str | None = None
    logo_content: bytes | None = None
    composition_direction: str = ""
    negative_space: str = ""
    channel: str = "other"

    def __post_init__(self) -> None:
        if not self.raw_visual or len(self.raw_visual) > MAX_RAW_VISUAL_BYTES:
            raise CreativeCompositionError("Raw visual size is invalid")
        if (
            self.target_width < 320
            or self.target_height < 320
            or self.target_width > MAX_RENDER_EDGE
            or self.target_height > MAX_RENDER_EDGE
            or self.target_width * self.target_height > MAX_RENDER_PIXELS
        ):
            raise CreativeCompositionError("Final creative dimensions are unsupported")
        _bounded_text(self.headline, "headline", 180)
        _bounded_text(self.supporting_copy, "supporting copy", 600)
        if self.cta is not None:
            _bounded_text(self.cta, "CTA", 300)
        _bounded_text(self.business_name, "business name", 180)
        if self.logo_content is not None and len(self.logo_content) > MAX_LOGO_BYTES:
            raise CreativeCompositionError("Logo size is invalid")


@dataclass(frozen=True, slots=True)
class CreativeQualityReport:
    valid_png: bool
    exact_dimensions: bool
    output_bytes: int
    selected_layout: LayoutFamily
    safe_margin: int
    text_bounds: dict[str, Box]
    logo_bounds: Box | None
    minimum_contrast_ratio: float
    contrast_treatment: str
    source_dimensions: tuple[int, int]
    source_aspect_ratio: float
    rendered_aspect_ratio: float
    logo_source_aspect_ratio: float | None
    logo_rendered_aspect_ratio: float | None
    logo_treatment: LogoTreatment
    text_side: TextSide
    rendered_text: dict[str, str]


@dataclass(frozen=True, slots=True)
class CreativeCompositionResult:
    content: bytes
    width: int
    height: int
    selected_layout: LayoutFamily
    quality: CreativeQualityReport


@dataclass(frozen=True, slots=True)
class _TextFit:
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    font_size: int
    text: str
    origin: tuple[int, int]
    bounds: Box
    line_count: int
    spacing: int


@dataclass(frozen=True, slots=True)
class _LayoutPlan:
    family: LayoutFamily
    image_box: Box
    brand_box: Box
    headline_box: Box
    supporting_box: Box
    cta_box: Box | None
    text_surface: Box
    overlay: bool
    panel_color: RGB | None
    text_color: RGB | None


class CreativeCompositor:
    """Deterministic brand compositor with a configurable local font path."""

    def __init__(self, *, font_path: Path | None = None) -> None:
        if font_path is not None and not font_path.is_file():
            raise ValueError("Configured creative font does not exist")
        self._font_path = font_path
        # Instance-local caching avoids repeated font decoding while keeping
        # rendering state bounded and isolated between concurrent jobs.
        self._font_cache: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    def compose(self, value: CreativeCompositionInput) -> CreativeCompositionResult:
        self._validate_typography_coverage(value)
        source = _load_source_visual(value.raw_visual)
        logo, logo_source_ratio = _load_logo(value.logo_content)
        palette = _palette(value)
        preferred = _select_layout(value, source)
        candidates = _layout_candidates(preferred, value)
        text_side = (
            _resolve_text_side(
                value.negative_space,
                value.composition_direction,
            )
            or "left"
        )
        last_error: CreativeCompositionError | None = None

        for family in candidates:
            try:
                return self._compose_layout(
                    value,
                    source,
                    logo,
                    logo_source_ratio,
                    palette,
                    family,
                    text_side,
                )
            except CreativeCompositionError as exc:
                last_error = exc

        raise CreativeCompositionError(
            "Exact marketing copy cannot fit a safe professional layout"
        ) from last_error

    def _compose_layout(
        self,
        value: CreativeCompositionInput,
        source: Image.Image,
        logo: Image.Image | None,
        logo_source_ratio: float | None,
        palette: tuple[RGB, RGB, RGB],
        family: LayoutFamily,
        text_side: TextSide,
    ) -> CreativeCompositionResult:
        width, height = value.target_width, value.target_height
        margin, safe_top, safe_bottom = _safe_margins(value)
        plan = _layout_plan(
            family,
            width,
            height,
            margin,
            safe_top,
            safe_bottom,
            palette,
            bool(value.cta),
            text_side,
        )
        canvas = Image.new("RGB", (width, height), palette[1])
        focal = _focal_center(value.negative_space, value.composition_direction)
        fitted = ImageOps.fit(
            source,
            _box_size(plan.image_box),
            method=Image.Resampling.LANCZOS,
            centering=focal,
        )
        canvas.paste(fitted, plan.image_box[:2])

        draw = ImageDraw.Draw(canvas, "RGBA")
        _draw_layout_frame(draw, plan, palette, width, height, margin)

        contrast_treatment = "solid_panel"
        text_color = plan.text_color
        if plan.overlay:
            text_color, contrast_treatment = _prepare_overlay_contrast(
                canvas,
                plan.text_surface,
                family,
                text_side,
                (
                    plan.brand_box,
                    plan.headline_box,
                    plan.supporting_box,
                )
                if logo is None
                else (plan.headline_box, plan.supporting_box),
            )
        if text_color is None:
            raise CreativeCompositionError("Layout text color is unavailable")

        text_bounds: dict[str, Box] = {}
        rendered_text: dict[str, str] = {}
        identity_fit: _TextFit | None = None
        logo_bounds: Box | None = None
        logo_treatment: LogoTreatment = "none"
        if logo is not None:
            logo_bounds, logo_treatment = _draw_logo_identity(
                canvas,
                logo,
                plan.brand_box,
            )
        else:
            identity_fit = self._fit_text(
                canvas,
                value.business_name,
                plan.brand_box,
                max_size=max(18, round(canvas.height * 0.025)),
                min_size=13,
                max_lines=2,
            )
            _verify_exact_copy(
                "business name",
                value.business_name,
                identity_fit.text,
            )
            text_bounds["business_name"] = identity_fit.bounds
            rendered_text["business_name"] = identity_fit.text

        headline_fit = self._fit_text(
            canvas,
            value.headline,
            plan.headline_box,
            max_size=max(34, round(min(width, height) * 0.078)),
            min_size=max(20, round(min(width, height) * 0.027)),
            max_lines=4,
            spacing_ratio=0.13,
        )
        _verify_exact_copy("headline", value.headline, headline_fit.text)
        text_bounds["headline"] = headline_fit.bounds
        rendered_text["headline"] = headline_fit.text

        supporting_fit = self._fit_text(
            canvas,
            value.supporting_copy,
            plan.supporting_box,
            max_size=max(20, round(min(width, height) * 0.030)),
            min_size=max(14, round(min(width, height) * 0.017)),
            max_lines=9,
        )
        _verify_exact_copy(
            "supporting copy",
            value.supporting_copy,
            supporting_fit.text,
        )
        text_bounds["supporting_copy"] = supporting_fit.bounds
        rendered_text["supporting_copy"] = supporting_fit.text

        # Capture the prepared background before drawing any typography so
        # foreground pixels can never inflate the measured contrast score.
        typography_background = canvas.copy()
        minimum_contrast = _sampled_minimum_contrast(
            typography_background,
            tuple(text_bounds.values()),
            text_color,
        )
        if minimum_contrast < 4.5:
            raise CreativeCompositionError(
                "Typography contrast is below quality threshold"
            )

        if identity_fit is not None:
            _draw_text_fit(ImageDraw.Draw(canvas), identity_fit, text_color)
        _draw_text_fit(
            ImageDraw.Draw(canvas),
            headline_fit,
            text_color,
            stroke_width=max(0, round(headline_fit.font_size * 0.012)),
        )
        _draw_text_fit(ImageDraw.Draw(canvas), supporting_fit, text_color)

        if value.cta and plan.cta_box is not None:
            cta_bounds, cta_contrast, actual_cta = self._draw_cta(
                canvas,
                value.cta,
                plan.cta_box,
                palette,
            )
            text_bounds["cta"] = cta_bounds
            rendered_text["cta"] = actual_cta

        safe_box = (margin, safe_top, width - margin, height - safe_bottom)
        _validate_element_bounds(text_bounds, logo_bounds, safe_box)
        if value.cta and plan.cta_box is not None:
            minimum_contrast = min(minimum_contrast, cta_contrast)
        if minimum_contrast < 4.5:
            raise CreativeCompositionError("Typography contrast is below quality threshold")

        content = _encode_png(canvas)
        _validate_final_png(content, width, height)

        rendered_logo_ratio = (
            _box_size(logo_bounds)[0] / _box_size(logo_bounds)[1]
            if logo_bounds is not None
            else None
        )
        quality = CreativeQualityReport(
            valid_png=True,
            exact_dimensions=True,
            output_bytes=len(content),
            selected_layout=family,
            safe_margin=margin,
            text_bounds=text_bounds,
            logo_bounds=logo_bounds,
            minimum_contrast_ratio=round(minimum_contrast, 3),
            contrast_treatment=contrast_treatment,
            source_dimensions=source.size,
            source_aspect_ratio=round(source.width / source.height, 6),
            rendered_aspect_ratio=round(fitted.width / fitted.height, 6),
            logo_source_aspect_ratio=(
                round(logo_source_ratio, 6) if logo_source_ratio is not None else None
            ),
            logo_rendered_aspect_ratio=(
                round(rendered_logo_ratio, 6)
                if rendered_logo_ratio is not None
                else None
            ),
            logo_treatment=logo_treatment,
            text_side=text_side,
            rendered_text=rendered_text,
        )
        return CreativeCompositionResult(
            content=content,
            width=width,
            height=height,
            selected_layout=family,
            quality=quality,
        )

    def _font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        cached = self._font_cache.get(size)
        if cached is not None:
            return cached
        try:
            if self._font_path is not None:
                font = ImageFont.truetype(str(self._font_path), size=size)
            else:
                # No repository-owned broad Unicode font is present. Pillow's
                # embedded Aileron is deterministic and deployment-safe; the
                # explicit coverage check below rejects its missing glyphs.
                font = ImageFont.load_default(size=size)
        except (OSError, ValueError):
            raise CreativeCompositionError("Creative typography is unavailable") from None
        if len(self._font_cache) < 128:
            self._font_cache[size] = font
        return font

    def _validate_typography_coverage(
        self,
        value: CreativeCompositionInput,
    ) -> None:
        font = self._font(32)
        missing = _glyph_signature(font, "\U0010ffff")
        text_values = (
            value.headline,
            value.supporting_copy,
            value.cta or "",
            value.business_name if value.logo_content is None else "",
        )
        for character in set("".join(text_values)):
            if character.isspace() or _is_ignorable_format_character(character):
                continue
            if _glyph_signature(font, character) == missing:
                raise CreativeCompositionError(
                    "Creative font does not support the requested typography"
                )

    def _fit_text(
        self,
        image: Image.Image,
        text: str,
        box: Box,
        *,
        max_size: int,
        min_size: int,
        max_lines: int,
        spacing_ratio: float = 0.18,
    ) -> _TextFit:
        draw = ImageDraw.Draw(image)
        available_width, available_height = _box_size(box)
        for size in range(max_size, min_size - 1, -2):
            font = self._font(size)
            wrapped = _wrap_text(draw, text, font, available_width)
            if len(wrapped) > max_lines:
                continue
            spacing = max(3, round(size * spacing_ratio))
            joined = "\n".join(wrapped)
            measured = draw.multiline_textbbox(
                (0, 0), joined, font=font, spacing=spacing
            )
            measured_width = measured[2] - measured[0]
            measured_height = measured[3] - measured[1]
            if measured_width <= available_width and measured_height <= available_height:
                left = box[0] - measured[0]
                top = box[1] - measured[1]
                bounds = (
                    box[0],
                    box[1],
                    box[0] + measured_width,
                    box[1] + measured_height,
                )
                return _TextFit(
                    font=font,
                    font_size=size,
                    text=joined,
                    origin=(left, top),
                    bounds=bounds,
                    line_count=len(wrapped),
                    spacing=spacing,
                )
        raise CreativeCompositionError("Text cannot fit within safe bounds")

    def _draw_cta(
        self,
        canvas: Image.Image,
        text: str,
        box: Box,
        palette: tuple[RGB, RGB, RGB],
    ) -> tuple[Box, float, str]:
        width, height = _box_size(box)
        padding_x = max(14, round(canvas.width * 0.018))
        padding_y = max(9, round(canvas.height * 0.009))
        inner = (
            box[0] + padding_x,
            box[1] + padding_y,
            box[2] - padding_x,
            box[3] - padding_y,
        )
        fit = self._fit_text(
            canvas,
            text,
            inner,
            max_size=max(18, round(min(canvas.size) * 0.025)),
            min_size=13,
            max_lines=3,
        )
        _verify_exact_copy("CTA", text, fit.text)
        panel_width = min(width, (fit.bounds[2] - fit.bounds[0]) + padding_x * 2)
        panel_height = min(height, (fit.bounds[3] - fit.bounds[1]) + padding_y * 2)
        panel = (
            box[0],
            box[1],
            box[0] + panel_width,
            box[1] + panel_height,
        )
        fill = palette[2]
        cta_text = _best_text_color(fill)
        if _contrast_ratio(fill, cta_text) < 4.5:
            fill = palette[0]
            cta_text = _best_text_color(fill)
        radius = max(8, round(panel_height * 0.22))
        ImageDraw.Draw(canvas).rounded_rectangle(panel, radius=radius, fill=fill)
        adjusted = _TextFit(
            font=fit.font,
            font_size=fit.font_size,
            text=fit.text,
            origin=(
                panel[0] + padding_x + (fit.origin[0] - fit.bounds[0]),
                panel[1] + padding_y + (fit.origin[1] - fit.bounds[1]),
            ),
            bounds=(
                panel[0] + padding_x,
                panel[1] + padding_y,
                panel[0] + padding_x + (fit.bounds[2] - fit.bounds[0]),
                panel[1] + padding_y + (fit.bounds[3] - fit.bounds[1]),
            ),
            line_count=fit.line_count,
            spacing=fit.spacing,
        )
        _draw_text_fit(ImageDraw.Draw(canvas), adjusted, cta_text)
        return adjusted.bounds, _contrast_ratio(fill, cta_text), adjusted.text


def resolve_final_dimensions(
    asset_type: str,
    width: int | None,
    height: int | None,
    aspect_ratio: str | None,
) -> tuple[int, int]:
    if asset_type == "creative_brief":
        raise CreativeCompositionError("Creative briefs are not publishable image assets")
    if (width is None) != (height is None):
        raise CreativeCompositionError("Width and height must be supplied together")
    if width is not None and height is not None:
        target = (width, height)
    else:
        target = DEFAULT_CREATIVE_DIMENSIONS.get(
            asset_type,
            DEFAULT_CREATIVE_DIMENSIONS["other"],
        )
        if aspect_ratio:
            ratio = _parse_ratio(aspect_ratio)
            base_area = target[0] * target[1]
            candidate_width = round((base_area * ratio) ** 0.5)
            candidate_height = round(candidate_width / ratio)
            target = (candidate_width, candidate_height)

    target_width, target_height = target
    if (
        target_width < 320
        or target_height < 320
        or target_width > MAX_RENDER_EDGE
        or target_height > MAX_RENDER_EDGE
        or target_width * target_height > MAX_RENDER_PIXELS
    ):
        raise CreativeCompositionError("Final creative dimensions are unsupported")
    return target


def _bounded_text(value: str, label: str, maximum: int) -> None:
    if not value.strip() or len(value) > maximum:
        raise CreativeCompositionError(f"{label} is invalid")


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def _verify_exact_copy(label: str, expected: str, actual: str) -> None:
    if _normalize_whitespace(actual) != _normalize_whitespace(expected):
        raise CreativeCompositionError(f"Exact {label} integrity check failed")


def _glyph_signature(
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    character: str,
) -> tuple[tuple[int, int], bytes]:
    try:
        mask = font.getmask(character)
    except (OSError, TypeError, ValueError, UnicodeError):
        return ((-1, -1), b"")
    return mask.size, bytes(mask)


def _is_ignorable_format_character(character: str) -> bool:
    return category(character) in {"Cc", "Cf"}


def _load_source_visual(content: bytes) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as probe:
                image_format = (probe.format or "").upper()
                if image_format not in _SUPPORTED_SOURCE_FORMATS:
                    raise CreativeCompositionError("Raw visual format is unsupported")
                _validate_dimensions(*probe.size)
                if getattr(probe, "is_animated", False):
                    raise CreativeCompositionError("Animated raw visuals are unsupported")
                probe.verify()
            with Image.open(BytesIO(content)) as decoded:
                _validate_dimensions(*decoded.size)
                decoded.load()
                normalized = ImageOps.exif_transpose(decoded).convert("RGB")
    except CreativeCompositionError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        raise CreativeCompositionError("Raw visual is invalid") from None
    normalized.info.clear()
    return normalized


def _load_logo(content: bytes | None) -> tuple[Image.Image | None, float | None]:
    if content is None:
        return None, None
    if not content or len(content) > MAX_LOGO_BYTES:
        raise CreativeCompositionError("Logo size is invalid")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as probe:
                image_format = (probe.format or "").upper()
                if image_format not in _SUPPORTED_SOURCE_FORMATS:
                    raise CreativeCompositionError("Logo format is unsupported")
                _validate_dimensions(*probe.size)
                if getattr(probe, "is_animated", False):
                    raise CreativeCompositionError("Animated logos are unsupported")
                probe.verify()
            with Image.open(BytesIO(content)) as decoded:
                _validate_dimensions(*decoded.size)
                decoded.load()
                logo = ImageOps.exif_transpose(decoded).convert("RGBA")
    except CreativeCompositionError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        raise CreativeCompositionError("Logo is invalid") from None
    logo.info.clear()
    if logo.getchannel("A").getbbox() is None:
        raise CreativeCompositionError("Logo contains no visible pixels")
    return logo, logo.width / logo.height


def _draw_logo_identity(
    canvas: Image.Image,
    logo: Image.Image,
    box: Box,
) -> tuple[Box, LogoTreatment]:
    available_width, available_height = _box_size(box)
    rendered = logo.copy()
    rendered.thumbnail(
        (available_width, available_height),
        Image.Resampling.LANCZOS,
    )
    left = box[0]
    top = box[1] + max(0, (available_height - rendered.height) // 2)
    bounds = (left, top, left + rendered.width, top + rendered.height)

    if _sampled_logo_contrast(canvas, rendered, bounds) >= 3.0:
        canvas.paste(rendered, (left, top), rendered)
        return bounds, "none"

    padding = max(6, round(min(available_width, available_height) * 0.12))
    inner_width = available_width - padding * 2
    inner_height = available_height - padding * 2
    if inner_width <= 0 or inner_height <= 0:
        raise CreativeCompositionError("Logo cannot fit inside its safe identity zone")
    rendered = logo.copy()
    rendered.thumbnail((inner_width, inner_height), Image.Resampling.LANCZOS)
    left = box[0] + padding
    top = box[1] + max(padding, (available_height - rendered.height) // 2)
    bounds = (left, top, left + rendered.width, top + rendered.height)
    plate_bounds = (
        box[0],
        max(box[1], top - padding),
        min(box[2], bounds[2] + padding),
        min(box[3], bounds[3] + padding),
    )
    representative = _representative_logo_color(rendered)
    light_plate = (248, 248, 246)
    dark_plate = (20, 22, 26)
    plate_color = (
        light_plate
        if _contrast_ratio(representative, light_plate)
        >= _contrast_ratio(representative, dark_plate)
        else dark_plate
    )
    radius = max(6, round((plate_bounds[3] - plate_bounds[1]) * 0.18))
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle(
        plate_bounds,
        radius=radius,
        fill=(*plate_color, 238),
    )
    if _sampled_logo_contrast(canvas, rendered, bounds) < 3.0:
        ImageDraw.Draw(canvas).rounded_rectangle(
            plate_bounds,
            radius=radius,
            fill=plate_color,
        )
    canvas.paste(rendered, (left, top), rendered)
    return bounds, "contrast_plate"


def _representative_logo_color(logo: Image.Image) -> RGB:
    sampled = logo.resize(
        (min(16, logo.width), min(16, logo.height)),
        Image.Resampling.BOX,
    )
    pixels = list(sampled.get_flattened_data())
    maximum_alpha = max((pixel[3] for pixel in pixels), default=0)
    alpha_floor = max(64, round(maximum_alpha * 0.7))
    visible = [pixel for pixel in pixels if pixel[3] >= alpha_floor]
    if not visible:
        raise CreativeCompositionError("Logo contains no visible pixels")
    return tuple(
        sorted(pixel[channel] for pixel in visible)[len(visible) // 2]
        for channel in range(3)
    )


def _sampled_logo_contrast(
    canvas: Image.Image,
    logo: Image.Image,
    bounds: Box,
) -> float:
    sample_size = (min(16, logo.width), min(16, logo.height))
    sampled_logo = logo.resize(sample_size, Image.Resampling.BOX)
    sampled_background = canvas.crop(bounds).resize(
        sample_size,
        Image.Resampling.BOX,
    )
    foreground_pixels = list(sampled_logo.get_flattened_data())
    background_pixels = list(sampled_background.get_flattened_data())
    maximum_alpha = max((pixel[3] for pixel in foreground_pixels), default=0)
    alpha_floor = max(64, round(maximum_alpha * 0.7))
    ratios: list[float] = []
    for foreground, background in zip(
        foreground_pixels,
        background_pixels,
        strict=True,
    ):
        alpha = foreground[3] / 255
        if foreground[3] < alpha_floor:
            continue
        composed = tuple(
            round(foreground[channel] * alpha + background[channel] * (1 - alpha))
            for channel in range(3)
        )
        ratios.append(_contrast_ratio(composed, tuple(background[:3])))
    if not ratios:
        return 0.0
    ratios.sort()
    return ratios[min(len(ratios) - 1, len(ratios) // 20)]


def _validate_dimensions(width: int, height: int) -> None:
    if (
        width <= 0
        or height <= 0
        or width > MAX_RENDER_EDGE
        or height > MAX_RENDER_EDGE
        or width * height > MAX_RENDER_PIXELS
    ):
        raise CreativeCompositionError("Image dimensions exceed safe limits")


def _palette(value: CreativeCompositionInput) -> tuple[RGB, RGB, RGB]:
    neutral = ((30, 31, 34), (244, 242, 237), (142, 126, 112))
    return (
        _parse_color(value.primary_color) or neutral[0],
        _parse_color(value.secondary_color) or neutral[1],
        _parse_color(value.accent_color) or neutral[2],
    )


def _parse_color(value: str | None) -> RGB | None:
    if value is None:
        return None
    try:
        parsed = ImageColor.getrgb(value)
    except ValueError:
        return None
    return parsed if len(parsed) == 3 else parsed[:3]


def _select_layout(
    value: CreativeCompositionInput,
    source: Image.Image,
) -> LayoutFamily:
    ratio = value.target_width / value.target_height
    direction = f"{value.composition_direction} {value.negative_space}".casefold()
    if value.asset_type == "story_reel" or ratio <= 0.7:
        return "vertical_story"
    if ratio >= 1.45 or any(word in direction for word in ("split", "side panel")):
        return "editorial_split"
    if len(value.supporting_copy) > 260 or len(value.headline) > 95:
        return "framed_campaign"
    if len(value.headline) <= 48 and len(value.supporting_copy) <= 180:
        return "minimal_hero"
    luminance = ImageStat.Stat(source.resize((1, 1)).convert("L")).mean[0]
    if 70 <= luminance <= 205:
        return "cinematic_overlay"
    return "framed_campaign"


def _layout_candidates(
    preferred: LayoutFamily,
    value: CreativeCompositionInput,
) -> tuple[LayoutFamily, ...]:
    fallbacks: list[LayoutFamily] = [
        preferred,
        "framed_campaign",
        "editorial_split",
        "cinematic_overlay",
    ]
    if value.target_height > value.target_width:
        fallbacks.insert(1, "vertical_story")
    return tuple(dict.fromkeys(fallbacks))


def _safe_margins(value: CreativeCompositionInput) -> tuple[int, int, int]:
    margin = max(24, round(min(value.target_width, value.target_height) * 0.055))
    if value.asset_type == "story_reel" or value.target_height / value.target_width >= 1.65:
        return margin, max(margin, round(value.target_height * 0.10)), max(
            margin, round(value.target_height * 0.13)
        )
    return margin, margin, margin


def _resolve_text_side(
    negative_space: str,
    composition_direction: str,
) -> TextSide | None:
    """Resolve a bounded semantic direction; models never provide coordinates."""
    for source in (negative_space, composition_direction):
        normalized = " ".join(source.casefold().replace("-", " ").split())
        right_markers = (
            "negative space on the right",
            "negative space right",
            "space on the right",
            "space right",
            "open area on the right",
            "open space right",
            "copy on the right",
            "typography on the right",
            "subject on the left",
            "subject left",
            "hero on the left",
            "hero left",
        )
        left_markers = (
            "negative space on the left",
            "negative space left",
            "space on the left",
            "space left",
            "open area on the left",
            "open space left",
            "copy on the left",
            "typography on the left",
            "subject on the right",
            "subject right",
            "hero on the right",
            "hero right",
        )
        if any(marker in normalized for marker in right_markers):
            return "right"
        if any(marker in normalized for marker in left_markers):
            return "left"
    return None


def _copy_horizontal_bounds(
    width: int,
    margin: int,
    fraction: float,
    text_side: TextSide,
) -> tuple[int, int]:
    copy_width = round(width * fraction)
    if text_side == "right":
        return width - margin - copy_width, width - margin
    return margin, margin + copy_width


def _layout_plan(
    family: LayoutFamily,
    width: int,
    height: int,
    margin: int,
    safe_top: int,
    safe_bottom: int,
    palette: tuple[RGB, RGB, RGB],
    has_cta: bool,
    text_side: TextSide,
) -> _LayoutPlan:
    safe_left, safe_right = margin, width - margin
    safe_end = height - safe_bottom

    if family == "editorial_split":
        panel_width = max(round(width * 0.40), 360)
        panel_width = min(panel_width, width - margin * 3)
        panel_on_left = text_side == "left"
        if panel_on_left:
            panel = (0, 0, panel_width, height)
            image_box = (panel_width, 0, width, height)
            left, right = margin, panel_width - margin
        else:
            panel = (width - panel_width, 0, width, height)
            image_box = (0, 0, width - panel_width, height)
            left, right = panel[0] + margin, width - margin
        available = safe_end - safe_top
        return _LayoutPlan(
            family=family,
            image_box=image_box,
            brand_box=(left, safe_top, right, safe_top + round(available * 0.12)),
            headline_box=(left, safe_top + round(available * 0.20), right, safe_top + round(available * 0.50)),
            supporting_box=(left, safe_top + round(available * 0.53), right, safe_top + round(available * 0.77)),
            cta_box=((left, safe_top + round(available * 0.81), right, safe_end) if has_cta else None),
            text_surface=panel,
            overlay=False,
            panel_color=palette[1],
            text_color=_best_text_color(palette[1]),
        )

    if family == "framed_campaign":
        image_bottom = round(height * 0.55)
        image_box = (margin, safe_top, width - margin, image_bottom)
        text_top = image_bottom + margin
        available = max(1, safe_end - text_top)
        return _LayoutPlan(
            family=family,
            image_box=image_box,
            brand_box=(safe_left, text_top, safe_right, text_top + round(available * 0.11)),
            headline_box=(safe_left, text_top + round(available * 0.15), safe_right, text_top + round(available * 0.43)),
            supporting_box=(safe_left, text_top + round(available * 0.47), safe_right, text_top + round(available * 0.73)),
            cta_box=((safe_left, text_top + round(available * 0.77), safe_right, safe_end) if has_cta else None),
            text_surface=(0, image_bottom, width, height),
            overlay=False,
            panel_color=palette[1],
            text_color=_best_text_color(palette[1]),
        )

    if family == "vertical_story":
        text_top = round(height * 0.48)
        available = safe_end - text_top
        left, right = _copy_horizontal_bounds(width, margin, 0.78, text_side)
        surface = (
            0 if text_side == "left" else round(width * 0.16),
            0,
            round(width * 0.84) if text_side == "left" else width,
            height,
        )
        return _LayoutPlan(
            family=family,
            image_box=(0, 0, width, height),
            brand_box=(left, safe_top, right, safe_top + round(height * 0.07)),
            headline_box=(left, text_top, right, text_top + round(available * 0.34)),
            supporting_box=(left, text_top + round(available * 0.38), right, text_top + round(available * 0.68)),
            cta_box=((left, text_top + round(available * 0.74), right, safe_end) if has_cta else None),
            text_surface=surface,
            overlay=True,
            panel_color=None,
            text_color=None,
        )

    if family == "minimal_hero":
        left, right = _copy_horizontal_bounds(width, margin, 0.54, text_side)
        available = safe_end - safe_top
        surface = (
            0 if text_side == "left" else round(width * 0.32),
            0,
            round(width * 0.68) if text_side == "left" else width,
            height,
        )
        return _LayoutPlan(
            family=family,
            image_box=(0, 0, width, height),
            brand_box=(left, safe_top, right, safe_top + round(available * 0.10)),
            headline_box=(left, safe_top + round(available * 0.18), right, safe_top + round(available * 0.45)),
            supporting_box=(left, safe_top + round(available * 0.49), right, safe_top + round(available * 0.68)),
            cta_box=((left, safe_top + round(available * 0.75), right, safe_end) if has_cta else None),
            text_surface=surface,
            overlay=True,
            panel_color=None,
            text_color=None,
        )

    available = safe_end - safe_top
    left, right = _copy_horizontal_bounds(width, margin, 0.58, text_side)
    surface = (
        0 if text_side == "left" else round(width * 0.24),
        0,
        round(width * 0.76) if text_side == "left" else width,
        height,
    )
    return _LayoutPlan(
        family="cinematic_overlay",
        image_box=(0, 0, width, height),
        brand_box=(left, safe_top, right, safe_top + round(available * 0.10)),
        headline_box=(left, safe_top + round(available * 0.43), right, safe_top + round(available * 0.66)),
        supporting_box=(left, safe_top + round(available * 0.69), right, safe_top + round(available * 0.84)),
        cta_box=((left, safe_top + round(available * 0.87), right, safe_end) if has_cta else None),
        text_surface=surface,
        overlay=True,
        panel_color=None,
        text_color=None,
    )


def _draw_layout_frame(
    draw: ImageDraw.ImageDraw,
    plan: _LayoutPlan,
    palette: tuple[RGB, RGB, RGB],
    width: int,
    height: int,
    margin: int,
) -> None:
    if plan.panel_color is not None:
        draw.rectangle(plan.text_surface, fill=plan.panel_color)
    if plan.family == "framed_campaign":
        draw.rectangle((0, 0, width, height), outline=palette[0], width=max(4, margin // 6))
        draw.rectangle(plan.image_box, outline=palette[2], width=max(3, margin // 10))
    elif plan.family == "editorial_split":
        edge = plan.text_surface[2] if plan.text_surface[0] == 0 else plan.text_surface[0]
        draw.rectangle((edge - 3, 0, edge + 3, height), fill=palette[2])


def _prepare_overlay_contrast(
    canvas: Image.Image,
    surface: Box,
    family: LayoutFamily,
    text_side: TextSide,
    text_zones: tuple[Box, ...],
) -> tuple[RGB, str]:
    base = canvas.copy()
    light = (255, 255, 255)
    dark = (18, 20, 24)
    light_score = _sampled_minimum_contrast(base, text_zones, light)
    dark_score = _sampled_minimum_contrast(base, text_zones, dark)
    text_color = light if light_score >= dark_score else dark
    overlay_color = (0, 0, 0) if text_color == light else (255, 255, 255)

    # These masks are generated and scaled entirely by Pillow. Each pass is
    # bounded and avoids Python work proportional to final image pixels.
    for minimum_alpha, maximum_alpha in ((72, 188), (112, 224)):
        canvas.paste(base)
        _apply_native_gradient(
            canvas,
            surface,
            overlay_color,
            family,
            text_side,
            minimum_alpha,
            maximum_alpha,
        )
        if _sampled_minimum_contrast(canvas, text_zones, text_color) >= 4.5:
            return text_color, "controlled_gradient"

    # A restrained rounded surface is the final deterministic fallback for
    # visually mixed regions that remain unsafe after the stronger gradient.
    crop = canvas.crop(surface).convert("RGBA")
    plate = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    ImageDraw.Draw(plate).rounded_rectangle(
        (0, 0, max(0, crop.width - 1), max(0, crop.height - 1)),
        radius=max(12, round(min(canvas.size) * 0.018)),
        fill=(*overlay_color, 232),
    )
    canvas.paste(
        Image.alpha_composite(crop, plate).convert("RGB"),
        surface[:2],
    )
    if _sampled_minimum_contrast(canvas, text_zones, text_color) < 4.5:
        raise CreativeCompositionError(
            "Typography contrast is below quality threshold"
        )
    return text_color, "gradient_and_scrim"


def _apply_native_gradient(
    canvas: Image.Image,
    surface: Box,
    overlay_color: RGB,
    family: LayoutFamily,
    text_side: TextSide,
    minimum_alpha: int,
    maximum_alpha: int,
) -> None:
    width, height = _box_size(surface)
    horizontal = Image.linear_gradient("L").rotate(
        270 if text_side == "left" else 90
    )
    horizontal = horizontal.resize((width, height), Image.Resampling.BILINEAR)
    horizontal = horizontal.point(
        lambda value: minimum_alpha
        + round((maximum_alpha - minimum_alpha) * value / 255)
    )
    mask = horizontal
    if family in {"cinematic_overlay", "vertical_story"}:
        vertical = Image.linear_gradient("L").resize(
            (width, height),
            Image.Resampling.BILINEAR,
        )
        vertical = vertical.point(
            lambda value: round(minimum_alpha * 0.55)
            + round(
                (maximum_alpha - round(minimum_alpha * 0.55))
                * value
                / 255
            )
        )
        mask = ImageChops.lighter(horizontal, vertical)

    background = canvas.crop(surface).convert("RGB")
    overlay = Image.new("RGB", background.size, overlay_color)
    canvas.paste(Image.composite(overlay, background, mask), surface[:2])


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if not current or draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def _draw_text_fit(
    draw: ImageDraw.ImageDraw,
    fit: _TextFit,
    color: RGB,
    *,
    stroke_width: int = 0,
) -> None:
    draw.multiline_text(
        fit.origin,
        fit.text,
        font=fit.font,
        fill=color,
        spacing=fit.spacing,
        stroke_width=stroke_width,
        stroke_fill=color,
    )


def _validate_element_bounds(
    text_bounds: dict[str, Box],
    logo_bounds: Box | None,
    safe_box: Box,
) -> None:
    for bounds in [*text_bounds.values(), *([logo_bounds] if logo_bounds else [])]:
        if not _inside(bounds, safe_box):
            raise CreativeCompositionError("A required element left the safe area")


def _inside(inner: Box, outer: Box) -> bool:
    return (
        inner[0] >= outer[0]
        and inner[1] >= outer[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
        and inner[2] > inner[0]
        and inner[3] > inner[1]
    )


def _sampled_minimum_contrast(
    canvas: Image.Image,
    bounds: tuple[Box, ...],
    text_color: RGB,
) -> float:
    ratios: list[float] = []
    for box in bounds:
        clipped = (
            max(0, box[0]),
            max(0, box[1]),
            min(canvas.width, box[2]),
            min(canvas.height, box[3]),
        )
        width, height = _box_size(clipped)
        if width <= 0 or height <= 0:
            return 0.0
        sampled = canvas.crop(clipped).resize(
            (min(16, width), min(16, height)),
            Image.Resampling.BOX,
        )
        ratios.extend(
            _contrast_ratio(tuple(pixel[:3]), text_color)
            for pixel in sampled.get_flattened_data()
        )
    if not ratios:
        return 21.0
    ratios.sort()
    # The fifth percentile is a conservative lower bound without letting one
    # resampling-edge outlier dictate the entire design.
    return ratios[min(len(ratios) - 1, len(ratios) // 20)]


def _best_text_color(background: RGB) -> RGB:
    white = (255, 255, 255)
    dark = (18, 20, 24)
    return white if _contrast_ratio(background, white) >= _contrast_ratio(background, dark) else dark


def _contrast_ratio(first: RGB, second: RGB) -> float:
    high, low = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _relative_luminance(color: RGB) -> float:
    values = []
    for value in color:
        channel = value / 255
        values.append(channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]


def _focal_center(negative_space: str, composition_direction: str) -> tuple[float, float]:
    value = f"{negative_space} {composition_direction}".casefold()
    horizontal = 0.5
    vertical = 0.5
    text_side = _resolve_text_side(negative_space, composition_direction)
    if text_side == "left":
        horizontal = 0.68
    elif text_side == "right":
        horizontal = 0.32
    if "space above" in value or "negative space top" in value:
        vertical = 0.68
    elif "space below" in value or "negative space bottom" in value:
        vertical = 0.32
    return horizontal, vertical


def _encode_png(canvas: Image.Image) -> bytes:
    if canvas.getbbox() is None:
        raise CreativeCompositionError("Final creative is empty")
    output = BytesIO()
    canvas.convert("RGB").save(
        output,
        format="PNG",
        optimize=True,
        compress_level=9,
    )
    content = output.getvalue()
    if not content or len(content) > MAX_FINAL_PNG_BYTES:
        raise CreativeCompositionError("Final creative exceeds the output limit")
    return content


def _validate_final_png(content: bytes, width: int, height: int) -> None:
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != "PNG" or image.size != (width, height):
                raise CreativeCompositionError("Final creative validation failed")
            image.verify()
        with Image.open(BytesIO(content)) as decoded:
            decoded.load()
            if decoded.getbbox() is None:
                raise CreativeCompositionError("Final creative is empty")
    except CreativeCompositionError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise CreativeCompositionError("Final creative validation failed") from None


def _parse_ratio(value: str) -> float:
    try:
        left, right = value.strip().split(":", 1)
        ratio = float(left) / float(right)
    except (ValueError, ZeroDivisionError):
        raise CreativeCompositionError("Aspect ratio is invalid") from None
    if ratio < 1 / 3 or ratio > 3:
        raise CreativeCompositionError("Aspect ratio is unsupported")
    return ratio


def _box_size(box: Box) -> tuple[int, int]:
    return box[2] - box[0], box[3] - box[1]
