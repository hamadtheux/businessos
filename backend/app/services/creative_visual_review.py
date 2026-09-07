from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.provider import AIAgentProviderMetadata

from app.services.creative_world_class import (
    CreativeStoryMode,
    WORLD_CLASS_VISUAL_CRITIC_CONTRACT,
)


VisualRepairClass = Literal["none", "layout", "raw_visual"]
VisualHardFailure = Literal[
    "accidental_generated_text",
    "duplicated_message",
    "excessive_whitespace",
    "overcrowding",
    "irrelevant_visual",
    "irrelevant_decorative_art",
    "meaningless_focal_story",
    "replaceable_brand_creative",
    "decorative_abstraction_dominates",
    "no_product_service_story",
    "commercially_weak",
    "unnatural_headline_wrapping",
    "generic_template_output",
    "weak_brand_cta",
    "excessive_dead_panel_space",
]

_SEMANTIC_VISUAL_QUALITY_FIELDS = (
    "hierarchy",
    "composition",
    "brand_consistency",
    "logo_identity_quality",
    "readability",
    "cta_clarity",
    "offer_clarity",
    "focal_relevance",
    "product_relevance",
    "business_specific_relevance",
    "visual_storytelling",
    "commercial_sophistication",
    "originality",
    "scroll_stopping_strength",
    "message_coherence",
    "whitespace_balance",
    "typography_quality",
    "visual_sophistication",
    "campaign_alignment",
    "visual_polish",
)


