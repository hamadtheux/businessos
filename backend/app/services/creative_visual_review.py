from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.provider import AIAgentProviderMetadata


VisualRepairClass = Literal["none", "layout", "raw_visual"]
VisualHardFailure = Literal[
    "accidental_generated_text",
    "duplicated_message",
    "excessive_whitespace",
    "overcrowding",
    "irrelevant_visual",
    "irrelevant_decorative_art",
    "meaningless_focal_story",
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
    unnatural_headline_wrapping: bool
    generic_template_output: bool
    weak_brand_cta: bool
    excessive_dead_panel_space: bool
    hard_failures: tuple[VisualHardFailure, ...] = Field(
        default_factory=tuple,
        max_length=12,
    )
    approved: bool
    repair_class: VisualRepairClass
    repair_instructions: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_decision(self) -> CreativeVisualReview:
        raw_failure = (
            self.accidental_generated_text
            or self.duplicated_message
            or self.irrelevant_visual
            or self.irrelevant_decorative_art
            or self.meaningless_focal_story
        )
        layout_failure = (
            self.excessive_whitespace
            or self.overcrowding
            or self.unnatural_headline_wrapping
            or self.generic_template_output
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


def build_visual_review_task(request: CreativeVisualReviewRequest) -> str:
    """Create the bounded text paired with the transient final-image input."""
    return (
        "Review the attached final marketing creative as a strict senior visual "
        "quality critic. Judge what is visibly present, not hidden reasoning.\n"
        f"Campaign objective: {request.campaign_objective}\n"
        f"Channel: {request.channel}\n"
        f"Selected concept: {request.concept_name}\n"
        f"Expected hero and product story: {request.concept_expectations}\n"
        f"Expected exact headline: {request.expected_headline}\n"
        f"Expected exact offer: {request.expected_offer or '[none]'}\n"
        f"Expected exact CTA: {request.expected_cta or '[none]'}\n"
        f"Brand expectations: {request.brand_expectations}\n"
        f"Approval floor: {request.quality_threshold}/100.\n"
        "Score hierarchy, composition, brand consistency, logo/identity quality, "
        "readability, CTA and offer clarity, focal and product/category relevance, "
        "originality, polish, scroll-stopping strength, message coherence, whitespace "
        "balance, typography, sophistication, and campaign alignment. Treat 68 as the "
        "minimum acceptable score for any individual dimension and reject generic-template "
        "risk above 48. The overall semantic quality must also meet the supplied approval "
        "floor.\n"
        "Reject as raw_visual when generated background artwork contains any "
        "accidental words, fake letters, a second copy of the headline/offer/CTA "
        "(including a giant duplicate such as 50% OFF), a generic abstract SaaS "
        "background with no product/campaign relevance, irrelevant decorative art, "
        "or no meaningful focal story, "
        "or an artifact that cannot be repaired by rearranging deterministic layers. "
        "Reject as layout when the supplied artwork is usable but hierarchy, balance, "
        "spacing, crowding, excessive dead panel space, awkward short-headline wrapping, "
        "weak/non-brand CTA treatment, disconnected identity, or template-like geometry "
        "requires another local composition. Reject technically valid but aesthetically "
        "mediocre output. Approve only a polished, readable, original, brand-consistent "
        "final with a commercially meaningful visual story. Keep repair "
        "instructions short and do not reveal chain-of-thought."
    )


def _bounded(value: str, name: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Visual review {name} is invalid")
