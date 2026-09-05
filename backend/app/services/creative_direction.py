from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.schemas.marketing import CreativeStrategyProposal
from app.services.creative_research import (
    CreativeResearchBundle,
    PublicCreativeResearchContext,
)


class DirectionSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


_DIRECT_COPY_LANGUAGE = re.compile(
    r"\b(copy (?:this|the (?:design|layout|artwork))|clone (?:this|the)|"
    r"replicate (?:this|the)|same design as|duplicate this layout|"
    r"pixel[- ]perfect (?:copy|replica))\b",
    re.IGNORECASE,
)
_URL_LANGUAGE = re.compile(r"https?://|www\.", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "the",
        "to",
        "use",
        "with",
    }
)


class CreativeConceptScorecard(DirectionSchema):
    brand_fit: int = Field(ge=0, le=100)
    marketing_strength: int = Field(ge=0, le=100)
    distinctiveness: int = Field(ge=0, le=100)
    visual_sophistication: int = Field(ge=0, le=100)
    audience_relevance: int = Field(ge=0, le=100)
    platform_suitability: int = Field(ge=0, le=100)
    offer_clarity: int = Field(ge=0, le=100)
    cta_clarity: int = Field(ge=0, le=100)
    composition_feasibility: int = Field(ge=0, le=100)
    originality: int = Field(ge=0, le=100)
    pr_safety: int = Field(ge=0, le=100)
    business_brain_grounding: int = Field(ge=0, le=100)
    overall_score: int = Field(ge=0, le=100)