class CreativeVisualReview(BaseModel):
    """Provider-neutral, strictly bounded semantic review of one final PNG."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    hierarchy: int = Field(ge=0, le=100)
    composition: int = Field(ge=0, le=100)
    brand_consistency: int = Field(ge=0, le=100)
    logo_identity_quality: int = Field(ge=0, le=100)
    readability: int = Field(ge=0, le=100)
    cta_clarity: int = Field(ge=0, le=100)
    offer_clarity: int = Field(ge=0, le=100)
    focal_relevance: int = Field(ge=0, le=100)
    product_relevance: int = Field(ge=0, le=100)
    business_specific_relevance: int = Field(ge=0, le=100)
    visual_storytelling: int = Field(ge=0, le=100)
    commercial_sophistication: int = Field(ge=0, le=100)
    originality: int = Field(ge=0, le=100)
    scroll_stopping_strength: int = Field(ge=0, le=100)
    message_coherence: int = Field(ge=0, le=100)
    whitespace_balance: int = Field(ge=0, le=100)
    typography_quality: int = Field(ge=0, le=100)
    visual_sophistication: int = Field(ge=0, le=100)
    campaign_alignment: int = Field(ge=0, le=100)
    visual_polish: int = Field(ge=0, le=100)
    generic_template_risk: int = Field(ge=0, le=100)
    accidental_generated_text: bool
    duplicated_message: bool
    excessive_whitespace: bool
    overcrowding: bool
    irrelevant_visual: bool
    irrelevant_decorative_art: bool
    meaningless_focal_story: bool
    replaceable_brand_creative: bool
    decorative_abstraction_dominates: bool
    no_product_service_story: bool
    commercially_weak: bool
    unnatural_headline_wrapping: bool
    generic_template_output: bool
    weak_brand_cta: bool
    excessive_dead_panel_space: bool
    hard_failures: tuple[VisualHardFailure, ...] = Field(
        default_factory=tuple,
        max_length=16,
    )
    approved: bool
    repair_class: VisualRepairClass
    repair_instructions: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_decision(self) -> CreativeVisualReview:
        raw_failure = (
            self.accidental_generated_text
            or self.irrelevant_visual
            or self.irrelevant_decorative_art
            or self.meaningless_focal_story
            or self.replaceable_brand_creative
            or self.decorative_abstraction_dominates
            or self.no_product_service_story
            or self.commercially_weak
            or self.generic_template_output
        )
        layout_failure = (
            self.duplicated_message
            or self.excessive_whitespace
            or self.overcrowding
            or self.unnatural_headline_wrapping
            or self.weak_brand_cta
            or self.excessive_dead_panel_space
        )
        expected_failures = {
            name
            for name, active in (
                ("accidental_generated_text", self.accidental_generated_text),
                ("duplicated_message", self.duplicated_message),
                ("excessive_whitespace", self.excessive_whitespace),
                ("overcrowding", self.overcrowding),
                ("irrelevant_visual", self.irrelevant_visual),
                ("irrelevant_decorative_art", self.irrelevant_decorative_art),
                ("meaningless_focal_story", self.meaningless_focal_story),
                ("replaceable_brand_creative", self.replaceable_brand_creative),
                ("decorative_abstraction_dominates", self.decorative_abstraction_dominates),
                ("no_product_service_story", self.no_product_service_story),
                ("commercially_weak", self.commercially_weak),
                ("unnatural_headline_wrapping", self.unnatural_headline_wrapping),
                ("generic_template_output", self.generic_template_output),
                ("weak_brand_cta", self.weak_brand_cta),
                ("excessive_dead_panel_space", self.excessive_dead_panel_space),
            )
            if active
        }
        if set(self.hard_failures) != expected_failures:
            raise ValueError("visual hard failures must match detected conditions")
        dimension_floor = min(
            self.hierarchy,
            self.composition,
            self.brand_consistency,
            self.logo_identity_quality,
            self.readability,
            self.cta_clarity,
            self.offer_clarity,
            self.focal_relevance,
            self.product_relevance,
            self.business_specific_relevance,
            self.visual_storytelling,
            self.commercial_sophistication,
            self.originality,
            self.scroll_stopping_strength,
            self.message_coherence,
            self.whitespace_balance,
            self.typography_quality,
            self.visual_sophistication,
            self.campaign_alignment,
            self.visual_polish,
        )
        if self.approved:
            if raw_failure or layout_failure or self.repair_class != "none":
                raise ValueError("approved review cannot contain a repair condition")
            if dimension_floor < 68 or self.generic_template_risk > 48:
                raise ValueError("approved review does not meet semantic quality floor")
        else:
            if self.repair_class == "none":
                raise ValueError("rejected review must classify the repair boundary")
            if raw_failure and self.repair_class != "raw_visual":
                raise ValueError("raw visual failures require raw_visual repair")
            if not raw_failure and layout_failure and self.repair_class != "layout":
                raise ValueError("layout failures require layout repair")
            relevance_failure = min(
                self.focal_relevance,
                self.product_relevance,
                self.business_specific_relevance,
                self.visual_storytelling,
                self.commercial_sophistication,
                self.campaign_alignment,
                self.message_coherence,
            ) < 60
            if not raw_failure and not layout_failure and relevance_failure and self.repair_class != "raw_visual":
                raise ValueError("semantic relevance failures require raw_visual repair")
        return self


def semantic_visual_quality_score(review: CreativeVisualReview) -> int:
    """Return the auditable, equally weighted semantic-dimension mean."""
    return round(
        sum(
            getattr(review, field_name)
            for field_name in _SEMANTIC_VISUAL_QUALITY_FIELDS
        )
        / len(_SEMANTIC_VISUAL_QUALITY_FIELDS)
    )


def semantic_visual_review_meets_threshold(
    review: CreativeVisualReview,
    *,
    threshold: int,
) -> bool:
    """Enforce the server-owned overall floor after typed schema validation."""
    if not 60 <= threshold <= 95:
        raise ValueError("Visual review quality threshold is invalid")
    return review.approved and semantic_visual_quality_score(review) >= threshold


@dataclass(frozen=True, slots=True)
class CreativeVisualReviewRequest:
    """
    Transient review input.

    Only the rendered PNG and the minimum delivery expectations cross this
    boundary. CRM records, Business Brain context, research URLs, raw provider
    responses, source imagery, and credentials are deliberately unrepresentable.
    """

    final_png: bytes
    campaign_objective: str
    channel: str
    concept_name: str
    concept_expectations: str
    expected_headline: str
    expected_offer: str | None
    expected_cta: str | None
    brand_expectations: str
    quality_threshold: int
    review_mode: CreativeStoryMode

    def __post_init__(self) -> None:
        if (
            not self.final_png.startswith(b"\x89PNG\r\n\x1a\n")
            or len(self.final_png) > 30 * 1024 * 1024
        ):
            raise ValueError("Visual review PNG size is invalid")
        for name, value, limit in (
            ("campaign objective", self.campaign_objective, 120),
            ("channel", self.channel, 40),
            ("concept name", self.concept_name, 100),
            ("concept expectations", self.concept_expectations, 600),
            ("expected headline", self.expected_headline, 180),
            ("brand expectations", self.brand_expectations, 400),
        ):
            _bounded(value, name, limit)
        if self.expected_offer is not None:
            _bounded(self.expected_offer, "expected offer", 160)
        if self.expected_cta is not None:
            _bounded(self.expected_cta, "expected CTA", 300)
        if self.review_mode not in {"offering_proof", "brand_offer"}:
            raise ValueError("Visual review mode is invalid")
        if not 60 <= self.quality_threshold <= 95:
            raise ValueError("Visual review quality threshold is invalid")


@dataclass(frozen=True, slots=True)
class CreativeVisualReviewResult:
    review: CreativeVisualReview
    metadata: AIAgentProviderMetadata


@runtime_checkable
class CreativeVisualReviewProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    async def review(
        self,
        request: CreativeVisualReviewRequest,
    ) -> CreativeVisualReviewResult: ...


class CreativeVisualReviewProviderError(RuntimeError):
    """Safe visual-review failure that never contains provider payloads."""


def validate_visual_review_for_mode(
    review: CreativeVisualReview,
    *,
    story_mode: CreativeStoryMode,
) -> CreativeVisualReview:
    """Reject a typed critic result that contradicts its server-owned mode."""
    if story_mode not in {"offering_proof", "brand_offer"}:
        raise ValueError("Creative story mode is invalid")
    if story_mode == "brand_offer" and review.no_product_service_story:
        raise CreativeVisualReviewProviderError(
            "Visual reviewer contradicted the active story mode"
        )
    return review


def build_visual_review_task(
    request: CreativeVisualReviewRequest,
) -> str:
    """
    Create the bounded commercial-review task paired with one transient final PNG.

    This remains a deliberately narrow privacy boundary. The critic receives only
    the final creative and bounded delivery expectations. It does not receive CRM
    records, private Business Brain documents, research URLs, credentials, source
    images, storage identifiers, or provider internals.
    """
    mode_contract = (
        (
            "REVIEW MODE: offering_proof. A tenant-scoped campaign selection proves "
            "a real catalog product/service is in scope. Require credible visible "
            "offering/category/use-case proof. "
        )
        if request.review_mode == "offering_proof"
        else (
            "REVIEW MODE: brand_offer. No campaign-selected catalog offering is "
            "authoritative. Do not require or invent product/service proof; "
            "no_product_service_story must be false. Score product_relevance as "
            "relevance to the grounded campaign, offer, category context, and brand. "
        )
    )

    world_class_contract = (
        WORLD_CLASS_VISUAL_CRITIC_CONTRACT.strip()
        if request.review_mode == "offering_proof"
        else (
            "WORLD-CLASS BRAND/OFFER REVIEW:\n\n"
            "Do not approve an image merely because it is clean, premium, realistic, "
            "or technically well generated. Reject generic stock/lifestyle scenes, "
            "swap-logo creative, generic productivity imagery, meaningless decorative "
            "abstraction, weak focal storytelling, disconnected brand treatment, or "
            "an image whose only campaign meaning comes from the overlaid copy. "
            "A world-class approval requires a distinctive campaign idea, intentional "
            "brand ownership, meaningful offer/campaign/category relevance, strong art "
            "direction, scroll-stopping visual logic, and commercial specificity. "
            "Do not require, imply, or invent a product/service mechanism when the "
            "campaign has no authoritative selected catalog offering."
        )
    )

    task = (
        "Review the attached final marketing creative as an exceptionally strict "
        "global-agency Creative Director and visual quality critic. Judge only what "
        "is visibly present in the supplied final PNG and the bounded expectations "
        "below. Do not reward an image merely because it is clean, expensive-looking, "
        "photorealistic, minimal, trendy, or technically well generated.\n\n"

        "CAMPAIGN EXPECTATIONS:\n"
        f"- Objective: {request.campaign_objective}\n"
        f"- Channel: {request.channel}\n"
        f"- Selected concept: {request.concept_name}\n"
        f"- Commercial story expected: {request.concept_expectations}\n"
        f"- Exact headline: {request.expected_headline}\n"
        f"- Exact offer: {request.expected_offer or '[none]'}\n"
        f"- Exact CTA: {request.expected_cta or '[none]'}\n"
        f"- Brand expectations: {request.brand_expectations}\n"
        f"- Runtime approval target: {request.quality_threshold}/100.\n"
        f"- {mode_contract}\n\n"

        "SCORING STANDARD:\n"
        "Score every typed semantic dimension independently. The existing server "
        "requires at least 68 on every important dimension and separately enforces "
        "the runtime aggregate threshold. Never inflate a weak dimension merely to "
        "make the candidate pass. Generic-template risk must remain at or below the "
        "server-owned allowable ceiling.\n\n"

        "FOUR NON-NEGOTIABLE REVIEW LAYERS:\n"
        "1. COMMERCIAL IDEA — Is there an actual advertising idea, mechanism, "
        "tension, demonstration, transformation, or customer consequence?\n"
        "2. BUSINESS PROOF — Does the visual support the active review mode: "
        "offering proof when an offering is selected, otherwise the grounded "
        "campaign/offer/category/brand intent?\n"
        "3. BRAND OWNERSHIP — Does this feel intentionally created for this business, "
        "rather than unrelated stock art with a logo and palette pasted on top?\n"
        "4. EXECUTION — Is hierarchy, typography, composition, identity, readability, "
        "spacing, focal control, sophistication, polish and channel fit genuinely "
        "professional?\n\n"

        f"{world_class_contract}\n\n"

        "MANDATORY FAILURE MAPPING:\n"

        "- GENERIC STOCK/LIFESTYLE SCENE: A premium desk, coffee mug, stacked books, "
        "glasses, plant, notebook, laptop, generic office, generic person-at-laptop, "
        "or aspirational lifestyle scene is NOT a business story merely because it "
        "looks polished. When those objects do not serve the active review mode, set "
        "generic_template_output=true, replaceable_brand_creative=true, and "
        "commercially_weak=true. Set no_product_service_story only in offering_proof "
        "mode when supported offering proof is actually missing. This is a raw_visual "
        "failure.\n"

        "- SWAP-LOGO FAILURE: Mentally replace the displayed identity with an "
        "unrelated bank, furniture company, productivity app, consultancy, or SaaS "
        "brand. If the visual still works substantially unchanged, set "
        "replaceable_brand_creative=true. Correct logo placement does NOT override "
        "this failure.\n"

        "- LOGO-PASTED-ON-STOCK FAILURE: A real tenant logo and correct tenant colors "
        "do not by themselves prove brand consistency. If the underlying scene has "
        "no business-specific ownership, score brand_consistency and "
        "business_specific_relevance below the passing floor and classify the "
        "appropriate raw_visual failures.\n"

        "- COPY-CARRIES-THE-STORY FAILURE: If the copy explains the value but the "
        "image itself provides no visual support for the active review mode, set "
        "commercially_weak=true. In offering_proof mode also set "
        "no_product_service_story=true when offering proof is absent. In brand_offer "
        "mode judge campaign/offer/category/brand support without inventing a product.\n"

        "- DECORATIVE-ABSTRACTION FAILURE: Gradients, waves, rings, concentric "
        "circles, glowing orbs, blobs, generic neural motifs, floating geometry, or "
        "decorative technology effects cannot substitute for an advertising idea. "
        "When they dominate without meaningful storytelling for the active review "
        "mode, set "
        "decorative_abstraction_dominates=true and commercially_weak=true.\n"

        "- GENERIC AI/SAAS FANTASY FAILURE: Do not automatically reward floating "
        "dashboards, holographic interfaces, glowing data panels, robots, brains, "
        "neural networks, or generic futuristic AI imagery. If those elements do "
        "not credibly demonstrate the supplied concept and active-mode story, treat "
        "them as "
        "generic template output or irrelevant visual storytelling.\n"

        "- VISUAL-PROOF FAILURE: In offering_proof mode, require understandable "
        "product/service cause-and-effect. In brand_offer mode, require a meaningful "
        "campaign/offer/category/brand visual idea without fabricating an offering. "
        "Score relevance, storytelling, commercial sophistication and alignment "
        "accordingly; beautiful execution cannot compensate for missing meaning.\n"

        "- BRAND COLOR FAILURE: Correct palette usage must feel integrated with the "
        "scene and composition. Simply tinting unrelated stock imagery with the "
        "tenant colors is not world-class branding.\n"

        "- CTA/LOGO/LAYOUT FAILURE: If the underlying commercial artwork is strong "
        "and only deterministic hierarchy, typography, spacing, CTA treatment, logo "
        "placement, composition, or duplicated deterministic messaging needs repair, "
        "classify as layout rather than raw_visual.\n"

        "- RAW-VISUAL FAILURE: Generic concept, irrelevant hero, replaceable-brand "
        "creative, meaningless focal story, commercial weakness, dominant decorative "
        "abstraction, or—only in offering_proof mode—missing offering proof requires "
        "raw_visual repair. Do not misclassify those problems as layout.\n\n"

        "APPROVAL TEST:\n"
        "Approve only if the final creative could credibly appear in the portfolio "
        "of an excellent global advertising/design agency AND it unmistakably serves "
        "this specific campaign. Technical correctness is necessary but never "
        "sufficient. A clean mediocre ad is a rejection. A polished generic ad is a "
        "rejection. A correct-logo generic ad is a rejection. A beautiful visual "
        "with no commercial mechanism is a rejection.\n\n"

        "Keep repair_instructions short, actionable and focused on the visible "
        "failure. Do not reveal hidden reasoning or chain-of-thought."
    )

    # This task contains bounded request fields plus fixed server-owned policy.
    # Keep a deterministic ceiling so future edits cannot grow the provider
    # boundary without review.
    if len(task) > 9000:
        raise ValueError("Visual review task exceeds the safe policy budget")

    return task

def _bounded(value: str, name: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Visual review {name} is invalid")
