"""Shared Creative Engine V2 intent and media adapters.

This module deliberately owns *creative intent*, not provider calls, storage, or
tenant persistence.  It is transient execution data: callers may derive an
image or video plan from the same ``CreativeMasterPlan`` without serialising the
plan into ``creative_metadata``.

The models contain conclusions that are safe to inspect and execute.  They do
not ask a model for hidden reasoning and they never elevate generated prose to
Business Brain authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CompositionFamily = Literal[
    "full_bleed_hero",
    "editorial_split",
    "subject_overlap",
    "centered_campaign_poster",
    "asymmetric_magazine",
    "premium_minimal",
    "brand_offer_spotlight",
    "typographic_led",
]

CreativeFailureClass = Literal[
    "none",
    "concept",
    "raw_media",
    "composition",
    "branding_typography",
    "semantic_grounding",
]

RepairAction = Literal[
    "ready",
    "local_recompose",
    "regenerate_media",
    "reselect_concept",
    "fail_closed",
]


class EngineSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class NormalizedRegion(EngineSchema):
    """A semantic layout region in normalized 0..1 canvas coordinates."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def stays_inside_canvas(self) -> "NormalizedRegion":
        if self.x + self.width > 1.001 or self.y + self.height > 1.001:
            raise ValueError("normalized composition region leaves the canvas")
        return self


class CreativeCompositionPlan(EngineSchema):
    """Media-independent composition intent consumed by image and video."""

    family: CompositionFamily
    focal_subject_region: NormalizedRegion
    headline_safe_zone: NormalizedRegion
    brand_zone: NormalizedRegion
    supporting_copy_zone: NormalizedRegion
    cta_zone: NormalizedRegion
    subject_overlap_allowed: bool = False
    negative_space_strategy: str = Field(min_length=1, max_length=240)
    crop_safety: str = Field(min_length=1, max_length=180)
    hierarchy_priority: tuple[str, ...] = Field(min_length=3, max_length=6)


class CreativeMasterPlan(EngineSchema):
    """One advertising idea expressed independently of a rendering medium."""

    territory_key: str = Field(min_length=1, max_length=80)
    concept_name: str = Field(min_length=1, max_length=100)
    campaign_mechanism: str = Field(min_length=1, max_length=300)
    audience_tension: str = Field(min_length=1, max_length=300)
    single_minded_message: str = Field(min_length=1, max_length=240)
    hero_subject: str = Field(min_length=1, max_length=500)
    hero_action: str = Field(min_length=1, max_length=400)
    visible_consequence: str = Field(min_length=1, max_length=400)
    customer_reason_to_care: str = Field(min_length=1, max_length=300)
    visual_hook: str = Field(min_length=1, max_length=300)
    brand_ownership_reason: str = Field(min_length=1, max_length=300)
    visual_metaphor: str = Field(min_length=1, max_length=300)
    environment: str = Field(min_length=1, max_length=300)
    art_direction: str = Field(min_length=1, max_length=700)
    emotional_tone: str = Field(min_length=1, max_length=180)
    material_language: str = Field(min_length=1, max_length=220)
    lighting_direction: str = Field(min_length=1, max_length=240)
    camera_direction: str = Field(min_length=1, max_length=240)
    brand_presence_strategy: str = Field(min_length=1, max_length=240)
    motion_potential: str = Field(min_length=1, max_length=300)
    narrative_beats: tuple[str, ...] = Field(min_length=3, max_length=6)
    temporal_transition: str = Field(min_length=1, max_length=240)
    composition: CreativeCompositionPlan


class ImageExecutionPlan(EngineSchema):
    """The image adapter's translation of shared creative intent."""

    media_type: Literal["image"] = "image"
    composition_family: CompositionFamily
    focal_point: str = Field(min_length=1, max_length=180)
    crop_direction: str = Field(min_length=1, max_length=180)
    text_safe_strategy: str = Field(min_length=1, max_length=240)
    still_prompt: str = Field(min_length=1, max_length=5000)