class CreativeConceptProposal(DirectionSchema):
    """One safe Creative Director proposal before server-owned scoring."""

    concept_name: str = Field(min_length=1, max_length=100)
    strategic_reason: str = Field(min_length=1, max_length=300)
    hero_subject: str = Field(min_length=1, max_length=500)
    visual_metaphor: str = Field(min_length=1, max_length=300)
    layout_intent: str = Field(min_length=1, max_length=500)
    focal_area: str = Field(min_length=1, max_length=160)
    text_zone: str = Field(min_length=1, max_length=220)
    offer_treatment: str = Field(min_length=1, max_length=160)
    cta_treatment: str = Field(min_length=1, max_length=160)
    depth: str = Field(min_length=1, max_length=180)
    image_style: str = Field(min_length=1, max_length=220)
    camera_direction: str = Field(min_length=1, max_length=220)
    lighting: str = Field(min_length=1, max_length=240)
    mood: str = Field(min_length=1, max_length=240)
    visual_density: str = Field(min_length=1, max_length=120)
    background_complexity: str = Field(min_length=1, max_length=180)
    brand_expression: str = Field(min_length=1, max_length=300)
    inspiration_principles: tuple[str, ...] = Field(min_length=1, max_length=6)
    avoid_patterns: tuple[str, ...] = Field(min_length=1, max_length=6)
    originality_notes: str = Field(min_length=1, max_length=300)

    @field_validator(
        "concept_name",
        "strategic_reason",
        "hero_subject",
        "visual_metaphor",
        "layout_intent",
        "focal_area",
        "text_zone",
        "offer_treatment",
        "cta_treatment",
        "depth",
        "image_style",
        "camera_direction",
        "lighting",
        "mood",
        "visual_density",
        "background_complexity",
        "brand_expression",
        "originality_notes",
    )
    @classmethod
    def reject_copy_or_url_instructions(cls, value: str) -> str:
        if _DIRECT_COPY_LANGUAGE.search(value) or _URL_LANGUAGE.search(value):
            raise ValueError("creative concepts must use original abstract direction")
        return value

    @field_validator("inspiration_principles", "avoid_patterns")
    @classmethod
    def validate_abstract_principles(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(" ".join(value.split()) for value in values)
        if (
            any(
                not value
                or len(value) > 180
                or _DIRECT_COPY_LANGUAGE.search(value)
                or _URL_LANGUAGE.search(value)
                for value in normalized
            )
            or len({value.casefold() for value in normalized}) != len(normalized)
        ):
            raise ValueError("creative principles must be bounded, unique, and abstract")
        return normalized


class CreativeDirectorSynthesis(DirectionSchema):
    """Exactly three materially different proposals from one typed model call."""

    candidates: tuple[CreativeConceptProposal, ...] = Field(
        min_length=3,
        max_length=3,
    )

    @model_validator(mode="after")
    def candidates_are_materially_different(self) -> "CreativeDirectorSynthesis":
        names = {candidate.concept_name.casefold() for candidate in self.candidates}
        metaphors = {
            " ".join(candidate.visual_metaphor.casefold().split())
            for candidate in self.candidates
        }
        if len(names) != 3 or len(metaphors) != 3:
            raise ValueError("creative concepts must have distinct names and metaphors")
        signatures = tuple(_concept_signature(candidate) for candidate in self.candidates)
        for index, first in enumerate(signatures):
            for second in signatures[index + 1 :]:
                if _jaccard_similarity(first, second) > 0.72:
                    raise ValueError("creative concepts are not materially different")
        return self


class CreativeConceptCandidate(CreativeConceptProposal):
    scorecard: CreativeConceptScorecard


class CreativeDirectionPlan(DirectionSchema):
    candidates: tuple[CreativeConceptCandidate, ...] = Field(
        min_length=3,
        max_length=3,
    )
    selected_concept: CreativeConceptCandidate
    research_fingerprint: str = Field(min_length=16, max_length=64)
    used_live_research: bool
    used_ai_synthesis: bool = False

    @model_validator(mode="after")
    def selected_concept_is_ranked_winner(self) -> "CreativeDirectionPlan":
        winner = max(
            self.candidates,
            key=lambda value: (value.scorecard.overall_score, value.concept_name),
        )
        if winner != self.selected_concept:
            raise ValueError("selected concept must be the highest-scoring candidate")
        return self


@dataclass(frozen=True, slots=True)
class _DesignPattern:
    key: str
    name: str
    industries: frozenset[str]
    objectives: frozenset[str]
    formats: frozenset[str]
    styles: frozenset[str]
    visual_metaphor: str
    layout_intent: str
    focal_area: str
    text_zone: str
    image_style: str
    depth: str
    density: str
    background_complexity: str
    offer_treatment: str
    cta_treatment: str


_PATTERNS: tuple[_DesignPattern, ...] = (
    _DesignPattern(
        key="premium_editorial",
        name="Premium Editorial Focus",
        industries=frozenset(
            {"professional services", "real estate", "retail", "small business"}
        ),
        objectives=frozenset({"brand awareness", "product launch"}),
        formats=frozenset({"social square", "landscape ad"}),
        styles=frozenset({"minimal editorial", "premium modern"}),
        visual_metaphor="A decisive editorial hero moment with one unmistakable subject",
        layout_intent="Asymmetrical editorial composition with disciplined alignment and generous breathing room",
        focal_area="hero subject in the right-middle field",
        text_zone="quiet upper-left to center-left field",
        image_style="high-end editorial commercial photography or refined dimensional rendering",
        depth="controlled foreground-to-background separation",
        density="restrained",
        background_complexity="low detail in the copy field with selective detail around the subject",
        offer_treatment="compact editorial annotation",
        cta_treatment="small dark premium CTA",
    ),
    _DesignPattern(
        key="cinematic_promo",
        name="Cinematic Value Moment",
        industries=frozenset({"commerce", "retail", "technology", "real estate"}),
        objectives=frozenset({"promotional offer", "product launch"}),
        formats=frozenset({"social square", "story vertical", "landscape ad"}),
        styles=frozenset({"bold commercial", "premium modern", "product led"}),
        visual_metaphor="A spotlighted transformation moment that communicates value without literal offer text",
        layout_intent="Cinematic depth with the hero offset from a protected copy corridor",
        focal_area="hero subject in the lower-right or center-right field",
        text_zone="protected left-side copy corridor",
        image_style="cinematic commercial photography or photoreal premium render",
        depth="layered atmosphere with a crisp hero plane",
        density="medium",
        background_complexity="rich near the hero and deliberately quiet behind later typography",
        offer_treatment="small high-contrast offer badge",
        cta_treatment="compact accent CTA",
    ),
    _DesignPattern(
        key="minimal_luxury",
        name="Minimal Brand Signal",
        industries=frozenset(
            {"professional services", "real estate", "healthcare", "retail"}
        ),
        objectives=frozenset({"brand awareness", "lead generation"}),
        formats=frozenset({"social square", "landscape ad", "display banner"}),
        styles=frozenset({"minimal editorial", "premium modern", "trust focused"}),
        visual_metaphor="Quiet confidence expressed through material, light, and a single authentic subject",
        layout_intent="Minimal premium composition with precise margins and calm negative space",
        focal_area="single subject in the right third",
        text_zone="clean left third with low visual noise",
        image_style="restrained luxury editorial photography with authentic materials",
        depth="shallow, elegant depth with soft separation",
        density="low",
        background_complexity="very low in the text zone",
        offer_treatment="restrained outlined chip",
        cta_treatment="light or outlined premium CTA",
    ),
    _DesignPattern(
        key="saas_control_center",
        name="Intelligent Operations Environment",
        industries=frozenset({"technology"}),
        objectives=frozenset({"brand awareness", "lead generation", "product launch"}),
        formats=frozenset({"social square", "landscape ad", "story vertical"}),
        styles=frozenset({"premium modern", "product led"}),
        visual_metaphor="Connected operational intelligence represented through purposeful spatial systems",
        layout_intent="Structured technology environment with a strong visual anchor and modular depth",
        focal_area="system focal point in the center-right field",
        text_zone="uncluttered left-side plane",
        image_style="sophisticated dimensional technology visualization without fake interface text",
        depth="layered architectural depth and controlled perspective",
        density="medium",
        background_complexity="structured detail away from the protected copy plane",
        offer_treatment="compact floating offer chip",
        cta_treatment="filled brand-accent CTA",
    ),
    _DesignPattern(
        key="product_spotlight",
        name="Product Spotlight Story",
        industries=frozenset({"commerce", "retail", "agriculture"}),
        objectives=frozenset({"product launch", "promotional offer"}),
        formats=frozenset({"social square", "story vertical", "landscape ad"}),
        styles=frozenset({"product led", "bold commercial", "premium modern"}),
        visual_metaphor="The product or offering as the source of a desirable real-world moment",
        layout_intent="Product-first commercial composition with an oversized crop and clean conversion zone",
        focal_area="product or service subject in the center-right field",
        text_zone="open upper-left conversion zone",
        image_style="premium product photography with credible surfaces and tactile detail",
        depth="foreground product emphasis with contextual depth",
        density="medium",
        background_complexity="contextual detail kept outside the copy zone",
        offer_treatment="compact corner badge",
        cta_treatment="high-contrast filled CTA",
    ),
    _DesignPattern(
        key="trust_editorial",
        name="Trusted Human Context",
        industries=frozenset({"healthcare", "professional services", "real estate"}),
        objectives=frozenset({"brand awareness", "lead generation"}),
        formats=frozenset({"social square", "landscape ad", "story vertical"}),
        styles=frozenset({"trust focused", "minimal editorial"}),
        visual_metaphor="Credibility made tangible through an authentic environment and measured human presence",
        layout_intent="Calm editorial layout with an honest subject and an orderly information zone",
        focal_area="authentic subject in the right-middle field",
        text_zone="quiet left-side trust zone",
        image_style="natural editorial commercial photography without staged claims or implied outcomes",
        depth="natural environmental depth",
        density="restrained",
        background_complexity="calm and credible with no distracting clinical or legal details",
        offer_treatment="small factual annotation",
        cta_treatment="restrained dark CTA",
    ),
    _DesignPattern(
        key="asymmetric_offer",
        name="Asymmetric Offer Rhythm",
        industries=frozenset({"commerce", "retail", "technology", "small business"}),
        objectives=frozenset({"promotional offer", "event promotion"}),
        formats=frozenset({"social square", "story vertical"}),
        styles=frozenset({"bold commercial", "premium modern"}),
        visual_metaphor="Momentum and access expressed through shape, crop, and directional energy",
        layout_intent="Asymmetrical promotional composition with a strong hero and a separate offer lockup",
        focal_area="dynamic hero in the right two-thirds",
        text_zone="stable left-side hierarchy zone",
        image_style="bold contemporary commercial image-making with controlled graphic energy",
        depth="layered shapes and a distinct hero plane",
        density="energetic but controlled",
        background_complexity="active around the hero, low-noise behind exact copy",
        offer_treatment="small independent offer lockup",
        cta_treatment="compact contrasting CTA",
    ),
    _DesignPattern(
        key="immersive_story",
        name="Immersive Vertical Story",
        industries=frozenset(
            {"agriculture", "commerce", "healthcare", "real estate", "retail", "technology"}
        ),
        objectives=frozenset(
            {"brand awareness", "event promotion", "product launch", "promotional offer"}
        ),
        formats=frozenset({"story vertical"}),
        styles=frozenset({"bold commercial", "premium modern", "product led", "trust focused"}),
        visual_metaphor="An immersive scene that reveals the offering through a mobile-first visual journey",
        layout_intent="Vertical composition with interface-safe top and bottom zones and a protected copy column",
        focal_area="hero subject centered above the lower safe zone",
        text_zone="quiet middle-left column inside story safe areas",
        image_style="mobile-first commercial photography or dimensional illustration",
        depth="vertical depth with a clear near-to-far read",
        density="medium",
        background_complexity="detail outside the copy column and platform-safe margins",
        offer_treatment="compact floating badge",
        cta_treatment="mobile-readable filled CTA",
    ),
)


def build_creative_direction(
    *,
    strategy: CreativeStrategyProposal,
    research: CreativeResearchBundle,
    context: PublicCreativeResearchContext,
    synthesis: CreativeDirectorSynthesis | None = None,
) -> CreativeDirectionPlan:
    proposals = (
        synthesis.candidates
        if synthesis is not None
        else tuple(
            _build_pattern_proposal(
                pattern=pattern,
                strategy=strategy,
                research=research,
                context=context,
            )
            for pattern in _ranked_patterns(context)
        )
    )
    scorecards = _score_candidates(
        proposals,
        strategy=strategy,
        research=research,
        context=context,
    )
    candidates = tuple(
        CreativeConceptCandidate(
            **proposal.model_dump(),
            scorecard=scorecard,
        )
        for proposal, scorecard in zip(proposals, scorecards, strict=True)
    )
    selected = max(
        candidates,
        key=lambda candidate: (
            candidate.scorecard.overall_score,
            candidate.concept_name,
        ),
    )
    return CreativeDirectionPlan(
        candidates=candidates,
        selected_concept=selected,
        research_fingerprint=research.research_fingerprint,
        used_live_research=not research.degraded and research.reference_count > 0,
        used_ai_synthesis=synthesis is not None,
    )


def build_creative_director_task(
    *,
    strategy: CreativeStrategyProposal,
    research: CreativeResearchBundle,
    context: PublicCreativeResearchContext,
) -> str:
    """Build one bounded task containing principles, never source artwork or URLs."""
    pattern_lines = "\n".join(
        (
            f"- {pattern.name}: {pattern.visual_metaphor}; "
            f"{pattern.layout_intent}; {pattern.image_style}."
        )
        for pattern in _ranked_patterns(context)
    )
    research_principles = "; ".join(
        (
            *research.dominant_patterns,
            *research.emerging_patterns,
            *research.recommended_visual_directions,
        )[:12]
    ) or "No live research available; rely on the internal abstract pattern guidance."
    avoid_patterns = "; ".join(
        (*research.avoid_patterns, *research.originality_constraints)[:10]
    )
    task = (
        "Act as a senior advertising Creative Director. Produce exactly three "
        "materially different, executable visual concepts in one typed response. "
        "The candidates must differ in hero idea, metaphor, image-making approach, "
        "camera direction, and spatial rhythm—not merely color. Do not self-score.\n\n"
        "TRUSTED CAMPAIGN STRATEGY:\n"
        f"- Goal: {strategy.marketing_goal}\n"
        f"- Audience: {strategy.target_audience}\n"
        f"- Audience insight: {strategy.audience_insight}\n"
        f"- Campaign angle: {strategy.campaign_angle}\n"
        f"- Intended headline: {strategy.headline}\n"
        f"- Intended offer: {strategy.offer or 'none'}\n"
        f"- Intended CTA: {strategy.cta or 'none'}\n"
        f"- Supported visual concept: {strategy.visual_concept}\n"
        f"- Supported hero subject: {strategy.subject_focus}\n"
        f"- Brand treatment: {strategy.brand_treatment}\n\n"
        "PUBLIC-SAFE CAMPAIGN DIMENSIONS:\n"
        f"- Industry: {context.industry}\n"
        f"- Objective: {context.campaign_objective}\n"
        f"- Channel: {context.channel}\n"
        f"- Format: {context.creative_format}\n"
        f"- Style family: {context.style_family}\n\n"
        "ABSTRACT RESEARCH SIGNALS ONLY:\n"
        f"{research_principles[:1200]}\n"
        f"Avoid: {avoid_patterns[:900]}\n\n"
        "INTERNAL FALLBACK GUIDANCE (combine or depart from it thoughtfully):\n"
        f"{pattern_lines[:1200]}\n\n"
        "OUTPUT RULES:\n"
        "- Return exactly three candidates through the required typed schema.\n"
        "- Ground every subject and factual implication in the trusted strategy.\n"
        "- Treat an offer as a controlled supporting element, not the automatic hero.\n"
        "- Reserve a feasible quiet zone for exact deterministic copy and logo.\n"
        "- Use inspiration as abstract rhythm, hierarchy, lighting, density, and style only.\n"
        "- Never copy, clone, replicate, duplicate, or imitate a source design.\n"
        "- Never include URLs, evidence IDs, external actions, hidden reasoning, or chain-of-thought.\n"
        "- The raw image will contain no typography; describe visual direction, not final copy."
    )
    return task[:4000]


def build_visual_art_direction(
    *,
    strategy: CreativeStrategyProposal,
    direction: CreativeDirectionPlan,
    context: PublicCreativeResearchContext,
    aspect_ratio: str,
    primary_color: str | None,
    secondary_color: str | None,
    accent_color: str | None,
    correction: str | None = None,
) -> str:
    concept = direction.selected_concept
    campaign_angle = _without_deterministic_copy(
        strategy.campaign_angle,
        strategy,
        fallback="A value-led campaign grounded in the supported offering.",
    )
    hero_subject = _without_deterministic_copy(
        concept.hero_subject,
        strategy,
        fallback="The supported business offering as the single hero subject.",
    )
    visual_concept = _without_deterministic_copy(
        strategy.visual_concept,
        strategy,
        fallback="A premium commercial environment with one clear hero subject.",
    )
    colors = ", ".join(
        color
        for color in (primary_color, secondary_color, accent_color)
        if color
    ) or "restrained brand-compatible neutrals"
    strategic_reason = _without_deterministic_copy(
        concept.strategic_reason,
        strategy,
        fallback="A grounded commercial premise led by the supported offering.",
    )
    visual_metaphor = _without_deterministic_copy(
        concept.visual_metaphor,
        strategy,
        fallback="A clear visual metaphor grounded in the supported offering.",
    )
    image_style = _without_deterministic_copy(
        concept.image_style,
        strategy,
        fallback="professionally art-directed commercial image-making",
    )
    camera = _without_deterministic_copy(
        concept.camera_direction,
        strategy,
        fallback=_camera_guidance(context.creative_format),
    )
    focal_area = _without_deterministic_copy(
        concept.focal_area,
        strategy,
        fallback="one off-center focal subject",
    )
    text_zone = _without_deterministic_copy(
        concept.text_zone,
        strategy,
        fallback="a protected low-detail copy corridor",
    )
    background_complexity = _without_deterministic_copy(
        concept.background_complexity,
        strategy,
        fallback="controlled detail outside the reserved copy corridor",
    )
    inspiration = "; ".join(concept.inspiration_principles) or (
        "clear hierarchy; one focal subject; protected negative space"
    )
    inspiration = _without_deterministic_copy(
        inspiration[:600],
        strategy,
        fallback="clear hierarchy; one focal subject; protected negative space",
    )
    lighting = _without_deterministic_copy(
        concept.lighting,
        strategy,
        fallback="controlled professional commercial lighting",
    )
    mood = _without_deterministic_copy(
        concept.mood,
        strategy,
        fallback="confident and professionally art-directed",
    )
    correction_text = f"Correction for this attempt: {correction}\n" if correction else ""
    body = (
        "Create only the raw background / hero visual for a professional marketing creative.\n"
        f"Campaign objective: {context.campaign_objective}.\n"
        f"Campaign angle: {campaign_angle}\n"
        f"Platform and format: {context.channel}, {context.creative_format}, aspect ratio {aspect_ratio}.\n"
        f"Selected original concept: {concept.concept_name}.\n"
        f"Strategic visual premise: {strategic_reason}\n"
        f"Hero subject: {hero_subject}\n"
        f"Environment and visual concept: {visual_concept}\n"
        f"Visual metaphor: {visual_metaphor}\n"
        f"Commercial image style: {image_style}\n"
        f"Camera framing: {camera}\n"
        "Perspective: believable commercial perspective with one immediate focal read.\n"
        f"Depth: {concept.depth}\n"
        f"Lighting: {lighting}\n"
        "Materials: credible, tactile, high-detail surfaces appropriate to the subject.\n"
        f"Atmosphere and mood: {mood}\n"
        f"Color relationship: use {colors} selectively with controlled contrast; do not force every color.\n"
        f"Subject position and focal point: {focal_area}\n"
        f"Reserved overlay zone: {text_zone}\n"
        f"Background complexity: {background_complexity}\n"
        f"Composition density: {concept.visual_density}\n"
        f"Abstract inspiration principles to synthesize: {inspiration}\n"
        "Originality: combine the principles into a new brand-specific visual; do not imitate or reproduce any source design.\n"
        "Premium quality: art-directed commercial finish, coherent lighting, clean edges, intentional balance, no generic template aesthetic.\n"
    )
    critical_constraints = (
        correction_text
        +
        "DO NOT GENERATE words, letters, numbers, typography, logos, fake brand marks, watermarks, interface text, offer copy, CTA text, fake product labels, invented packaging, duplicate discount symbols, or visual clutter inside the reserved overlay zone.\n"
        "Do not turn an offer or discount into a literal numeric graphic. The application adds all exact marketing text and the real logo afterward."
    )
    maximum_body = 5000 - len(critical_constraints) - 1
    return f"{body[:maximum_body].rstrip()}\n{critical_constraints}"


def _build_pattern_proposal(
    *,
    pattern: _DesignPattern,
    strategy: CreativeStrategyProposal,
    research: CreativeResearchBundle,
    context: PublicCreativeResearchContext,
) -> CreativeConceptProposal:
    principles = _selected_principles(research, pattern)
    return CreativeConceptProposal(
        concept_name=pattern.name,
        strategic_reason=(
            f"Translate a {context.industry} {context.campaign_objective} into a "
            f"{context.style_family} "
            "commercial idea led by the supported offering rather than literal promotional typography."
        ),
        hero_subject=strategy.subject_focus,
        visual_metaphor=pattern.visual_metaphor,
        layout_intent=f"{pattern.layout_intent}; {_platform_layout(context.channel)}",
        focal_area=pattern.focal_area,
        text_zone=pattern.text_zone,
        offer_treatment=(
            pattern.offer_treatment
            if strategy.offer
            else "no offer element unless exact authorized offer copy exists"
        ),
        cta_treatment=(
            pattern.cta_treatment
            if strategy.cta
            else "no CTA element when exact CTA copy is unavailable"
        ),
        depth=pattern.depth,
        image_style=pattern.image_style,
        camera_direction=_camera_guidance(context.creative_format),
        lighting=strategy.lighting,
        mood=strategy.mood,
        visual_density=pattern.density,
        background_complexity=pattern.background_complexity,
        brand_expression=(
            f"{strategy.brand_treatment} Use brand cues selectively and reserve the real logo for deterministic composition."
        )[:300],
        inspiration_principles=principles,
        avoid_patterns=tuple(
            dict.fromkeys(
                (
                    *research.avoid_patterns,
                    "source-design imitation",
                    "literal promotional typography in the raw visual",
                )
            )
        )[:6],
        originality_notes=(
            "This direction combines internal patterns with several abstract research signals; it must not reproduce any source layout, artwork, copy, or identity."
        ),
    )


def _score_candidates(
    proposals: tuple[CreativeConceptProposal, ...],
    *,
    strategy: CreativeStrategyProposal,
    research: CreativeResearchBundle,
    context: PublicCreativeResearchContext,
) -> tuple[CreativeConceptScorecard, ...]:
    signatures = tuple(_concept_signature(proposal) for proposal in proposals)
    research_tokens = _tokens(
        " ".join(
            (
                *research.dominant_patterns,
                *research.emerging_patterns,
                *research.recommended_visual_directions,
            )
        )
    )
    strategy_tokens = _tokens(
        " ".join(
            (
                strategy.campaign_angle,
                strategy.visual_concept,
                strategy.subject_focus,
                strategy.brand_treatment,
                strategy.target_audience,
                strategy.audience_insight,
            )
        )
    )
    results: list[CreativeConceptScorecard] = []
    for index, proposal in enumerate(proposals):
        proposal_text = " ".join(
            str(value)
            for key, value in proposal.model_dump().items()
            if key not in {"avoid_patterns", "originality_notes"}
        )
        proposal_tokens = _tokens(proposal_text)
        other_signatures = signatures[:index] + signatures[index + 1 :]
        average_distance = sum(
            1.0 - _jaccard_similarity(signatures[index], other)
            for other in other_signatures
        ) / max(1, len(other_signatures))
        distinctiveness = _bounded_score(42 + average_distance * 48)

        industry_fit = _dimension_relevance(
            proposal_tokens,
            _tokens(context.industry),
        )
        objective_fit = _dimension_relevance(
            proposal_tokens,
            _tokens(context.campaign_objective),
        )
        channel_fit = _dimension_relevance(
            _tokens(f"{proposal.layout_intent} {proposal.camera_direction}"),
            _tokens(f"{context.channel} {_platform_layout(context.channel)}"),
        )
        format_fit = _dimension_relevance(
            _tokens(f"{proposal.layout_intent} {proposal.camera_direction} {proposal.text_zone}"),
            _tokens(f"{context.creative_format} {_camera_guidance(context.creative_format)}"),
        )
        grounding_overlap = _overlap_ratio(proposal_tokens, strategy_tokens)
        research_overlap = _overlap_ratio(proposal_tokens, research_tokens)
        specificity = min(1.0, len(proposal_tokens) / 85)
        quiet_feasibility = _contains_any(
            proposal.text_zone,
            ("quiet", "clear", "open", "protected", "uncluttered", "low-detail"),
        )
        manageable_density = _contains_any(
            proposal.visual_density,
            ("low", "medium", "restrained", "controlled"),
        )
        compact_offer = _contains_any(
            proposal.offer_treatment,
            ("small", "compact", "badge", "chip", "annotation", "support", "lockup"),
        )
        offer_dominates = _contains_any(
            proposal.offer_treatment,
            ("giant", "dominant", "full-canvas", "hero typography"),
        )
        actionable_cta = _contains_any(
            proposal.cta_treatment,
            ("contrast", "filled", "dark", "light", "accent", "outlined", "compact"),
        )
        brand_overlap = _overlap_ratio(
            _tokens(proposal.brand_expression),
            _tokens(strategy.brand_treatment),
        )
        brand_controls = _contains_any(
            proposal.brand_expression,
            ("brand", "palette", "logo", "identity", "color"),
        )
        safety_controls = bool(proposal.avoid_patterns) and not (
            _DIRECT_COPY_LANGUAGE.search(proposal_text)
            or _URL_LANGUAGE.search(proposal_text)
        )

        dimensions = {
            "brand_fit": _bounded_score(
                48 + brand_overlap * 32 + (12 if brand_controls else 0)
            ),
            "marketing_strength": _bounded_score(
                45 + objective_fit * 0.28 + grounding_overlap * 22
                + (8 if compact_offer and strategy.offer else 0)
            ),
            "distinctiveness": distinctiveness,
            "visual_sophistication": _bounded_score(
                48 + specificity * 22
                + (10 if proposal.camera_direction and proposal.lighting else 0)
                + (8 if manageable_density else 0)
            ),
            "audience_relevance": _bounded_score(
                44 + grounding_overlap * 34 + industry_fit * 0.18
            ),
            "platform_suitability": _bounded_score(
                35 + channel_fit * 0.30 + format_fit * 0.35
            ),
            "offer_clarity": (
                _bounded_score(55 + (25 if compact_offer else 0) - (35 if offer_dominates else 0))
                if strategy.offer
                else 75
            ),
            "cta_clarity": (
                _bounded_score(55 + (25 if actionable_cta else 0))
                if strategy.cta
                else 75
            ),
            "composition_feasibility": _bounded_score(
                46 + (22 if quiet_feasibility else 0)
                + (18 if manageable_density else 0)
            ),
            "originality": _bounded_score(
                distinctiveness * 0.65
                + (research_overlap * 20 if research_tokens else 8)
                + 12
            ),
            "pr_safety": 78 if safety_controls else 55,
            "business_brain_grounding": _bounded_score(
                42 + grounding_overlap * 48
            ),
        }
        overall = round(sum(dimensions.values()) / len(dimensions))
        results.append(
            CreativeConceptScorecard(**dimensions, overall_score=overall)
        )
    return tuple(results)


def _pattern_fit(
    pattern: _DesignPattern,
    context: PublicCreativeResearchContext,
) -> int:
    return (
        (8 if context.industry in pattern.industries else 0)
        + (7 if context.campaign_objective in pattern.objectives else 0)
        + (6 if context.creative_format in pattern.formats else 0)
        + (5 if context.style_family in pattern.styles else 0)
    )


def _ranked_patterns(
    context: PublicCreativeResearchContext,
) -> tuple[_DesignPattern, ...]:
    return tuple(
        sorted(
            _PATTERNS,
            key=lambda pattern: (-_pattern_fit(pattern, context), pattern.key),
        )[:3]
    )


def _concept_signature(
    proposal: CreativeConceptProposal,
) -> frozenset[str]:
    return _tokens(
        " ".join(
            (
                proposal.concept_name,
                proposal.visual_metaphor,
                proposal.layout_intent,
                proposal.image_style,
                proposal.camera_direction,
                proposal.focal_area,
            )
        )
    )


def _tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in _TOKEN.findall(value.casefold())
        if len(token) > 2 and token not in _STOPWORDS
    )


