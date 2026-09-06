from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID

from app.models.business import Business
from app.models.business_branding import BusinessBranding


BrandPaletteSource = Literal[
    "tenant",
    "partial_tenant",
    "neutral_fallback",
]

BrandLogoMode = Literal[
    "tenant_logo",
    "business_name_fallback",
]


_HEX_COLOR: Final[re.Pattern[str]] = re.compile(r"^#[0-9A-Fa-f]{6}$")

_WHITE: Final[str] = "#FFFFFF"
_BLACK: Final[str] = "#111111"

# Neutral fallback deliberately does NOT use 9D Brain product colors.
# Tenant creative must never silently inherit platform identity.
_NEUTRAL_PRIMARY: Final[str] = "#171717"
_NEUTRAL_SECONDARY: Final[str] = "#F5F5F4"
_NEUTRAL_ACCENT: Final[str] = "#525252"


@dataclass(frozen=True, slots=True)
class CreativeBrandIdentity:
    """
    Safe, deterministic visual brand identity for one tenant.

    Raw storage identifiers and logo URLs are intentionally excluded.
    The sanitized logo bytes remain local to the deterministic compositor.
    """

    business_id: UUID
    business_name: str

    primary_color: str
    secondary_color: str
    accent_color: str

    canvas_color: str
    canvas_text_color: str

    cta_fill_color: str
    cta_text_color: str

    muted_surface_color: str
    border_color: str

    palette_source: BrandPaletteSource
    logo_mode: BrandLogoMode

    @property
    def has_tenant_palette(self) -> bool:
        return self.palette_source in {"tenant", "partial_tenant"}

    @property
    def has_tenant_logo(self) -> bool:
        return self.logo_mode == "tenant_logo"

    def provider_palette_instruction(self) -> str:
        """
        Provider-safe palette direction.

        Exact logo data, storage keys, URLs, credentials, and internal identifiers
        are never included here.
        """
        return (
            "BRAND COLOR DIRECTION FOR THE RAW VISUAL:\n"
            f"- Primary brand color: {self.primary_color}\n"
            f"- Secondary brand color: {self.secondary_color}\n"
            f"- Accent brand color: {self.accent_color}\n"
            "- Use this palette for lighting, materials, environmental accents, "
            "wardrobe accents, surfaces, or scene color harmony where appropriate.\n"
            "- Do not force every color into the image.\n"
            "- Do not render a logo, brand mark, wordmark, typography, letters, "
            "CTA, UI chrome, or invented branded packaging.\n"
            "- The application will apply the real tenant logo and exact marketing "
            "copy in the deterministic composition stage.\n"
            "- The raw visual must still communicate the business-specific campaign "
            "idea even before the logo and typography are added."
        )

    def deterministic_composition_instruction(self) -> str:
        return (
            "DETERMINISTIC BRAND COMPOSITION:\n"
            f"- Business name: {self.business_name}\n"
            f"- Primary: {self.primary_color}\n"
            f"- Secondary: {self.secondary_color}\n"
            f"- Accent: {self.accent_color}\n"
            f"- Preferred canvas: {self.canvas_color}\n"
            f"- Preferred canvas text: {self.canvas_text_color}\n"
            f"- Preferred CTA fill: {self.cta_fill_color}\n"
            f"- Preferred CTA text: {self.cta_text_color}\n"
            f"- Logo mode: {self.logo_mode}\n"
            "- Preserve exact tenant identity. Do not substitute platform branding."
        )


def build_creative_brand_identity(
    *,
    business: Business,
    branding: BusinessBranding | None,
    sanitized_logo_content: bytes | None,
) -> CreativeBrandIdentity:
    """
    Convert authoritative tenant branding into deterministic creative roles.

    `sanitized_logo_content` must come from the existing tenant-safe logo loader.
    This service never reads object storage itself.
    """

    if branding is not None and branding.business_id != business.id:
        raise ValueError("branding does not belong to the requested business")

    primary_source = _normalize_hex(
        branding.primary_color if branding is not None else None
    )
    secondary_source = _normalize_hex(
        branding.secondary_color if branding is not None else None
    )
    accent_source = _normalize_hex(
        branding.accent_color if branding is not None else None
    )

    supplied_count = sum(
        value is not None
        for value in (
            primary_source,
            secondary_source,
            accent_source,
        )
    )

    if supplied_count == 3:
        palette_source: BrandPaletteSource = "tenant"
    elif supplied_count > 0:
        palette_source = "partial_tenant"
    else:
        palette_source = "neutral_fallback"

    if palette_source == "neutral_fallback":
        # A tenant with no supplied brand palette receives an intentionally
        # neutral system. Never inherit 9D Brain/platform colors.
        primary = _NEUTRAL_PRIMARY
        secondary = _NEUTRAL_SECONDARY
        accent = _NEUTRAL_ACCENT
    else:
        # At least one tenant-owned color exists. Keep the tenant identity as
        # the source of truth and derive missing roles from those supplied
        # colors rather than introducing unrelated brand colors.
        primary = (
            primary_source
            or accent_source
            or secondary_source
        )

        if primary is None:
            raise ValueError("tenant palette resolution failed")

        secondary = (
            secondary_source
            or _supporting_surface(primary)
        )

        accent = (
            accent_source
            or primary_source
            or secondary_source
            or primary
        )

    canvas = _preferred_canvas(
        primary=primary,
        secondary=secondary,
    )

    canvas_text = _best_foreground(canvas)

    cta_fill = _preferred_cta_fill(
        primary=primary,
        accent=accent,
        canvas=canvas,
    )

    cta_text = _best_foreground(cta_fill)

    muted_surface = _mix_hex(
        canvas,
        _WHITE if _relative_luminance(canvas) < 0.5 else _BLACK,
        0.06,
    )

    border = _mix_hex(
        canvas_text,
        canvas,
        0.78,
    )

    logo_mode: BrandLogoMode = (
        "tenant_logo"
        if sanitized_logo_content
        else "business_name_fallback"
    )

    return CreativeBrandIdentity(
        business_id=business.id,
        business_name=business.name.strip(),
        primary_color=primary,
        secondary_color=secondary,
        accent_color=accent,
        canvas_color=canvas,
        canvas_text_color=canvas_text,
        cta_fill_color=cta_fill,
        cta_text_color=cta_text,
        muted_surface_color=muted_surface,
        border_color=border,
        palette_source=palette_source,
        logo_mode=logo_mode,
    )


