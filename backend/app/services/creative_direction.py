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

from app.schemas.ai_agent import MAX_AGENT_TASK_LENGTH
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


from app.services.creative_world_class import (
    CreativeStoryMode,
    creative_story_evidence_tokens,
    assess_world_class_creative,
)
from app.services.creative_authority import AuthoritativeCreativeContext


class CreativeConceptScorecard(DirectionSchema):
    brand_fit: int = Field(ge=0, le=100)
    business_specific_relevance: int = Field(ge=0, le=100)
    marketing_strength: int = Field(ge=0, le=100)
    marketing_idea_strength: int = Field(ge=0, le=100)
    distinctiveness: int = Field(ge=0, le=100)
    visual_sophistication: int = Field(ge=0, le=100)
    commercial_sophistication: int = Field(ge=0, le=100)
    audience_relevance: int = Field(ge=0, le=100)
    product_relevance: int = Field(ge=0, le=100)
    visual_storytelling: int = Field(ge=0, le=100)
    scroll_stopping_potential: int = Field(ge=0, le=100)
    platform_suitability: int = Field(ge=0, le=100)
    offer_clarity: int = Field(ge=0, le=100)
    cta_clarity: int = Field(ge=0, le=100)
    composition_feasibility: int = Field(ge=0, le=100)
    originality: int = Field(ge=0, le=100)
    pr_safety: int = Field(ge=0, le=100)
    business_brain_grounding: int = Field(ge=0, le=100)
    genericness_risk: int = Field(ge=0, le=100)
    replaceable_brand_risk: int = Field(ge=0, le=100)
    overall_score: int = Field(ge=0, le=100)


class CreativeConceptProposal(DirectionSchema):
    """One safe Creative Director proposal before server-owned scoring."""

    concept_name: str = Field(min_length=1, max_length=100)
    marketing_idea: str = Field(min_length=1, max_length=300)
    customer_care_reason: str = Field(min_length=1, max_length=300)
    strategic_reason: str = Field(min_length=1, max_length=300)
    hero_subject: str = Field(min_length=1, max_length=500)
    hero_relevance: str = Field(min_length=1, max_length=300)
    product_story: str = Field(min_length=1, max_length=400)
    scroll_stopping_hook: str = Field(min_length=1, max_length=300)
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
        "marketing_idea",
        "customer_care_reason",
        "strategic_reason",
        "hero_subject",
        "hero_relevance",
        "product_story",
        "scroll_stopping_hook",
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


_BRAND_OFFER_CONCRETE_ARTIFACT = re.compile(
    r"\b(?:app|application|dashboard|user interface|ui|mock screen|software screen|"
    r"package|packaging)\b|"
    r"\b(?:product|service|offering)[ -](?:environment|first|led|specific)\b",
    re.IGNORECASE,
)
_BRAND_OFFER_INVENTED_ARTIFACT = re.compile(
    r"\b(?:fake|fabricated|fictional|imaginary|imagined|invented|made[ -]up|mock)\b"
    r".{0,80}\b(?:app|application|dashboard|interface|ui|feature|product|service|"
    r"offering|package|packaging|system|workflow|integration|fulfillment)\b",
    re.IGNORECASE,
)
_BRAND_OFFER_OFFERING_DEPICTION = re.compile(
    r"(?:\b(?:show|depict|render|spotlight|demonstrate|represent|display)\b"
    r".{0,80}\b(?:product|service|offering|package)\b|"
    r"\bsupported\b.{0,80}\b(?:offering|product|service|package)\b|"
    r"\b(?:product|service|offering|package)\b.{0,80}"
    r"\b(?:hero|story|moment|in use|in action|doing work|delivering|automates?|"
    r"coordinates?|orchestrates?)\b)",
    re.IGNORECASE,
)
_BRAND_OFFER_AUTHORIZABLE_CAPABILITY = re.compile(
    r"\b(?:workflow|workflows|integration|integrations|system|systems|software|"
    r"platform|platforms|hub|hubs|feature|features|automation|automations|"
    r"fulfillment|predictive|forecast|forecasts|forecasting)\b",
    re.IGNORECASE,
)


_BRAND_OFFER_UNSUPPORTED_CLAIM = re.compile(
    r"\b(?:guarantees?|guaranteed|testimonials?|proven results)\b|"
    r"\b(?:saves?|saved|reduces?|reduced|increases?|increased|boosts?|boosted|"
    r"doubles?|doubled|triples?|tripled)\b.{0,45}"
    r"\b(?:revenue|profit|sales|conversion|conversions|hours|costs|income)\b|"
    r"\b(?:revenue|profit|sales|conversion|income)\b.{0,30}\d+\s*%",
    re.IGNORECASE,
)


def _brand_offer_text_has_unsupported_offering_story(
    value: str,
    *,
    authoritative_context: AuthoritativeCreativeContext | None = None,
) -> bool:
    """Detect unsupported depictions while allowing verified capabilities."""
    if any(
        pattern.search(value)
        for pattern in (
            _BRAND_OFFER_CONCRETE_ARTIFACT,
            _BRAND_OFFER_INVENTED_ARTIFACT,
            _BRAND_OFFER_OFFERING_DEPICTION,
            _BRAND_OFFER_UNSUPPORTED_CLAIM,
        )
    ):
        return True

    capability_tokens: set[str] = set()
    for capability in _BRAND_OFFER_AUTHORIZABLE_CAPABILITY.finditer(value):
        capability_tokens.update(
            creative_story_evidence_tokens(capability.group())
        )
    if not capability_tokens:
        return False

    return (
        authoritative_context is None
        or not authoritative_context.supports_evidence_tokens(capability_tokens)
    )


