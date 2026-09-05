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
]


class CreativeVisualReview(BaseModel):
    """Provider-neutral, strictly bounded semantic review of one final PNG."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    hierarchy: int = Field(ge=0, le=100)
    composition: int = Field(ge=0, le=100)
    brand_consistency: int = Field(ge=0, le=100)
    readability: int = Field(ge=0, le=100)
    cta_clarity: int = Field(ge=0, le=100)
    offer_clarity: int = Field(ge=0, le=100)
    focal_relevance: int = Field(ge=0, le=100)
    visual_polish: int = Field(ge=0, le=100)
    generic_template_risk: int = Field(ge=0, le=100)
    accidental_generated_text: bool
    duplicated_message: bool
    excessive_whitespace: bool
    overcrowding: bool
    irrelevant_visual: bool
    hard_failures: tuple[VisualHardFailure, ...] = Field(
        default_factory=tuple,
        max_length=5,
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
        )
        layout_failure = self.excessive_whitespace or self.overcrowding
        expected_failures = {
            name
            for name, active in (
                ("accidental_generated_text", self.accidental_generated_text),
                ("duplicated_message", self.duplicated_message),
                ("excessive_whitespace", self.excessive_whitespace),
                ("overcrowding", self.overcrowding),
                ("irrelevant_visual", self.irrelevant_visual),
            )
            if active
        }
        if set(self.hard_failures) != expected_failures:
            raise ValueError("visual hard failures must match detected conditions")
        dimension_floor = min(
            self.hierarchy,
            self.composition,
            self.brand_consistency,
            self.readability,
            self.cta_clarity,
            self.offer_clarity,
            self.focal_relevance,
            self.visual_polish,
        )
        if self.approved:
            if raw_failure or layout_failure or self.repair_class != "none":
                raise ValueError("approved review cannot contain a repair condition")
            if dimension_floor < 60 or self.generic_template_risk > 70:
                raise ValueError("approved review does not meet semantic quality floor")
        else:
            if self.repair_class == "none":
                raise ValueError("rejected review must classify the repair boundary")
            if raw_failure and self.repair_class != "raw_visual":
                raise ValueError("raw visual failures require raw_visual repair")
            if not raw_failure and layout_failure and self.repair_class != "layout":
                raise ValueError("layout failures require layout repair")
        return self


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
        f"Expected exact headline: {request.expected_headline}\n"
        f"Expected exact offer: {request.expected_offer or '[none]'}\n"
        f"Expected exact CTA: {request.expected_cta or '[none]'}\n"
        f"Brand expectations: {request.brand_expectations}\n"
        f"Approval floor: {request.quality_threshold}/100.\n"
        "Reject as raw_visual when generated background artwork contains any "
        "accidental words, fake letters, a second copy of the headline/offer/CTA "
        "(including a giant duplicate such as 50% OFF), an irrelevant focal visual, "
        "or an artifact that cannot be repaired by rearranging deterministic layers. "
        "Reject as layout when the supplied artwork is usable but hierarchy, balance, "
        "spacing, crowding, or whitespace requires another local composition. "
        "Approve only a polished, readable, brand-consistent final. Keep repair "
        "instructions short and do not reveal chain-of-thought."
    )


def _bounded(value: str, name: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Visual review {name} is invalid")