def brand_identity_meets_composition_floor(
    identity: CreativeBrandIdentity,
) -> bool:
    """
    Deterministic sanity floor for final branded composition.

    A brand identity must always produce readable canvas and CTA pairings.
    """

    return (
        _contrast_ratio(
            identity.canvas_color,
            identity.canvas_text_color,
        )
        >= 4.5
        and _contrast_ratio(
            identity.cta_fill_color,
            identity.cta_text_color,
        )
        >= 4.5
    )


def _normalize_hex(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip().upper()

    if not _HEX_COLOR.fullmatch(normalized):
        return None

    return normalized


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    normalized = value.lstrip("#")

    return (
        int(normalized[0:2], 16),
        int(normalized[2:4], 16),
        int(normalized[4:6], 16),
    )


def _rgb_to_hex(red: int, green: int, blue: int) -> str:
    return f"#{red:02X}{green:02X}{blue:02X}"


def _mix_hex(
    first: str,
    second: str,
    second_weight: float,
) -> str:
    weight = max(0.0, min(1.0, second_weight))

    first_rgb = _hex_to_rgb(first)
    second_rgb = _hex_to_rgb(second)

    mixed = tuple(
        round(first_value * (1.0 - weight) + second_value * weight)
        for first_value, second_value in zip(
            first_rgb,
            second_rgb,
            strict=True,
        )
    )

    return _rgb_to_hex(*mixed)


def _linear_channel(value: int) -> float:
    normalized = value / 255.0

    if normalized <= 0.04045:
        return normalized / 12.92

    return ((normalized + 0.055) / 1.055) ** 2.4


def _relative_luminance(value: str) -> float:
    red, green, blue = _hex_to_rgb(value)

    return (
        0.2126 * _linear_channel(red)
        + 0.7152 * _linear_channel(green)
        + 0.0722 * _linear_channel(blue)
    )


def _contrast_ratio(first: str, second: str) -> float:
    first_luminance = _relative_luminance(first)
    second_luminance = _relative_luminance(second)

    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)

    return (lighter + 0.05) / (darker + 0.05)


def _best_foreground(background: str) -> str:
    white_contrast = _contrast_ratio(background, _WHITE)
    black_contrast = _contrast_ratio(background, _BLACK)

    return _WHITE if white_contrast >= black_contrast else _BLACK


def _supporting_surface(primary: str) -> str:
    """
    Create a restrained supporting surface from a lone tenant color.

    This retains family resemblance without pretending the derived tone was
    explicitly supplied by the business.
    """
    if _relative_luminance(primary) < 0.42:
        return _mix_hex(primary, _WHITE, 0.91)

    return _mix_hex(primary, _WHITE, 0.84)


def _preferred_canvas(
    *,
    primary: str,
    secondary: str,
) -> str:
    """
    Prefer the tenant's secondary color as the editorial canvas when usable.

    Extremely dark or saturated-looking secondaries are softened so exact copy
    remains readable while the source identity still influences the design.
    """
    luminance = _relative_luminance(secondary)

    if 0.18 <= luminance <= 0.92:
        return secondary

    if luminance < 0.18:
        return _mix_hex(secondary, _WHITE, 0.88)

    return _mix_hex(primary, _WHITE, 0.94)


def _preferred_cta_fill(
    *,
    primary: str,
    accent: str,
    canvas: str,
) -> str:
    """
    Choose the tenant accent when it visually separates from the canvas.

    Fall back to primary when the accent is too close to the background.
    """
    if _contrast_ratio(accent, canvas) >= 2.1:
        return accent

    if _contrast_ratio(primary, canvas) >= 2.1:
        return primary

    return (
        _mix_hex(primary, _BLACK, 0.34)
        if _relative_luminance(canvas) > 0.5
        else _mix_hex(primary, _WHITE, 0.34)
    )