class VideoBeat(EngineSchema):
    name: Literal["hook", "setup", "tension", "consequence", "resolution", "cta"]
    purpose: str = Field(min_length=1, max_length=180)
    visual: str = Field(min_length=1, max_length=360)
    motion: str = Field(min_length=1, max_length=220)
    duration_fraction: float = Field(gt=0, le=1)
    text_safe_strategy: str = Field(min_length=1, max_length=180)


class VideoExecutionPlan(EngineSchema):
    """The video adapter's shot/timeline translation of shared intent."""

    media_type: Literal["video"] = "video"
    composition_family: CompositionFamily
    opening_hook: str = Field(min_length=1, max_length=260)
    scene_plan: tuple[VideoBeat, ...] = Field(min_length=3, max_length=6)
    camera_motion: str = Field(min_length=1, max_length=240)
    continuity: str = Field(min_length=1, max_length=240)
    pacing: str = Field(min_length=1, max_length=180)
    cta_end_frame: str = Field(min_length=1, max_length=260)


class CreativeQualityAssessment(EngineSchema):
    """Shared quality vocabulary for image and representative-frame review."""

    campaign_idea_strength: int = Field(ge=0, le=100)
    brand_specificity: int = Field(ge=0, le=100)
    visual_originality: int = Field(ge=0, le=100)
    scroll_stopping_power: int = Field(ge=0, le=100)
    hierarchy: int = Field(ge=0, le=100)
    composition: int = Field(ge=0, le=100)
    negative_space_quality: int = Field(ge=0, le=100)
    focal_clarity: int = Field(ge=0, le=100)
    emotional_relevance: int = Field(ge=0, le=100)
    commercial_polish: int = Field(ge=0, le=100)
    genericness: int = Field(ge=0, le=100)
    replaceable_brand_risk: int = Field(ge=0, le=100)
    ai_cliche_risk: int = Field(ge=0, le=100)
    visual_storytelling: int = Field(ge=0, le=100)
    readability: int = Field(ge=0, le=100)
    brand_consistency: int = Field(ge=0, le=100)
    overall_score: int = Field(ge=0, le=100)


class CreativeRepairDecision(EngineSchema):
    failure_class: CreativeFailureClass
    action: RepairAction
    media_spend_allowed: bool
    codes: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    reason: str = Field(min_length=1, max_length=240)


def validate_composition_plan(plan: CreativeCompositionPlan) -> tuple[str, ...]:
    """Deterministically validate semantic layout relationships before rendering."""

    regions = {
        "headline": plan.headline_safe_zone,
        "supporting_copy": plan.supporting_copy_zone,
        "cta": plan.cta_zone,
        "brand": plan.brand_zone,
    }
    failures: list[str] = []
    values = tuple(regions.items())
    for index, (first_name, first) in enumerate(values):
        for second_name, second in values[index + 1 :]:
            if _regions_overlap(first, second):
                failures.append(f"{first_name}_{second_name}_overlap")
    # ``subject_overlap`` describes a broader image/copy boundary treatment;
    # it never authorizes the focal subject to occupy protected copy regions.
    # Keep this check unconditional so the flag cannot disable copy safety.
    for name, region in regions.items():
        if _regions_overlap(region, plan.focal_subject_region):
            failures.append(f"{name}_focal_subject_overlap")
    if plan.subject_overlap_allowed and plan.family != "subject_overlap":
        failures.append("subject_overlap_permission_requires_subject_overlap_family")
    return tuple(dict.fromkeys(failures))


def composition_plan_is_valid(plan: CreativeCompositionPlan) -> bool:
    return not validate_composition_plan(plan)


@dataclass(frozen=True, slots=True)
class GenericVisualRisk:
    """Deterministic concept/render signal; semantic vision still owns pixels."""

    score: int
    matched_patterns: tuple[str, ...]
    hard_failure: bool


