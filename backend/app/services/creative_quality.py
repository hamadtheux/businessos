from __future__ import annotations

from itertools import combinations

from pydantic import BaseModel, ConfigDict, Field

from app.services.creative_compositor import (
    Box,
    CreativeCompositionResult,
    CreativeQualityReport,
)


class CreativeQualityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    brand_consistency: int = Field(ge=0, le=100)
    hierarchy: int = Field(ge=0, le=100)
    readability: int = Field(ge=0, le=100)
    visual_balance: int = Field(ge=0, le=100)
    subject_clarity: int = Field(ge=0, le=100)
    marketing_strength: int = Field(ge=0, le=100)
    cta_clarity: int = Field(ge=0, le=100)
    platform_fit: int = Field(ge=0, le=100)
    originality: int = Field(ge=0, le=100)
    visual_polish: int = Field(ge=0, le=100)
    offer_clarity: int = Field(ge=0, le=100)
    spacing: int = Field(ge=0, le=100)
    composition: int = Field(ge=0, le=100)
    pr_safety: int = Field(ge=0, le=100)
    overall_score: int = Field(ge=0, le=100)
    hard_failures: tuple[str, ...] = Field(max_length=12)
    improvement_actions: tuple[str, ...] = Field(max_length=8)
    approved_for_delivery: bool
    failure_kind: str | None = Field(default=None, max_length=40)


def assess_creative_quality(
    result: CreativeCompositionResult,
    *,
    threshold: int,
) -> CreativeQualityAssessment:
    if not 60 <= threshold <= 95:
        raise ValueError("creative quality threshold is invalid")
    report = result.quality
    failures: list[str] = []
    if not report.valid_png:
        failures.append("final_image_malformed")
    if not report.exact_dimensions or (result.width, result.height) != (
        report.target_dimensions
    ):
        failures.append("output_dimensions_wrong")
    if report.minimum_contrast_ratio < 4.5:
        failures.append("contrast_unacceptable")
    effective_bounds = {
        name: report.component_bounds.get(name, bounds)
        for name, bounds in report.text_bounds.items()
    }
    elements: list[tuple[str, Box]] = list(effective_bounds.items())
    if report.logo_bounds is not None:
        elements.append(("logo", report.logo_bounds))
    for (first_name, first), (second_name, second) in combinations(elements, 2):
        if _overlap_area(first, second) > 0:
            failures.append(f"{first_name}_{second_name}_overlap")
    # Retain a direct text-glyph overlap gate as well as the stricter full
    # component-panel gate. A malformed/mutated report must not hide colliding
    # copy merely because its CTA or offer panel bounds are still separate.
    for (first_name, first), (second_name, second) in combinations(
        report.text_bounds.items(),
        2,
    ):
        if _overlap_area(first, second) > 0:
            failures.append(f"{first_name}_{second_name}_text_overlap")
    for name, component in report.component_bounds.items():
        text = report.text_bounds.get(name)
        if text is None or not _inside(text, component):
            failures.append(f"{name}_text_outside_component")
    safe_box = (
        report.safe_margin,
        report.safe_margin,
        result.width - report.safe_margin,
        result.height - report.safe_margin,
    )
    if any(not _inside(bounds, safe_box) for _name, bounds in elements):
        failures.append("component_outside_safe_area")
    if report.copy_safe_area_ratio < 0.12:
        failures.append("extreme_unused_or_unbalanced_space")
    if (
        not report.intentional_negative_space
        and report.largest_empty_edge_ratio > 0.40
        and report.occupied_area_ratio < 0.08
    ):
        failures.append("extreme_meaningless_whitespace")
    rendered_values = [
        " ".join(value.casefold().split())
        for value in report.rendered_text.values()
        if value.strip()
    ]
    if len(rendered_values) != len(set(rendered_values)):
        failures.append("duplicate_deterministic_text")
    if (
        report.visual_complexity > 0.84
        and report.selected_zone_quiet_score < 0.30
        and report.selected_layout in {
        "cinematic_overlay",
        "minimal_hero",
        "vertical_story",
        }
    ):
        failures.append("raw_visual_too_complex")

    minimum_edge = max(1, min(result.width, result.height))
    headline_size = report.font_sizes.get("headline", 0)
    supporting_size = report.font_sizes.get("supporting_copy", 0)
    hierarchy_ratio = headline_size / max(1, supporting_size)
    hierarchy = (
        94
        if 1.45 <= hierarchy_ratio <= 4.2
        else _bounded(90 - abs(hierarchy_ratio - 2.2) * 20)
    )
    support_legibility = supporting_size / minimum_edge
    readability = _bounded(
        58
        + min(24, max(0.0, report.minimum_contrast_ratio - 3.0) * 8)
        + min(16, support_legibility / 0.018 * 16)
    )
    composition = max(0, min(100, round(report.layout_score)))
    spacing = _spacing_score(elements, minimum_edge)
    visual_balance = _bounded(
        96
        - report.content_centroid_offset * 72
        - (
            0
            if report.intentional_negative_space
            else report.largest_empty_edge_ratio * 28
        )
    )
    cta_clarity = _component_clarity_score(
        name="cta",
        report=report,
        minimum_edge=minimum_edge,
        contrast=report.cta_contrast_ratio,
        default_when_absent=80,
    )
    offer_clarity = _offer_clarity_score(report, minimum_edge)
    identity_bounds = report.logo_bounds or effective_bounds.get("business_name")
    identity_area_ratio = (
        _area(identity_bounds) / max(1, result.width * result.height)
        if identity_bounds is not None
        else 0.0
    )
    brand_consistency = _bounded(
        60
        + (15 if identity_bounds is not None else 0)
        + (10 if 0.0005 <= identity_area_ratio <= 0.08 else 0)
        + min(10, max(0.0, report.minimum_contrast_ratio - 4.5) * 3)
    )
    # Deterministic saliency is an edge/contrast proxy, not semantic subject
    # recognition. Keep this score deliberately conservative; the optional
    # vision critic owns semantic focal relevance.
    subject_clarity = _bounded(58 + report.saliency_concentration * 24)
    originality_proxy = _bounded(
        63
        + min(12, report.visual_complexity * 18)
        + (4 if report.selected_layout != "framed_campaign" else 0)
    )
    visual_polish = _bounded(
        readability * 0.30
        + visual_balance * 0.20
        + spacing * 0.20
        + composition * 0.30
    )
    marketing_strength = _bounded(
        hierarchy * 0.28
        + cta_clarity * 0.24
        + offer_clarity * 0.18
        + visual_balance * 0.15
        + composition * 0.15
    )
    dimensions = {
        "brand_consistency": brand_consistency,
        "hierarchy": hierarchy,
        "readability": readability,
        "visual_balance": visual_balance,
        "subject_clarity": subject_clarity,
        "marketing_strength": marketing_strength,
        "cta_clarity": cta_clarity,
        "platform_fit": 94 if report.platform_fit else 58,
        "originality": originality_proxy,
        "visual_polish": visual_polish,
        "offer_clarity": offer_clarity,
        "spacing": spacing,
        "composition": composition,
        # No detected deterministic violation is evidence of passing gates,
        # not proof of perfect PR safety.
        "pr_safety": 80 if not failures else 58,
    }
    overall = round(sum(dimensions.values()) / len(dimensions))
    if failures:
        overall = min(overall, threshold - 1)
    actions = _improvements(failures, overall, threshold)
    approved = not failures and overall >= threshold
    failure_kind = None
    if not approved:
        failure_kind = (
            "raw_visual"
            if "raw_visual_too_complex" in failures
            else "layout"
        )
    return CreativeQualityAssessment(
        **dimensions,
        overall_score=overall,
        hard_failures=tuple(dict.fromkeys(failures)),
        improvement_actions=actions,
        approved_for_delivery=approved,
        failure_kind=failure_kind,
    )