def _brand_offer_has_unsupported_offering_story(
    proposal: CreativeConceptProposal,
    *,
    authoritative_context: AuthoritativeCreativeContext | None = None,
) -> bool:
    """
    Fail closed on unsupported positive renderer semantics.

    Negative controls live only in avoid_patterns/originality_notes and are
    intentionally excluded. Free-form negation in a positive story field does
    not make an otherwise forbidden depiction safe.
    """
    positive_values = (
        proposal.concept_name,
        proposal.marketing_idea,
        proposal.customer_care_reason,
        proposal.strategic_reason,
        proposal.hero_subject,
        proposal.hero_relevance,
        proposal.product_story,
        proposal.scroll_stopping_hook,
        proposal.visual_metaphor,
        proposal.layout_intent,
        proposal.focal_area,
        proposal.image_style,
        proposal.depth,
        proposal.camera_direction,
        proposal.lighting,
        proposal.mood,
        proposal.visual_density,
        proposal.background_complexity,
        proposal.brand_expression,
        proposal.text_zone,
        proposal.offer_treatment,
        proposal.cta_treatment,
        *proposal.inspiration_principles,
    )
    return any(
        _brand_offer_text_has_unsupported_offering_story(
            value,
            authoritative_context=authoritative_context,
        )
        for value in positive_values
    )


def _brand_offer_safe_text(value: str, *, fallback: str) -> str:
    if _brand_offer_text_has_unsupported_offering_story(value):
        return fallback
    return value


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
        ideas = {
            " ".join(candidate.marketing_idea.casefold().split())
            for candidate in self.candidates
        }
        if len(names) != 3 or len(metaphors) != 3 or len(ideas) != 3:
            raise ValueError(
                "creative concepts must have distinct names, ideas, and metaphors"
            )
        signatures = tuple(_concept_signature(candidate) for candidate in self.candidates)
        for index, first in enumerate(signatures):
            for second in signatures[index + 1 :]:
                if _jaccard_similarity(first, second) > 0.72:
                    raise ValueError("creative concepts are not materially different")
        return self


class CreativeConceptCandidate(CreativeConceptProposal):
    scorecard: CreativeConceptScorecard