_GENERIC_AI_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("glowing_ai_hub", ("glowing ai brain", "glowing brain", "central ai hub", "glowing hub", "central orb")),
    ("neon_network", ("neon network", "neon connection", "glowing network", "neural network", "digital network", "floating nodes")),
    ("floating_department_icons", ("floating department icons", "floating app icons", "marketing sales support icons", "floating icons")),
    ("futuristic_ui_panels", ("futuristic tech panels", "floating dashboard", "holographic interface", "generic dashboard", "glassmorphism cards")),
    ("generic_saas_scene", ("generic dark saas", "blue futuristic tunnel", "businessman looking at hologram", "robotic hand touching screen")),
    ("meaningless_data_effects", ("glowing data streams", "random hologram", "abstract spheres", "gradient wave", "floating rings")),
)

_GENERIC_RE = re.compile(
    r"\b(?:ai|artificial intelligence|automation|software|platform|business)\b",
    re.IGNORECASE,
)


def detect_generic_visual_shorthand(value: str) -> GenericVisualRisk:
    """Reject recognizable AI/SaaS shorthand as a pattern, not a word ban.

    A single harmless mention is not enough.  Multiple co-occurring visual
    motifs, or a hub/network/icon stack, indicate the familiar generic visual
    grammar the final critic must reject.
    """

    normalized = " ".join(value.casefold().split())
    matches = tuple(
        key
        for key, phrases in _GENERIC_AI_PATTERNS
        if any(phrase in normalized for phrase in phrases)
    )
    ai_context = bool(_GENERIC_RE.search(normalized))
    score = min(100, len(matches) * 24 + (12 if ai_context and matches else 0))
    hard_failure = len(matches) >= 2 or (
        len(matches) == 1 and matches[0] in {"glowing_ai_hub", "neon_network"}
    )
    return GenericVisualRisk(score, matches, hard_failure)


def choose_composition_family(
    *,
    concept_name: str,
    layout_intent: str,
    visual_density: str,
    aspect_ratio: str,
) -> CompositionFamily:
    """Select a family from concept intent, with a stable non-split fallback."""

    intent = f"{concept_name} {layout_intent} {visual_density}".casefold()
    explicit: tuple[tuple[CompositionFamily, tuple[str, ...]], ...] = (
        ("typographic_led", ("typographic", "type-led", "type led")),
        ("subject_overlap", ("overlap", "crosses the", "crosses visual")),
        ("centered_campaign_poster", ("poster", "centered", "central idea")),
        ("asymmetric_magazine", ("asymmetric", "asymmetrical", "magazine", "editorial grid")),
        ("premium_minimal", ("minimal", "quiet confidence", "low density")),
        ("brand_offer_spotlight", ("product-first", "product spotlight", "offer spotlight")),
        ("editorial_split", ("editorial split", "side panel", "split")),
        ("full_bleed_hero", ("full bleed", "image dominant", "image-led")),
    )
    for family, markers in explicit:
        if any(marker in intent for marker in markers):
            return family

    # Stable diversity across concepts.  This is deliberately not random and
    # keeps a campaign from collapsing into the legacy image-left/copy-right
    # default when the direction did not name a family.
    digest = int(sha256(f"{concept_name}|{aspect_ratio}".encode()).hexdigest()[:8], 16)
    families: tuple[CompositionFamily, ...] = (
        "full_bleed_hero",
        "asymmetric_magazine",
        "centered_campaign_poster",
        "premium_minimal",
        "subject_overlap",
    )
    return families[digest % len(families)]