def _component_clarity_score(
    *,
    name: str,
    report: CreativeQualityReport,
    minimum_edge: int,
    contrast: float | None,
    default_when_absent: int,
) -> int:
    bounds = report.component_bounds.get(name)
    font_size = report.font_sizes.get(name)
    if bounds is None or font_size is None or contrast is None:
        return default_when_absent
    font_score = min(24.0, font_size / minimum_edge / 0.018 * 24)
    contrast_score = min(24.0, max(0.0, contrast - 3.0) * 8)
    area_ratio = _area(bounds) / max(1, report.target_dimensions[0] * report.target_dimensions[1])
    size_score = 16.0 if 0.002 <= area_ratio <= 0.16 else 6.0
    return _bounded(35 + font_score + contrast_score + size_score)


def _offer_clarity_score(
    report: CreativeQualityReport,
    minimum_edge: int,
) -> int:
    if "offer" not in report.text_bounds:
        return 82
    score = _component_clarity_score(
        name="offer",
        report=report,
        minimum_edge=minimum_edge,
        contrast=report.offer_contrast_ratio,
        default_when_absent=45,
    )
    offer_size = report.font_sizes.get("offer", 0)
    headline_size = report.font_sizes.get("headline", 1)
    ratio = offer_size / max(1, headline_size)
    if ratio > 0.85:
        score -= 22
    elif 0.22 <= ratio <= 0.70:
        score += 5
    return _bounded(score)


def _spacing_score(elements: list[tuple[str, Box]], minimum_edge: int) -> int:
    if len(elements) < 2:
        return 82
    minimum_gap = min(
        _box_gap(first, second)
        for (_first_name, first), (_second_name, second) in combinations(elements, 2)
    )
    return _bounded(68 + min(27, minimum_gap / max(1, minimum_edge) * 700))


def _box_gap(first: Box, second: Box) -> float:
    horizontal = max(first[0] - second[2], second[0] - first[2], 0)
    vertical = max(first[1] - second[3], second[1] - first[3], 0)
    return (horizontal ** 2 + vertical ** 2) ** 0.5


def _inside(inner: Box, outer: Box) -> bool:
    return (
        inner[0] >= outer[0]
        and inner[1] >= outer[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
        and inner[2] > inner[0]
        and inner[3] > inner[1]
    )


def _area(bounds: Box | None) -> int:
    if bounds is None:
        return 0
    return max(0, bounds[2] - bounds[0]) * max(0, bounds[3] - bounds[1])


def _bounded(value: float) -> int:
    return max(0, min(100, round(value)))


def _improvements(
    failures: list[str],
    score: int,
    threshold: int,
) -> tuple[str, ...]:
    actions: list[str] = []
    if any("overlap" in failure for failure in failures):
        actions.append("select a layout with more separation between required elements")
    if "contrast_unacceptable" in failures:
        actions.append("strengthen the controlled text surface contrast")
    if "raw_visual_too_complex" in failures:
        actions.append("regenerate a quieter visual with a protected copy corridor")
    if "extreme_unused_or_unbalanced_space" in failures:
        actions.append("rebalance the hero and copy zones")
    if "extreme_meaningless_whitespace" in failures:
        actions.append("reduce the extreme unused edge and rebalance campaign content")
    if "component_outside_safe_area" in failures:
        actions.append("move the complete CTA or offer component inside the safe area")
    if not actions and score < threshold:
        actions.append("use the next-ranked deterministic composition candidate")
    return tuple(actions[:8])


def _overlap_area(first: Box, second: Box) -> int:
    width = max(0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0, min(first[3], second[3]) - max(first[1], second[1]))
    return width * height