def _jaccard_similarity(
    first: frozenset[str],
    second: frozenset[str],
) -> float:
    if not first and not second:
        return 1.0
    return len(first & second) / max(1, len(first | second))


def _overlap_ratio(
    candidate: frozenset[str],
    reference: frozenset[str],
) -> float:
    if not candidate or not reference:
        return 0.0
    return len(candidate & reference) / max(1, min(len(candidate), len(reference)))


def _dimension_relevance(
    candidate: frozenset[str],
    reference: frozenset[str],
) -> float:
    overlap = _overlap_ratio(candidate, reference)
    return min(100.0, 35.0 + overlap * 65.0) if overlap else 25.0


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    normalized = value.casefold()
    return any(marker in normalized for marker in markers)


def _bounded_score(value: float) -> int:
    return max(0, min(100, round(value)))


def _selected_principles(
    research: CreativeResearchBundle,
    pattern: _DesignPattern,
) -> tuple[str, ...]:
    values = [
        *research.recommended_visual_directions,
        pattern.layout_intent,
        pattern.background_complexity,
    ]
    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split())[:180]
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        selected.append(normalized)
        if len(selected) == 6:
            break
    return tuple(selected)


def _platform_layout(channel: str) -> str:
    return {
        "instagram": "optimize for a visual-first social read and immediate hierarchy",
        "facebook": "optimize for feed readability and clear promotional flow",
        "linkedin": "use an editorial professional rhythm with restrained effects",
        "tiktok": "use vertical mobile energy and protect interface-safe margins",
    }.get(channel, "use a balanced commercial layout for the requested format")


def _camera_guidance(creative_format: str) -> str:
    return {
        "story vertical": "vertical medium-to-wide framing with top and bottom interface-safe space",
        "landscape ad": "wide horizontal framing with directional flow into the protected copy area",
        "display banner": "wide, simple silhouette with an immediate read at small display size",
    }.get(creative_format, "square-friendly medium framing with a strong off-center hero")


def _without_deterministic_copy(
    value: str,
    strategy: CreativeStrategyProposal,
    *,
    fallback: str,
) -> str:
    sanitized = value
    replacements = (
        (strategy.supporting_message, "supported business value"),
        (strategy.headline, "the campaign idea"),
        (strategy.offer or "", "the authorized offer"),
        (strategy.cta or "", "a later call to action"),
    )
    for exact, replacement in replacements:
        if not exact.strip():
            continue
        sanitized = re.sub(
            re.escape(exact.strip()),
            replacement,
            sanitized,
            flags=re.IGNORECASE,
        )
    sanitized = " ".join(sanitized.split()).strip(" -,:;.")
    return sanitized or fallback