def build_creative_master_plan(
    *,
    strategy: Any,
    direction: Any,
    aspect_ratio: str,
    territory_key: str | None = None,
) -> CreativeMasterPlan:
    """Build shared intent from the already grounded strategy and winner.

    ``strategy`` and ``direction`` are intentionally duck-typed at this boundary
    to avoid a schema import cycle.  The caller remains responsible for the
    existing Business Brain authority and story-mode validation.
    """

    concept = direction.selected_concept
    family = choose_composition_family(
        concept_name=concept.concept_name,
        layout_intent=concept.layout_intent,
        visual_density=concept.visual_density,
        aspect_ratio=aspect_ratio,
    )
    composition = _composition_for_family(
        family,
        concept.text_zone,
        concept.focal_area,
        aspect_ratio,
    )
    if not composition_plan_is_valid(composition):
        raise ValueError("shared composition plan failed deterministic validation")
    message = _first_sentence(strategy.headline)
    master = CreativeMasterPlan(
        territory_key=territory_key or _territory_key(concept.concept_name),
        concept_name=concept.concept_name,
        campaign_mechanism=concept.marketing_idea,
        audience_tension=concept.customer_care_reason,
        single_minded_message=message,
        hero_subject=concept.hero_subject,
        hero_action=concept.product_story,
        visible_consequence=concept.hero_relevance,
        customer_reason_to_care=concept.customer_care_reason,
        visual_hook=concept.scroll_stopping_hook,
        brand_ownership_reason=concept.brand_expression,
        visual_metaphor=concept.visual_metaphor,
        environment=concept.background_complexity,
        art_direction=(
            f"{concept.image_style}; {concept.depth}; {concept.camera_direction}; "
            f"{concept.lighting}; {concept.mood}."
        ),
        emotional_tone=concept.mood,
        material_language=concept.image_style,
        lighting_direction=concept.lighting,
        camera_direction=concept.camera_direction,
        brand_presence_strategy=concept.brand_expression,
        motion_potential=(
            f"Expand the decisive {concept.product_story[:180]} into a visible transition "
            "with one clear consequence; preserve the same hero and brand role."
        ),
        narrative_beats=(
            "open on the audience tension",
            "make the competing pressure legible",
            "show the grounded mechanism changing the moment",
            "land on the visible customer consequence",
            "resolve with a restrained branded CTA frame",
        ),
        temporal_transition=(
            "Move from fragmented pressure to one controlled, legible operating rhythm."
        ),
        composition=composition,
    )
    return master


def build_master_plan_from_video_strategy(
    *,
    strategy: Any,
    instructions: str,
    aspect_ratio: str,
) -> CreativeMasterPlan:
    """Adapt an existing grounded video strategy into shared creative intent.

    This is an adapter for the current provider-neutral video contract.  It does
    not run a second Creative Director and it does not invent business facts: all
    story material comes from the already validated strategy/request fields.
    """

    scenes = tuple(getattr(strategy, "scenes", ()))
    first_visual = str(getattr(scenes[0], "visual", "the opening campaign moment")) if scenes else "the opening campaign moment"
    action_visual = str(getattr(scenes[1], "visual", first_visual)) if len(scenes) > 1 else first_visual
    end_card = str(getattr(strategy, "end_card", "a restrained branded end frame"))
    hook = str(getattr(strategy, "hook", "the campaign tension"))
    storyboard = str(getattr(strategy, "storyboard_summary", "the grounded campaign story"))
    script = str(getattr(strategy, "script", "the validated campaign script"))
    territory_key = str(
        getattr(strategy, "creative_territory_key", None)
        or _territory_key(storyboard)
    )[:80]
    concept_name = str(
        getattr(strategy, "creative_concept_name", None)
        or storyboard[:100]
    )[:100]
    campaign_mechanism = str(
        getattr(strategy, "campaign_mechanism", None)
        or storyboard[:300]
    )[:300]
    family = choose_composition_family(
        concept_name=storyboard[:100],
        layout_intent=str(getattr(strategy, "shot_plan", "")),
        visual_density="medium",
        aspect_ratio=aspect_ratio,
    )
    composition = _composition_for_family(
        family,
        "protect caption-safe negative space",
        "the hero action remains crop-safe across the requested ratio",
        aspect_ratio,
    )
    return CreativeMasterPlan(
        territory_key=territory_key,
        concept_name=concept_name,
        campaign_mechanism=campaign_mechanism,
        audience_tension=instructions[:300],
        single_minded_message=hook[:240],
        hero_subject=first_visual[:500],
        hero_action=action_visual[:400],
        visible_consequence=end_card[:400],
        customer_reason_to_care=instructions[:300],
        visual_hook=hook[:300],
        brand_ownership_reason=end_card[:300],
        visual_metaphor=storyboard[:300],
        environment=first_visual[:300],
        art_direction=(
            f"{', '.join(str(item) for item in getattr(strategy, 'shot_plan', ()))[:360]}; "
            f"{getattr(strategy, 'continuity_direction', '')[:180]}"
        )[:700],
        emotional_tone="grounded and decisive",
        material_language="the validated video visual direction",
        lighting_direction="preserve the validated continuity direction",
        camera_direction=(
            ", ".join(str(item) for item in getattr(strategy, "shot_plan", ()))
            or "controlled campaign framing"
        )[:240],
        brand_presence_strategy=end_card[:240],
        motion_potential=(
            ", ".join(str(getattr(scene, "motion", "controlled motion")) for scene in scenes)
            or "controlled motion that makes the campaign mechanism legible"
        )[:300],
        narrative_beats=(
            "open on the audience tension",
            "establish the grounded campaign situation",
            "make the mechanism legible through action",
            "land on the visible consequence",
            "resolve with the validated end card",
        ),
        temporal_transition="Use the existing scenes to move from tension to consequence without changing the campaign idea.",
        composition=composition,
    )


