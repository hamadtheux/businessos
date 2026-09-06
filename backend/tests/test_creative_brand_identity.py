from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.services.creative_brand_identity import (
    brand_identity_meets_composition_floor,
    build_creative_brand_identity,
)
from app.services.creative_compositor import (
    CreativeCompositionInput,
    _palette,
)


def _business(*, business_id=None, name: str = "Acme Commerce"):
    return SimpleNamespace(
        id=business_id or uuid4(),
        name=name,
    )


def _branding(
    *,
    business_id,
    primary: str | None = None,
    secondary: str | None = None,
    accent: str | None = None,
):
    return SimpleNamespace(
        business_id=business_id,
        primary_color=primary,
        secondary_color=secondary,
        accent_color=accent,
    )


def _raw_png() -> bytes:
    image = Image.new("RGB", (64, 64), (220, 220, 220))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_exact_tenant_palette_is_preserved() -> None:
    business = _business()

    identity = build_creative_brand_identity(
        business=business,  # type: ignore[arg-type]
        branding=_branding(
            business_id=business.id,
            primary="#123456",
            secondary="#F4F1EA",
            accent="#E86A33",
        ),  # type: ignore[arg-type]
        sanitized_logo_content=b"sanitized-logo-present",
    )

    assert identity.primary_color == "#123456"
    assert identity.secondary_color == "#F4F1EA"
    assert identity.accent_color == "#E86A33"

    assert identity.palette_source == "tenant"
    assert identity.logo_mode == "tenant_logo"
    assert identity.has_tenant_palette is True
    assert identity.has_tenant_logo is True

    assert brand_identity_meets_composition_floor(identity) is True


def test_missing_logo_uses_business_name_fallback_not_platform_identity() -> None:
    business = _business(name="Northstar Dental")

    identity = build_creative_brand_identity(
        business=business,  # type: ignore[arg-type]
        branding=_branding(
            business_id=business.id,
            primary="#183A61",
            secondary="#EEF4F8",
            accent="#2C7DA0",
        ),  # type: ignore[arg-type]
        sanitized_logo_content=None,
    )

    assert identity.business_name == "Northstar Dental"
    assert identity.logo_mode == "business_name_fallback"
    assert identity.has_tenant_logo is False

    # No product/platform identity is invented.
    assert "9D Brain" not in identity.business_name


def test_partial_tenant_palette_derives_supporting_roles_without_losing_source_color() -> None:
    business = _business()

    identity = build_creative_brand_identity(
        business=business,  # type: ignore[arg-type]
        branding=_branding(
            business_id=business.id,
            primary="#6D28D9",
        ),  # type: ignore[arg-type]
        sanitized_logo_content=None,
    )

    assert identity.primary_color == "#6D28D9"
    assert identity.accent_color == "#6D28D9"
    assert identity.palette_source == "partial_tenant"

    assert identity.secondary_color.startswith("#")
    assert identity.canvas_color.startswith("#")
    assert identity.canvas_text_color.startswith("#")
    assert identity.cta_fill_color.startswith("#")
    assert identity.cta_text_color.startswith("#")
    assert identity.muted_surface_color.startswith("#")
    assert identity.border_color.startswith("#")

    assert brand_identity_meets_composition_floor(identity) is True


def test_unbranded_business_receives_neutral_not_9d_brain_palette() -> None:
    business = _business()

    identity = build_creative_brand_identity(
        business=business,  # type: ignore[arg-type]
        branding=None,
        sanitized_logo_content=None,
    )

    assert identity.palette_source == "neutral_fallback"

    assert identity.primary_color == "#171717"
    assert identity.secondary_color == "#F5F5F4"
    assert identity.accent_color == "#525252"

    assert brand_identity_meets_composition_floor(identity) is True


def test_branding_from_another_tenant_is_rejected() -> None:
    business = _business()

    with pytest.raises(
        ValueError,
        match="branding does not belong to the requested business",
    ):
        build_creative_brand_identity(
            business=business,  # type: ignore[arg-type]
            branding=_branding(
                business_id=uuid4(),
                primary="#123456",
                secondary="#FFFFFF",
                accent="#FF5500",
            ),  # type: ignore[arg-type]
            sanitized_logo_content=None,
        )


def test_provider_palette_instruction_contains_safe_palette_but_no_storage_data() -> None:
    business = _business()

    identity = build_creative_brand_identity(
        business=business,  # type: ignore[arg-type]
        branding=_branding(
            business_id=business.id,
            primary="#123456",
            secondary="#F4F1EA",
            accent="#E86A33",
        ),  # type: ignore[arg-type]
        sanitized_logo_content=b"PRIVATE-LOGO-BYTES",
    )

    instruction = identity.provider_palette_instruction()

    assert "#123456" in instruction
    assert "#F4F1EA" in instruction
    assert "#E86A33" in instruction

    normalized = instruction.casefold()

    assert "private-logo-bytes" not in normalized
    assert "businesses/" not in normalized
    assert "logo_storage_key" not in normalized
    assert "logo_url" not in normalized
    assert "http://" not in normalized
    assert "https://" not in normalized

    # Provider is explicitly told not to hallucinate identity.
    assert "do not render a logo" in normalized
    assert "application will apply the real tenant logo" in normalized


def test_compositor_uses_named_derived_brand_roles() -> None:
    value = CreativeCompositionInput(
        raw_visual=_raw_png(),
        target_width=1080,
        target_height=1080,
        asset_type="social_square",
        headline="Grow with less manual work",
        supporting_copy="AI coordinates the repetitive work across your business.",
        cta="Learn more",
        business_name="Acme Commerce",
        primary_color="#102A43",
        secondary_color="#EAF2F8",
        accent_color="#FF8A00",
        canvas_color="#F7FAFC",
        canvas_text_color="#111111",
        cta_fill_color="#0057B8",
        cta_text_color="#FFFFFF",
        muted_surface_color="#EDF2F7",
        border_color="#102A43",
    )

    palette = _palette(value)

    assert palette.primary == (16, 42, 67)
    assert palette.secondary == (234, 242, 248)
    assert palette.accent == (255, 138, 0)

    assert palette.canvas == (247, 250, 252)
    assert palette.canvas_text == (17, 17, 17)

    assert palette.cta_fill == (0, 87, 184)
    assert palette.cta_text == (255, 255, 255)

    assert palette.muted_surface == (237, 242, 247)
    assert palette.border == (16, 42, 67)


def test_unreadable_requested_text_roles_are_corrected_deterministically() -> None:
    value = CreativeCompositionInput(
        raw_visual=_raw_png(),
        target_width=1080,
        target_height=1080,
        asset_type="social_square",
        headline="Business result",
        supporting_copy="Supported customer outcome.",
        cta="Get started",
        business_name="Acme",
        primary_color="#222222",
        secondary_color="#FFFFFF",
        accent_color="#3366CC",
        canvas_color="#FFFFFF",
        canvas_text_color="#FFFFFF",
        cta_fill_color="#FFFFFF",
        cta_text_color="#FFFFFF",
    )

    palette = _palette(value)

    # White-on-white requests must never survive into final composition.
    assert palette.canvas_text != palette.canvas

    assert palette.cta_text != palette.cta_fill