class CreativeDirectionPlan(DirectionSchema):
    """A scored direction, or a selected-only direction reconstructed on recovery."""

    candidates: tuple[CreativeConceptCandidate, ...] = Field(
        min_length=1,
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


class CreativeDirectionCheckpoint(DirectionSchema):
    """The bounded durable identity of the direction used by an image attempt."""

    checkpoint_version: Literal[1] = 1
    generation_epoch: int = Field(ge=1)
    image_attempt: int = Field(ge=1, le=2)
    selected_concept: CreativeConceptProposal
    research_fingerprint: str = Field(min_length=16, max_length=64)
    used_live_research: bool
    used_ai_synthesis: bool = False


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
        visual_metaphor="Business decisions and automated workflows converging through a credible orchestration workspace",
        layout_intent="Product-led technology environment with a strong operational story, asymmetric anchor, and modular depth",
        focal_area="recognizable orchestration workspace or workflow moment in the center-right field",
        text_zone="uncluttered left-side plane",
        image_style="premium product-environment visualization with UI-free functional zones, no decorative abstract rings",
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
    story_mode: CreativeStoryMode = "offering_proof",
    authoritative_context: AuthoritativeCreativeContext | None = None,
) -> CreativeDirectionPlan:
    if story_mode not in {"offering_proof", "brand_offer"}:
        raise ValueError("Creative story mode is invalid")
    proposals = (
        synthesis.candidates
        if synthesis is not None
        else tuple(
            _build_pattern_proposal(
                pattern=pattern,
                strategy=strategy,
                research=research,
                context=context,
                story_mode=story_mode,
            )
            for pattern in _ranked_patterns(context, story_mode=story_mode)
        )
    )
    scorecards = _score_candidates(
        proposals,
        strategy=strategy,
        research=research,
        context=context,
        story_mode=story_mode,
        authoritative_context=authoritative_context,
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


def revalidate_selected_creative_concept(
    *,
    selected_concept: CreativeConceptProposal,
    strategy: CreativeStrategyProposal,
    research: CreativeResearchBundle,
    context: PublicCreativeResearchContext,
    story_mode: CreativeStoryMode = "offering_proof",
    authoritative_context: AuthoritativeCreativeContext | None = None,
    research_fingerprint: str | None = None,
    used_live_research: bool | None = None,
    used_ai_synthesis: bool = False,
) -> CreativeDirectionPlan:
    """Re-score one durable renderer-bound concept using current server rules.

    A recovered image attempt must not trust a persisted scorecard or rebuild a
    purchase decision from stale alternatives. The selected proposal is the
    durable identity; its score is rebuilt from current research and current
    authoritative tenant context.
    """
    scorecard = _score_candidates(
        (selected_concept,),
        strategy=strategy,
        research=research,
        context=context,
        story_mode=story_mode,
        authoritative_context=authoritative_context,
    )[0]
    candidate = CreativeConceptCandidate(
        **selected_concept.model_dump(),
        scorecard=scorecard,
    )
    return CreativeDirectionPlan(
        candidates=(candidate,),
        selected_concept=candidate,
        research_fingerprint=(
            research_fingerprint
            if research_fingerprint is not None
            else research.research_fingerprint
        ),
        used_live_research=(
            used_live_research
            if used_live_research is not None
            else not research.degraded and research.reference_count > 0
        ),
        used_ai_synthesis=used_ai_synthesis,
    )


def creative_directions_materially_differ(
    previous: CreativeDirectionPlan, revised: CreativeDirectionPlan,
) -> bool:
    old, new = previous.selected_concept, revised.selected_concept
    return all(
        _jaccard_similarity(
            creative_story_evidence_tokens(first), creative_story_evidence_tokens(second),
        ) <= 0.72
        for first, second in (
            (old.hero_subject, new.hero_subject),
            (old.product_story, new.product_story),
        )
    )


class CreativeDirectorTaskBudgetError(ValueError):
    """Safe task-construction diagnostics without retaining campaign content."""

    def __init__(self, *, task_length: int, mandatory_length: int) -> None:
        super().__init__("Creative Director task exceeds the safe task budget")
        self.task_length = task_length
        self.mandatory_length = mandatory_length
        self.max_task_length = MAX_AGENT_TASK_LENGTH


def build_creative_director_task(
    *,
    strategy: CreativeStrategyProposal,
    research: CreativeResearchBundle,
    context: PublicCreativeResearchContext,
    story_mode: CreativeStoryMode = "offering_proof",
    repair: bool = False,
) -> str:
    """
    Build one bounded Creative Director task without blind truncation.

    Authoritative campaign values and the final server-owned quality/safety
    contract are mandatory. Only abstract research and fallback design guidance
    may be shortened to satisfy the global 4,000-character task ceiling.
    """

    # Repair policy belongs exclusively to the runtime's bounded server_context.
    # Keep this task identical for initial and repair calls, including its facts
    # and mandatory safety policy. The repair flag remains caller-compatible.
    max_task_length = MAX_AGENT_TASK_LENGTH

    if story_mode not in {"offering_proof", "brand_offer"}:
        raise ValueError("Creative story mode is invalid")

    if story_mode == "offering_proof":
        representation_axis = "product/service representation"
        task_campaign_angle = strategy.campaign_angle
        task_visual_concept = strategy.visual_concept
        task_subject_focus = strategy.subject_focus
        story_prefix = (
            "Every concept must show a specific cause -> mechanism -> customer outcome. "
            "The selected campaign has an authoritative product/service offering, so "
            "the concept must visibly demonstrate that supported offering. "
        )
        story_output_rules = (
            "- product_story must show cause -> mechanism -> outcome.\n"
            "- The hero must visually prove the supported offering rather than merely "
            "provide attractive atmosphere.\n"
        )
    else:
        representation_axis = "subject representation"
        task_campaign_angle = _brand_offer_safe_text(
            strategy.campaign_angle,
            fallback=f"The grounded {context.campaign_objective} campaign.",
        )
        task_visual_concept = _brand_offer_safe_text(
            strategy.visual_concept,
            fallback=(
                "Use only grounded campaign, category, audience, and brand context; "
                "do not assume a concrete offering."
            ),
        )
        task_subject_focus = _brand_offer_safe_text(
            strategy.subject_focus,
            fallback=(
                "A grounded campaign tension, contrast, reveal, occasion, or "
                "brand-owned category moment."
            ),
        )
        story_prefix = (
            "Every concept must show a specific cause -> mechanism -> viewer consequence "
            "using only grounded campaign, audience, category, offer, and brand context. "
            "No campaign-selected catalog offering is authoritative: do not invent a "
            "product, service, package, app, application, interface, UI, feature, "
            "workflow, integration, fulfillment process, fulfillment path, customer "
            "fact, or unsupported outcome. "
        )
        story_output_rules = (
            "- product_story is a schema compatibility field: in this mode it must "
            "describe the grounded campaign mechanism and cause -> mechanism -> outcome "
            "without inventing a product or service.\n"
            "- The hero must visually prove the grounded campaign/brand idea rather "
            "than merely provide attractive atmosphere.\n"
        )

    pattern_lines = "\n".join(
        (
            f"- {pattern.name}: {pattern.visual_metaphor}; "
            f"{pattern.layout_intent}; {pattern.image_style}."
        )
        for pattern in _ranked_patterns(context, story_mode=story_mode)
    )

    research_principles = "; ".join(
        (
            *research.dominant_patterns,
            *research.emerging_patterns,
            *research.recommended_visual_directions,
        )[:12]
    ) or (
        "No live research available; rely on internal abstract pattern guidance."
    )

    avoid_patterns = "; ".join(
        (
            *research.avoid_patterns,
            *research.originality_constraints,
        )[:10]
    ) or "none"

    mandatory_prefix = (
        "Act as a senior advertising Creative Director. Produce exactly three "
        "materially different executable concepts. They must differ in hero idea, "
        f"metaphor, image-making approach, {representation_axis}, camera direction, "
        "and spatial rhythm—not merely color or crop. "
        f"{story_prefix}"
        "Reject generic premium "
        "desk/workspace photography, decorative abstraction, generic productivity "
        "metaphors, generic person-at-laptop scenes, replaceable-brand concepts, and "
        "generic AI/SaaS fantasy imagery unless it visibly demonstrates the supported "
        "business mechanism. Do not self-score.\n\n"

        "TRUSTED CAMPAIGN STRATEGY:\n"
        f"- Goal: {strategy.marketing_goal}\n"
        f"- Audience: {strategy.target_audience}\n"
        f"- Audience insight: {strategy.audience_insight}\n"
        f"- Campaign angle: {task_campaign_angle}\n"
        f"- Intended headline: {strategy.headline}\n"
        f"- Intended offer: {strategy.offer or 'none'}\n"
        f"- Intended CTA: {strategy.cta or 'none'}\n"
        f"- Strategy visual context: {task_visual_concept}\n"
        f"- Strategy subject context: {task_subject_focus}\n"
        f"- Brand treatment: {strategy.brand_treatment}\n\n"

        "PUBLIC-SAFE CAMPAIGN DIMENSIONS:\n"
        f"- Industry: {context.industry}\n"
        f"- Objective: {context.campaign_objective}\n"
        f"- Channel: {context.channel}\n"
        f"- Format: {context.creative_format}\n"
        f"- Style family: {context.style_family}\n"
    )

    mandatory_suffix = (
        "\n\nOUTPUT RULES:\n"
        "- Return exactly three candidates through the required typed schema.\n"
        "- Ground every subject and factual implication in the trusted strategy.\n"
        "- marketing_idea must state the advertising mechanism.\n"
        "- customer_care_reason must explain the customer tension or consequence.\n"
        f"{story_output_rules}"
        "- Reject generic stock/lifestyle scenes even when visually premium.\n"
        "- Reject swap-logo concepts an unrelated business could use unchanged.\n"
        "- Reject decoration-only gradients, rings, circles, waves, blobs, or "
        "geometry as the central campaign idea.\n"
        "- Treat an offer as a controlled supporting element, not the automatic hero.\n"
        "- Reserve a feasible quiet zone for exact deterministic copy and logo.\n"
        "- Use inspiration only as abstract rhythm, hierarchy, lighting, density, "
        "composition, and style.\n"
        "- Never copy, clone, replicate, duplicate, or imitate source artwork.\n"
        "- Never include URLs, evidence IDs, external actions, hidden reasoning, "
        "credentials, or chain-of-thought.\n"
        "- The raw image will contain no typography; describe visual direction, "
        "not final copy."
    )

    fixed_task = mandatory_prefix + mandatory_suffix

    if len(fixed_task) > max_task_length:
        # Never truncate authoritative campaign values or mandatory policy.
        raise CreativeDirectorTaskBudgetError(
            task_length=len(fixed_task), mandatory_length=len(fixed_task),
        )

    remaining = max_task_length - len(fixed_task)

    dynamic_sources: tuple[tuple[str, str, float], ...] = (
        (
            "\n\nABSTRACT RESEARCH SIGNALS ONLY:\n",
            research_principles,
            0.38,
        ),
        (
            "\nAvoid: ",
            avoid_patterns,
            0.20,
        ),
        (
            "\n\nINTERNAL FALLBACK GUIDANCE:\n",
            pattern_lines,
            0.42,
        ),
    )

    label_total = sum(
        len(label)
        for label, _value, _weight in dynamic_sources
    )

    dynamic = ""

    if remaining > label_total:
        content_budget = remaining - label_total
        allocations: list[int] = []
        unallocated = content_budget

        for index, (_label, _value, weight) in enumerate(dynamic_sources):
            if index == len(dynamic_sources) - 1:
                amount = unallocated
            else:
                amount = min(
                    unallocated,
                    max(0, int(content_budget * weight)),
                )

            allocations.append(amount)
            unallocated -= amount

        sections: list[str] = []

        for (
            label,
            value,
            _weight,
        ), allocation in zip(
            dynamic_sources,
            allocations,
            strict=True,
        ):
            # Research/pattern material is non-authoritative and may be bounded.
            normalized = " ".join(value.split())

            if len(normalized) > allocation:
                if allocation >= 2:
                    normalized = normalized[: allocation - 1].rstrip() + "…"
                else:
                    normalized = normalized[:allocation]

            sections.append(label + normalized)

        dynamic = "".join(sections)

    task = mandatory_prefix + dynamic + mandatory_suffix

    if len(task) > max_task_length:
        raise CreativeDirectorTaskBudgetError(
            task_length=len(task), mandatory_length=len(fixed_task),
        )

    required_contract_markers = (
        "Return exactly three candidates",
        "Reject generic stock/lifestyle scenes",
        "Reject swap-logo concepts",
        "Never copy, clone, replicate",
        "Never include URLs",
        "raw image will contain no typography",
    )

    if any(marker not in task for marker in required_contract_markers):
        raise ValueError(
            "Creative Director task lost a mandatory quality or safety contract"
        )

    authoritative_values = (
        strategy.marketing_goal,
        strategy.target_audience,
        strategy.audience_insight,
        task_campaign_angle,
        strategy.headline,
        strategy.offer,
        strategy.cta,
        task_visual_concept,
        task_subject_focus,
        strategy.brand_treatment,
    )

    for value in authoritative_values:
        if value and value not in task:
            raise ValueError(
                "Creative Director task lost authoritative campaign input"
            )

    return task

def build_visual_art_direction(
    *,
    strategy: CreativeStrategyProposal,
    direction: CreativeDirectionPlan,
    context: PublicCreativeResearchContext,
    aspect_ratio: str,
    primary_color: str | None,
    secondary_color: str | None,
    accent_color: str | None,
    story_mode: CreativeStoryMode = "offering_proof",
    correction: str | None = None,
    authoritative_context: AuthoritativeCreativeContext | None = None,
) -> str:
    if story_mode not in {"offering_proof", "brand_offer"}:
        raise ValueError("Creative story mode is invalid")
    if correction is not None and (
        not isinstance(correction, str)
        or not correction.strip()
        or len(correction) > 600
    ):
        raise ValueError("Creative correction is invalid")
    concept = direction.selected_concept
    if (
        story_mode == "brand_offer"
        and _brand_offer_has_unsupported_offering_story(
            concept,
            authoritative_context=authoritative_context,
        )
    ):
        raise ValueError("Brand-offer direction contains an unsupported offering story")

    if story_mode == "offering_proof":
        campaign_fallback = "A value-led campaign grounded in the supported offering."
        hero_fallback = "The supported business offering as the single hero subject."
        reason_fallback = "A grounded commercial premise led by the supported offering."
        metaphor_fallback = "A clear visual metaphor grounded in the supported offering."
        relevance_fallback = (
            "The hero directly represents the supported offering and campaign objective."
        )
        story_fallback = (
            "Show the supported offering creating a clear, credible business moment."
        )
        story_label = "Product or service story"
        mode_safety = ""
    else:
        campaign_fallback = "A campaign-led premise grounded in the supplied brand context."
        hero_fallback = "One grounded campaign-relevant hero subject."
        reason_fallback = "A grounded commercial premise led by the campaign idea."
        metaphor_fallback = "A clear visual metaphor grounded in the campaign idea."
        relevance_fallback = (
            "The hero directly supports the grounded campaign objective and brand idea."
        )
        story_fallback = (
            "Show one grounded campaign mechanism with a clear visual consequence."
        )
        story_label = "Grounded campaign mechanism"
        mode_safety = (
            "Do not invent a product, service, package, app, application, interface, "
            "UI, feature, workflow, integration, fulfillment process, fulfillment path, "
            "customer fact, or unsupported outcome. "
        )

    campaign_angle = _without_deterministic_copy(
        strategy.campaign_angle,
        strategy,
        fallback=campaign_fallback,
    )
    if story_mode == "brand_offer":
        campaign_angle = _brand_offer_safe_text(
            campaign_angle,
            fallback=campaign_fallback,
        )
    hero_subject = _without_deterministic_copy(
        concept.hero_subject,
        strategy,
        fallback=hero_fallback,
    )
    visual_concept = _without_deterministic_copy(
        strategy.visual_concept,
        strategy,
        fallback=(
            "A campaign-specific category scene with one grounded visual mechanism."
            if story_mode == "brand_offer"
            else "A premium commercial environment with one clear hero subject."
        ),
    )
    if story_mode == "brand_offer":
        visual_concept = _brand_offer_safe_text(
            visual_concept,
            fallback=(
                "A campaign-specific category scene with one grounded visual mechanism."
            ),
        )
    colors = ", ".join(
        color
        for color in (primary_color, secondary_color, accent_color)
        if color
    ) or "restrained brand-compatible neutrals"
    strategic_reason = _without_deterministic_copy(
        concept.strategic_reason,
        strategy,
        fallback=reason_fallback,
    )
    visual_metaphor = _without_deterministic_copy(
        concept.visual_metaphor,
        strategy,
        fallback=metaphor_fallback,
    )
    hero_relevance = _without_deterministic_copy(
        concept.hero_relevance,
        strategy,
        fallback=relevance_fallback,
    )
    product_story = _without_deterministic_copy(
        concept.product_story,
        strategy,
        fallback=story_fallback,
    )
    scroll_hook = _without_deterministic_copy(
        concept.scroll_stopping_hook,
        strategy,
        fallback="Use one instantly legible focal contrast and an unexpected but relevant perspective.",
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
        f"Why the hero is campaign-relevant: {hero_relevance}\n"
        f"{story_label}: {product_story}\n"
        f"Immediate visual hook: {scroll_hook}\n"
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
        f"{mode_safety}"
        "DO NOT GENERATE words, letters, numbers, typography, logos, fake brand marks, watermarks, interface text, offer copy, CTA text, fake product labels, invented packaging, duplicate discount symbols, or visual clutter inside the reserved overlay zone.\n"
        "Do not turn an offer or discount into a literal numeric graphic. The application adds all exact marketing text and the real logo afterward."
    )
    maximum_body = 5000 - len(critical_constraints) - 1
    task = f"{body[:maximum_body].rstrip()}\n{critical_constraints}"
    if len(task) > 5000:
        raise ValueError("Raw creative direction exceeds the safe prompt budget")
    return task


def _build_pattern_proposal(
    *,
    pattern: _DesignPattern,
    strategy: CreativeStrategyProposal,
    research: CreativeResearchBundle,
    context: PublicCreativeResearchContext,
    story_mode: CreativeStoryMode = "offering_proof",
) -> CreativeConceptProposal:
    if story_mode not in {"offering_proof", "brand_offer"}:
        raise ValueError("Creative story mode is invalid")

    principles = _selected_principles(research, pattern)

    if story_mode == "offering_proof":
        marketing_idea = (
            f"Make {strategy.subject_focus[:170].rstrip('.')} visibly demonstrate "
            f"the {context.campaign_objective} promise through {pattern.visual_metaphor.lower()}."
        )
        hero_subject = strategy.subject_focus
        hero_relevance = (
            f"This hero directly represents {strategy.subject_focus[:150].rstrip('.')} "
            "and makes it visible evidence of "
            f"the {context.campaign_objective} objective for this {context.industry} campaign."
        )
        care_reason = (
            f"The audience can immediately connect the supported offering to "
            f"{strategy.audience_insight[:210].rstrip('.')} instead of decoding decoration."
        )
        strategic_reason = (
            f"Translate a {context.industry} {context.campaign_objective} into a "
            f"{context.style_family} commercial idea led by the supported offering "
            "rather than literal promotional typography."
        )
        product_story = (
            f"Represent the supported offering in use or through a credible operational "
            f"transformation; connect it directly to {strategy.campaign_angle[:220].rstrip('.')}."
        )
    else:
        safe_insight = _brand_offer_safe_text(
            strategy.audience_insight,
            fallback=f"the grounded needs of {strategy.target_audience[:160].rstrip('.')}",
        )
        safe_angle = _brand_offer_safe_text(
            strategy.campaign_angle,
            fallback=f"the grounded {context.campaign_objective} campaign",
        )
        hero_subject = (
            f"A campaign-specific {context.industry} tension-and-reveal moment for "
            f"{strategy.target_audience[:180].rstrip('.')}"
        )
        marketing_idea = (
            f"Turn the grounded {context.campaign_objective} audience tension into "
            f"a distinctive contrast and reveal through {pattern.visual_metaphor.lower()}."
        )
        hero_relevance = (
            f"The tension and reveal connect {strategy.target_audience[:150].rstrip('.')} "
            f"to the {context.campaign_objective} objective using grounded "
            f"{context.industry} category context."
        )
        care_reason = (
            f"The audience can immediately connect the grounded campaign idea to "
            f"{safe_insight[:210].rstrip('.')} instead of decoding decoration."
        )
        strategic_reason = (
            f"Translate a {context.industry} {context.campaign_objective} into a "
            f"{context.style_family} campaign-specific commercial idea using only "
            "grounded campaign, audience, category, offer, and brand context."
        )
        product_story = (
            "Create a credible tension-to-reveal-to-consequence visual tied directly "
            f"to {safe_angle[:240].rstrip('.')}."
        )
        safe_principles = tuple(
            value
            for value in principles
            if not _brand_offer_text_has_unsupported_offering_story(value)
        )
        principles = safe_principles or (
            "campaign-specific hierarchy and contrast",
            "one grounded focal reveal with protected negative space",
        )
    return CreativeConceptProposal(
        concept_name=pattern.name,
        marketing_idea=marketing_idea[:300],
        customer_care_reason=care_reason[:300],
        strategic_reason=strategic_reason[:300],
        hero_subject=hero_subject[:500],
        hero_relevance=hero_relevance[:300],
        product_story=product_story[:400],
        scroll_stopping_hook=(
            f"Create one unmistakable {pattern.focal_area} with controlled contrast, "
            "depth, and a category-relevant moment instead of decorative abstraction."
        )[:300],
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


def _world_class_concept_assessment(
    *,
    proposal: CreativeConceptProposal,
    strategy: CreativeStrategyProposal,
    context: PublicCreativeResearchContext,
    story_mode: CreativeStoryMode = "offering_proof",
    authoritative_context: AuthoritativeCreativeContext | None = None,
):
    """
    Evaluate one renderer-bound concept against the centralized commercial policy.

    Strategy fields provide the campaign shape; capability authority is supplied
    separately as bounded Business Brain evidence. This function does not add new
    business claims, provider data, URLs, credentials, or private research evidence.
    """
    business_context = " | ".join(
        (
            context.industry,
            strategy.visual_concept,
            strategy.subject_focus,
            strategy.brand_treatment,
            strategy.audience_insight,
        )
        if story_mode == "offering_proof"
        else (
            context.industry,
            strategy.brand_treatment,
            strategy.target_audience,
            _brand_offer_safe_text(
                strategy.audience_insight,
                fallback=context.campaign_objective,
            ),
        )
    )

    return assess_world_class_creative(
        business_context=business_context,
        campaign_goal=strategy.marketing_goal,
        audience=strategy.target_audience,
        subject_focus=strategy.subject_focus,
        campaign_angle=strategy.campaign_angle,
        marketing_idea=proposal.marketing_idea,
        customer_care_reason=proposal.customer_care_reason,
        hero_subject=proposal.hero_subject,
        hero_relevance=proposal.hero_relevance,
        product_story=proposal.product_story,
        visual_metaphor=proposal.visual_metaphor,
        scroll_stopping_hook=proposal.scroll_stopping_hook,
        story_mode=story_mode,
        authoritative_evidence_segments=(
            authoritative_context.evidence_segments
            if authoritative_context is not None
            else ()
        ),
    )


def _world_class_policy_blocks_render(assessment) -> bool:
    """
    Hard-stop obvious agency-quality failures.

    We intentionally do not use `assessment.approved` as the only gate because the
    semantic final-image critic remains authoritative for nuanced visual quality.
    This deterministic layer exists to stop unmistakably generic concepts before
    money is spent on image generation.
    """
    blocking_failures = {
        "generic_lifestyle_stock_scene",
        "decorative_abstraction_as_story",
        "no_business_specific_mechanism",
        "no_product_service_mechanism",
        "no_customer_cause_effect",
        "replaceable_brand_idea",
        "weak_marketing_mechanism",
        "weak_visual_proof",
        "generic_productivity_metaphor",
    }

    return (
        bool(blocking_failures.intersection(assessment.hard_failures))
        or assessment.stock_lifestyle_risk >= 72
        or assessment.decorative_abstraction_risk >= 72
        or assessment.replaceable_brand_risk >= 72
        or (
            assessment.commercial_readiness < 42
            and assessment.visual_proof < 48
        )
        or (
            assessment.business_specificity < 42
            and assessment.product_service_mechanism < 48
        )
    )


def _score_candidates(
    proposals: tuple[CreativeConceptProposal, ...],
    *,
    strategy: CreativeStrategyProposal,
    research: CreativeResearchBundle,
    context: PublicCreativeResearchContext,
    story_mode: CreativeStoryMode = "offering_proof",
    authoritative_context: AuthoritativeCreativeContext | None = None,
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
    strategy_scoring_values = (
        (
            strategy.campaign_angle,
            strategy.visual_concept,
            strategy.subject_focus,
            strategy.brand_treatment,
            strategy.target_audience,
            strategy.audience_insight,
        )
        if story_mode == "offering_proof"
        else (
            context.industry,
            context.campaign_objective,
            strategy.marketing_goal,
            _brand_offer_safe_text(
                strategy.campaign_angle,
                fallback=context.campaign_objective,
            ),
            strategy.brand_treatment,
            strategy.target_audience,
            _brand_offer_safe_text(
                strategy.audience_insight,
                fallback=context.campaign_objective,
            ),
        )
    )
    strategy_tokens = _tokens(" ".join(strategy_scoring_values))
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
        product_overlap = _overlap_ratio(
            _tokens(
                f"{proposal.hero_subject} {proposal.hero_relevance} "
                f"{proposal.product_story} {proposal.visual_metaphor}"
            ),
            strategy_tokens,
        )
        product_story_text = (
            f"{proposal.marketing_idea} {proposal.hero_subject} "
            f"{proposal.hero_relevance} {proposal.product_story} "
            f"{proposal.customer_care_reason} {proposal.visual_metaphor}"
        )
        story_tokens = _tokens(product_story_text)
        unsupported_brand_story = (
            story_mode == "brand_offer"
            and _brand_offer_has_unsupported_offering_story(
                proposal,
                authoritative_context=authoritative_context,
            )
        )

        world_class = _world_class_concept_assessment(
            proposal=proposal,
            strategy=strategy,
            context=context,
            story_mode=story_mode,
            authoritative_context=authoritative_context,
        )

        genericness_risk = max(
            _genericness_risk(product_story_text, story_mode=story_mode),
            world_class.stock_lifestyle_risk,
            world_class.decorative_abstraction_risk,
        )

        replaceable_brand_risk = max(
            _replaceable_brand_risk(
                story_tokens=story_tokens,
                strategy_tokens=strategy_tokens,
                proposal=proposal,
                story_mode=story_mode,
            ),
            world_class.replaceable_brand_risk,
        )
        if story_mode == "offering_proof":
            story_markers = (
                "before and after",
                "connect",
                "coordinate",
                "demonstrate",
                "in use",
                "outcome",
                "problem",
                "solve",
                "transform",
                "workflow",
            )
            commercial_markers = (
                "commercial",
                "editorial",
                "environment",
                "photography",
                "product",
                "credible",
                "tactile",
            )
        else:
            story_markers = (
                "before and after",
                "campaign mechanism",
                "consequence",
                "contrast",
                "customer tension",
                "occasion",
                "reveal",
                "transition",
                "transform",
                "unexpected",
                "visual tension",
            )
            commercial_markers = (
                "brand",
                "campaign",
                "commercial",
                "contrast",
                "credible",
                "editorial",
                "occasion",
                "reveal",
                "tension",
            )
        story_mechanism = _contains_any(
            product_story_text,
            story_markers,
        )
        commercial_language = _contains_any(
            proposal_text,
            commercial_markers,
        )

        dimensions = {
            "brand_fit": _bounded_score(
                48 + brand_overlap * 32 + (12 if brand_controls else 0)
            ),
            "business_specific_relevance": _bounded_score(
                40 + grounding_overlap * 34 + product_overlap * 26
                - replaceable_brand_risk * 0.35
            ),
            "marketing_strength": _bounded_score(
                45 + objective_fit * 0.28 + grounding_overlap * 22
                + (8 if compact_offer and strategy.offer else 0)
            ),
            "marketing_idea_strength": _bounded_score(
                42 + product_overlap * 24 + grounding_overlap * 16
                + (14 if story_mechanism else 0)
                - genericness_risk * 0.28
            ),
            "distinctiveness": distinctiveness,
            "visual_sophistication": _bounded_score(
                48 + specificity * 22
                + (10 if proposal.camera_direction and proposal.lighting else 0)
                + (8 if manageable_density else 0)
            ),
            "commercial_sophistication": _bounded_score(
                48 + (18 if commercial_language else 0)
                + (12 if story_mechanism else 0)
                + specificity * 12
                - genericness_risk * 0.24
            ),
            "audience_relevance": _bounded_score(
                44 + grounding_overlap * 34 + industry_fit * 0.18
            ),
            "product_relevance": _bounded_score(
                38 + product_overlap * 44
                + (8 if len(_tokens(proposal.product_story)) >= 8 else 0)
                + (10 if story_mechanism else 0)
                - genericness_risk * 0.32
            ),
            "visual_storytelling": _bounded_score(
                40 + product_overlap * 24 + (20 if story_mechanism else 0)
                + min(12, len(story_tokens) / 8)
                - genericness_risk * 0.30
            ),
            "scroll_stopping_potential": _bounded_score(
                44 + distinctiveness * 0.24
                + (14 if proposal.scroll_stopping_hook else 0)
                - genericness_risk * 0.25
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
            "genericness_risk": genericness_risk,
            "replaceable_brand_risk": replaceable_brand_risk,
        }
        if story_mode == "brand_offer":
            # One commercial policy owns these dimensions. The older keyword
            # heuristics must neither reward boilerplate nor penalize a grounded
            # scene merely because it omits those labels.
            genericness_risk = max(
                world_class.stock_lifestyle_risk, world_class.decorative_abstraction_risk,
            )
            replaceable_brand_risk = world_class.replaceable_brand_risk
            dimensions.update(
                business_specific_relevance=world_class.business_specificity,
                marketing_idea_strength=world_class.marketing_idea_strength,
                commercial_sophistication=world_class.commercial_readiness,
                product_relevance=world_class.product_service_mechanism,
                visual_storytelling=world_class.visual_proof,
                genericness_risk=genericness_risk,
                replaceable_brand_risk=replaceable_brand_risk,
            )
        if unsupported_brand_story:
            # A disobedient Director cannot earn its way past a server-owned
            # no-invention contract with otherwise polished score dimensions.
            dimensions.update(
                {
                    "business_specific_relevance": 0,
                    "product_relevance": 0,
                    "visual_storytelling": 0,
                    "pr_safety": 0,
                    "genericness_risk": max(genericness_risk, 90),
                    "replaceable_brand_risk": max(replaceable_brand_risk, 90),
                }
            )
        positive_dimensions = {
            key: value
            for key, value in dimensions.items()
            if key not in {"genericness_risk", "replaceable_brand_risk"}
        }
        weights = {
            "business_specific_relevance": 1.7,
            "marketing_idea_strength": 1.5,
            "commercial_sophistication": 1.3,
            "product_relevance": 1.8,
            "visual_storytelling": 1.6,
            "scroll_stopping_potential": 1.2,
        }
        weight_total = sum(weights.get(key, 1.0) for key in positive_dimensions)
        overall = _bounded_score(
            sum(
                score * weights.get(key, 1.0)
                for key, score in positive_dimensions.items()
            )
            / weight_total
            - genericness_risk * 0.16
            - replaceable_brand_risk * 0.18
        )
        if unsupported_brand_story:
            overall = min(overall, 49)
        elif _world_class_policy_blocks_render(world_class):
            # A beautiful-but-generic concept must never win server selection.
            overall = min(overall, 49)
        elif genericness_risk >= 65 or replaceable_brand_risk >= 72:
            overall = min(overall, 58)
        results.append(
            CreativeConceptScorecard(**dimensions, overall_score=overall)
        )
    return tuple(results)


def creative_direction_meets_quality_floor(
    direction: CreativeDirectionPlan,
) -> bool:
    """Reject a polished-looking concept that could belong to any business."""
    score = direction.selected_concept.scorecard
    return (
        score.overall_score >= 60
        and score.business_specific_relevance >= 55
        and score.marketing_idea_strength >= 55
        and score.product_relevance >= 55
        and score.visual_storytelling >= 55
        and score.genericness_risk < 65
        and score.replaceable_brand_risk < 72
    )


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


_BRAND_OFFER_UNSAFE_PATTERN_KEYS = frozenset(
    {
        "saas_control_center",
        "product_spotlight",
        "immersive_story",
    }
)


def _ranked_patterns(
    context: PublicCreativeResearchContext,
    *,
    story_mode: CreativeStoryMode = "offering_proof",
) -> tuple[_DesignPattern, ...]:
    if story_mode not in {"offering_proof", "brand_offer"}:
        raise ValueError("Creative story mode is invalid")

    eligible_patterns = (
        _PATTERNS
        if story_mode == "offering_proof"
        else tuple(
            pattern
            for pattern in _PATTERNS
            if pattern.key not in _BRAND_OFFER_UNSAFE_PATTERN_KEYS
        )
    )

    ranked = tuple(
        sorted(
            eligible_patterns,
            key=lambda pattern: (-_pattern_fit(pattern, context), pattern.key),
        )[:3]
    )

    if len(ranked) != 3:
        raise ValueError("Creative story mode has insufficient safe design patterns")

    return ranked


def _concept_signature(
    proposal: CreativeConceptProposal,
) -> frozenset[str]:
    return _tokens(
        " ".join(
            (
                proposal.concept_name,
                proposal.marketing_idea,
                proposal.customer_care_reason,
                proposal.visual_metaphor,
                proposal.product_story,
                proposal.scroll_stopping_hook,
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
    value_tokens = tuple(_TOKEN.findall(value.casefold()))
    for marker in markers:
        marker_tokens = tuple(_TOKEN.findall(marker.casefold()))
        if not marker_tokens:
            continue
        marker_length = len(marker_tokens)
        if any(
            value_tokens[index : index + marker_length] == marker_tokens
            for index in range(len(value_tokens) - marker_length + 1)
        ):
            return True
    return False


def _bounded_score(value: float) -> int:
    return max(0, min(100, round(value)))


def _genericness_risk(
    value: str,
    *,
    story_mode: CreativeStoryMode = "offering_proof",
) -> int:
    if story_mode not in {"offering_proof", "brand_offer"}:
        raise ValueError("Creative story mode is invalid")
    decorative_markers = (
        "abstract",
        "circle",
        "circles",
        "concentric",
        "decorative",
        "geometric",
        "geometry",
        "gradient",
        "gradients",
        "orb",
        "orbs",
        "ring",
        "rings",
        "shape",
        "shapes",
        "swirl",
        "swirls",
        "wave",
        "waves",
    )
    meaningful_markers = (
        (
            "customer",
            "customers",
            "demonstrate",
            "demonstrates",
            "human",
            "humans",
            "in use",
            "operation",
            "operational",
            "operations",
            "outcome",
            "outcomes",
            "offering",
            "offerings",
            "product",
            "products",
            "service",
            "services",
            "transform",
            "transformation",
            "workflow",
            "workflows",
        )
        if story_mode == "offering_proof"
        else ()
    )
    decorative_count = sum(
        _contains_any(value, (marker,)) for marker in decorative_markers
    )
    meaningful_count = sum(
        _contains_any(value, (marker,)) for marker in meaningful_markers
    )
    return _bounded_score(24 + decorative_count * 15 - meaningful_count * 7)


def _replaceable_brand_risk(
    *,
    story_tokens: frozenset[str],
    strategy_tokens: frozenset[str],
    proposal: CreativeConceptProposal,
    story_mode: CreativeStoryMode = "offering_proof",
) -> int:
    if story_mode not in {"offering_proof", "brand_offer"}:
        raise ValueError("Creative story mode is invalid")
    overlap = _overlap_ratio(story_tokens, strategy_tokens)
    explicit_story_markers = (
        (
            "demonstrate",
            "demonstrates",
            "in use",
            "operation",
            "operational",
            "operations",
            "outcome",
            "outcomes",
            "service",
            "services",
            "transform",
            "transformation",
            "workflow",
            "workflows",
        )
        if story_mode == "offering_proof"
        else ()
    )
    explicit_story = _contains_any(
        f"{proposal.marketing_idea} {proposal.product_story} {proposal.hero_relevance}",
        explicit_story_markers,
    )
    return _bounded_score(
        82 - overlap * 58 - (14 if explicit_story else 0)
    )


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