def build_image_execution_plan(
    master: CreativeMasterPlan,
    *,
    palette_instruction: str = "",
    max_prompt_chars: int = 5000,
) -> ImageExecutionPlan:
    """Translate the same master idea into a still-image provider request."""

    composition = master.composition
    prompt = build_image_prompt_v2(
        master,
        palette_instruction=palette_instruction,
        max_chars=max_prompt_chars,
    )
    return ImageExecutionPlan(
        composition_family=composition.family,
        focal_point=master.hero_subject,
        crop_direction=(
            f"Protect the focal subject in the {composition.focal_subject_region.x:.2f} to "
            f"{composition.focal_subject_region.x + composition.focal_subject_region.width:.2f} "
            "horizontal band; keep crop-safe margins for social formats."
        ),
        text_safe_strategy=composition.negative_space_strategy,
        still_prompt=prompt,
    )


def build_video_execution_plan(
    master: CreativeMasterPlan,
    *,
    duration_seconds: int = 15,
) -> VideoExecutionPlan:
    """Expand the shared still-worthy idea into a bounded shot plan."""

    if duration_seconds not in {6, 8, 15, 30}:
        raise ValueError("video duration is invalid")
    beats = master.narrative_beats
    names: tuple[Literal["hook", "setup", "tension", "consequence", "resolution", "cta"], ...] = (
        "hook", "setup", "tension", "consequence", "resolution"
    )
    fractions = (0.16, 0.18, 0.22, 0.22, 0.22)
    scene_plan = tuple(
        VideoBeat(
            name=name,
            purpose=beats[index],
            visual=(
                master.hero_subject if index == 0 else
                f"{master.hero_action} {master.visible_consequence}"
            )[:360],
            motion=(
                "Immediate visual reveal with a controlled push-in."
                if index == 0
                else master.motion_potential
            )[:220],
            duration_fraction=fraction,
            text_safe_strategy=master.composition.negative_space_strategy,
        )
        for index, (name, fraction) in enumerate(zip(names, fractions, strict=True))
    )
    return VideoExecutionPlan(
        composition_family=master.composition.family,
        opening_hook=master.visual_hook,
        scene_plan=scene_plan,
        camera_motion=master.camera_direction,
        continuity=(
            f"Keep {master.hero_subject[:140]} visually continuous; preserve material, "
            "lighting, palette, and the same composition family across cuts."
        ),
        pacing=("Fast, legible hook; measured middle; decisive final two seconds."),
        cta_end_frame=(
            f"Use the same {master.composition.family} hierarchy for a quiet end frame; "
            "apply exact brand copy and CTA as deterministic overlays."
        ),
    )


def build_image_prompt_v2(
    master: CreativeMasterPlan,
    *,
    palette_instruction: str = "",
    max_chars: int = 5000,
) -> str:
    """Create executable provider direction for the final intended layout."""

    if not 800 <= max_chars <= 5000:
        raise ValueError("image prompt budget is invalid")
    c = master.composition
    sections = (
        "CREATIVE ENGINE V2 — STILL IMAGE EXECUTION",
        f"TERRITORY KEY: {master.territory_key}",
        f"CONCEPT: {master.concept_name}",
        "ADVERTISING IDEA: campaign-specific category scene with one grounded "
        f"visual mechanism; {master.campaign_mechanism[:180]}",
        f"AUDIENCE TENSION: {master.audience_tension[:180]}",
        f"HERO: {master.hero_subject[:220]}. Action: {master.hero_action[:120]}. Consequence: {master.visible_consequence[:120]}.",
        f"VISUAL HOOK: {master.visual_hook[:180]}",
        f"ART DIRECTION: {master.art_direction[:360]}",
        f"COMPOSITION FAMILY: {c.family}",
        f"FOCAL SUBJECT: normalized region x={c.focal_subject_region.x:.2f}, y={c.focal_subject_region.y:.2f}, width={c.focal_subject_region.width:.2f}, height={c.focal_subject_region.height:.2f}.",
        f"HEADLINE SAFE ZONE: normalized region x={c.headline_safe_zone.x:.2f}, y={c.headline_safe_zone.y:.2f}, width={c.headline_safe_zone.width:.2f}, height={c.headline_safe_zone.height:.2f}. Keep it low-detail and object-free.",
        f"NEGATIVE SPACE: {c.negative_space_strategy[:180]}",
        f"CROP SAFETY: {c.crop_safety[:160]}",
        f"MATERIAL / LIGHT: {master.material_language[:100]}; {master.lighting_direction[:100]}",
        palette_instruction.strip(),
        "RAW RENDER RULES: No text, letters, or numbers. No logos, UI, dashboards, fake packaging, watermarks, floating app icons, glowing AI hubs, neon networks, generic tech panels, or decorative AI/SaaS shorthand.",
        "Render the artwork for this composition; the application will add the exact real logo, headline, support copy, offer, and CTA later.",
    )
    prompt = "\n".join(section for section in sections if section)
    if len(prompt) > max_chars:
        # The campaign idea, hero, and layout contract are mandatory. Shorten
        # only descriptive sections; fail closed rather than silently truncating
        # the protected layout rules.
        protected = (
            sections[0:4]
            + sections[6:13]
            + sections[-2:]
        )
        dynamic = "\n".join(
            section for section in sections[4:6] + sections[13:-2] if section
        )
        remaining = max_chars - len("\n".join(protected)) - 1
        if remaining < 120:
            raise ValueError("image prompt mandatory contract exceeds provider budget")
        prompt = "\n".join(protected) + "\n" + dynamic[: remaining - 1].rstrip() + "…"
    if len(prompt) > max_chars:
        raise ValueError("image prompt exceeds provider budget")
    return prompt


def route_creative_failure(
    *,
    concept_failure: bool = False,
    semantic_grounding_failure: bool = False,
    raw_media_failure: bool = False,
    composition_failure: bool = False,
    branding_typography_failure: bool = False,
    codes: tuple[str, ...] = (),
) -> CreativeRepairDecision:
    """Centralize cost-aware repair routing for both media adapters."""

    if semantic_grounding_failure:
        return CreativeRepairDecision(
            failure_class="semantic_grounding",
            action="fail_closed",
            media_spend_allowed=False,
            codes=codes,
            reason="Unsupported claims or capability evidence require concept repair, not cosmetic changes.",
        )
    if concept_failure:
        return CreativeRepairDecision(
            failure_class="concept",
            action="reselect_concept",
            media_spend_allowed=False,
            codes=codes,
            reason="The advertising idea is generic or replaceable; reselect the territory before media spend.",
        )
    if composition_failure or branding_typography_failure:
        return CreativeRepairDecision(
            failure_class="composition" if composition_failure else "branding_typography",
            action="local_recompose",
            media_spend_allowed=False,
            codes=codes,
            reason="The raw media can remain; repair deterministic layout, typography, crop, or branding locally.",
        )
    if raw_media_failure:
        return CreativeRepairDecision(
            failure_class="raw_media",
            action="regenerate_media",
            media_spend_allowed=True,
            codes=codes,
            reason="The rendered subject or framing is wrong and cannot be repaired by the compositor.",
        )
    return CreativeRepairDecision(
        failure_class="none",
        action="ready",
        media_spend_allowed=False,
        codes=(),
        reason="The shared creative plan and rendered execution meet the current gates.",
    )


def _composition_for_family(
    family: CompositionFamily,
    text_zone: str,
    focal_area: str,
    aspect_ratio: str = "1:1",
) -> CreativeCompositionPlan:
    subject = NormalizedRegion(x=0.55, y=0.18, width=0.38, height=0.68)
    headline = NormalizedRegion(x=0.08, y=0.12, width=0.40, height=0.28)
    brand = NormalizedRegion(x=0.08, y=0.06, width=0.30, height=0.035)
    supporting = NormalizedRegion(x=0.08, y=0.44, width=0.37, height=0.18)
    cta = NormalizedRegion(x=0.08, y=0.70, width=0.28, height=0.09)
    overlap = False
    negative = f"{text_zone}; keep the headline and logo field quiet and free of meaningful objects."
    crop = f"{focal_area}; preserve the hero through 1:1, 4:5, and 9:16 crops."

    if family == "centered_campaign_poster":
        subject = NormalizedRegion(x=0.22, y=0.15, width=0.56, height=0.44)
        headline = NormalizedRegion(x=0.14, y=0.62, width=0.72, height=0.16)
        brand = NormalizedRegion(x=0.34, y=0.065, width=0.32, height=0.07)
        supporting = NormalizedRegion(x=0.18, y=0.79, width=0.64, height=0.08)
        cta = NormalizedRegion(x=0.35, y=0.875, width=0.30, height=0.065)
    elif family == "subject_overlap":
        # The hero remains a full-canvas visual with a deliberate right-side
        # focal field. It crosses the conceptual image/copy boundary without
        # entering any protected brand or text region.
        subject = NormalizedRegion(x=0.52, y=0.28, width=0.40, height=0.54)
        headline = NormalizedRegion(x=0.08, y=0.19, width=0.42, height=0.24)
        supporting = NormalizedRegion(x=0.08, y=0.48, width=0.34, height=0.15)
        cta = NormalizedRegion(x=0.08, y=0.69, width=0.28, height=0.09)
        overlap = True
    elif family == "premium_minimal":
        subject = NormalizedRegion(x=0.60, y=0.22, width=0.28, height=0.52)
        headline = NormalizedRegion(x=0.08, y=0.22, width=0.38, height=0.20)
        supporting = NormalizedRegion(x=0.08, y=0.48, width=0.31, height=0.14)
        cta = NormalizedRegion(x=0.08, y=0.68, width=0.25, height=0.08)
    elif family == "typographic_led":
        subject = NormalizedRegion(x=0.30, y=0.54, width=0.40, height=0.30)
        headline = NormalizedRegion(x=0.10, y=0.16, width=0.80, height=0.25)
        brand = NormalizedRegion(x=0.10, y=0.065, width=0.30, height=0.07)
        supporting = NormalizedRegion(x=0.16, y=0.45, width=0.68, height=0.08)
        cta = NormalizedRegion(x=0.37, y=0.875, width=0.26, height=0.065)
    elif family == "editorial_split":
        subject = NormalizedRegion(x=0.49, y=0.08, width=0.46, height=0.84)
    elif family == "brand_offer_spotlight":
        subject = NormalizedRegion(x=0.26, y=0.24, width=0.52, height=0.50)
        headline = NormalizedRegion(x=0.09, y=0.12, width=0.40, height=0.09)
        supporting = NormalizedRegion(x=0.09, y=0.77, width=0.40, height=0.08)
        cta = NormalizedRegion(x=0.67, y=0.83, width=0.24, height=0.08)
    elif family == "asymmetric_magazine":
        subject = NormalizedRegion(x=0.48, y=0.20, width=0.43, height=0.62)
        headline = NormalizedRegion(x=0.08, y=0.10, width=0.34, height=0.24)
        supporting = NormalizedRegion(x=0.12, y=0.58, width=0.30, height=0.12)
        cta = NormalizedRegion(x=0.12, y=0.75, width=0.24, height=0.08)

    # Tall placements have larger platform-safe top and bottom zones. Keep the
    # same semantic family while adapting its normalized copy rhythm for mobile
    # crops instead of letting deterministic safe-margin validation reject it.
    if _is_tall_ratio(aspect_ratio):
        subject = NormalizedRegion(x=0.30, y=0.57, width=0.40, height=0.23)
        brand = NormalizedRegion(x=0.10, y=0.12, width=0.62, height=0.055)
        headline = NormalizedRegion(x=0.09, y=0.22, width=0.82, height=0.20)
        supporting = NormalizedRegion(x=0.09, y=0.45, width=0.82, height=0.09)
        cta = NormalizedRegion(x=0.09, y=0.81, width=0.46, height=0.06)
        if family == "centered_campaign_poster":
            subject = NormalizedRegion(x=0.14, y=0.20, width=0.72, height=0.35)
            headline = NormalizedRegion(x=0.10, y=0.58, width=0.80, height=0.10)
            supporting = NormalizedRegion(x=0.10, y=0.70, width=0.80, height=0.07)
            cta = NormalizedRegion(x=0.10, y=0.80, width=0.50, height=0.06)
        elif family == "subject_overlap":
            subject = NormalizedRegion(x=0.54, y=0.20, width=0.36, height=0.48)
            headline = NormalizedRegion(x=0.07, y=0.20, width=0.45, height=0.18)
            supporting = NormalizedRegion(x=0.07, y=0.42, width=0.40, height=0.12)
            cta = NormalizedRegion(x=0.07, y=0.60, width=0.42, height=0.08)
        elif family == "typographic_led":
            subject = NormalizedRegion(x=0.24, y=0.54, width=0.52, height=0.25)
            headline = NormalizedRegion(x=0.10, y=0.14, width=0.80, height=0.25)
            supporting = NormalizedRegion(x=0.10, y=0.43, width=0.80, height=0.10)
            cta = NormalizedRegion(x=0.10, y=0.78, width=0.45, height=0.07)
    return CreativeCompositionPlan(
        family=family,
        focal_subject_region=subject,
        headline_safe_zone=headline,
        brand_zone=brand,
        supporting_copy_zone=supporting,
        cta_zone=cta,
        subject_overlap_allowed=overlap,
        negative_space_strategy=negative,
        crop_safety=crop,
        hierarchy_priority=("hero subject", "headline", "brand identity", "supporting copy", "CTA"),
    )


def _territory_key(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return normalized[:80] or "creative-territory"


def _is_tall_ratio(aspect_ratio: str) -> bool:
    try:
        width, height = (float(part) for part in aspect_ratio.split(":", 1))
    except (AttributeError, TypeError, ValueError):
        return False
    return width > 0 and height / width >= 1.65


def _first_sentence(value: str) -> str:
    sentence = re.split(r"[.!?]", value.strip(), maxsplit=1)[0].strip()
    return (sentence or value.strip())[:240]


def _regions_overlap(first: NormalizedRegion, second: NormalizedRegion) -> bool:
    return (
        min(first.x + first.width, second.x + second.width) > max(first.x, second.x)
        and min(first.y + first.height, second.y + second.height) > max(first.y, second.y)
    )
