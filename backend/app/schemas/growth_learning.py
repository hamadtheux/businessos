from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


GrowthExperimentType = Literal["campaign", "content"]
GrowthExperimentStatus = Literal[
    "draft", "ready", "running", "completed", "evaluated", "canceled"
]
GrowthMetric = Literal["ctr", "conversion_rate", "cpc", "cpa", "roas"]
GrowthAttribution = Literal["provider_attributed", "first_party_observed"]
GrowthResultClassification = Literal[
    "insufficient_evidence",
    "no_material_difference",
    "observed_directional_difference",
    "mixed_result",
]


class GrowthSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GrowthVariantCreate(GrowthSchema):
    variant_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    label: str = Field(min_length=1, max_length=120)
    is_control: bool = False
    campaign_id: UUID
    content_id: UUID | None = None


class GrowthExperimentCreate(GrowthSchema):
    name: str = Field(min_length=1, max_length=180)
    hypothesis: str = Field(min_length=1, max_length=2000)
    learning_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    experiment_type: GrowthExperimentType
    primary_metric: GrowthMetric
    attribution_classification: GrowthAttribution
    evaluation_window_days: int = Field(ge=1, le=90)
    minimum_sample_size: int = Field(ge=1, le=1_000_000_000)
    source_opportunity_id: UUID | None = None
    source_ai_action_id: UUID | None = None
    variants: list[GrowthVariantCreate] = Field(min_length=2, max_length=5)

    @model_validator(mode="after")
    def validate_variant_contract(self) -> "GrowthExperimentCreate":
        if sum(variant.is_control for variant in self.variants) != 1:
            raise ValueError("exactly one control variant is required")
        keys = [variant.variant_key for variant in self.variants]
        if len(keys) != len(set(keys)):
            raise ValueError("variant keys must be unique")
        references = [
            (variant.campaign_id, variant.content_id) for variant in self.variants
        ]
        if len(references) != len(set(references)):
            raise ValueError("variant references must be unique")
        if self.experiment_type == "campaign" and any(
            variant.content_id is not None for variant in self.variants
        ):
            raise ValueError("campaign experiments cannot reference content")
        if self.experiment_type == "content" and any(
            variant.content_id is None for variant in self.variants
        ):
            raise ValueError("content experiments require content references")
        return self


class GrowthExperimentUpdate(GrowthSchema):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    hypothesis: str | None = Field(default=None, min_length=1, max_length=2000)
    evaluation_window_days: int | None = Field(default=None, ge=1, le=90)
    minimum_sample_size: int | None = Field(
        default=None, ge=1, le=1_000_000_000
    )

    @model_validator(mode="after")
    def require_change(self) -> "GrowthExperimentUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one definition field is required")
        return self


class GrowthVariantResponse(GrowthSchema):
    id: UUID
    business_id: UUID
    experiment_id: UUID
    variant_key: str
    label: str
    is_control: bool
    campaign_id: UUID
    content_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class GrowthExperimentResultResponse(GrowthSchema):
    id: UUID
    business_id: UUID
    experiment_id: UUID
    classification: GrowthResultClassification
    primary_metric: GrowthMetric
    attribution_classification: GrowthAttribution
    currency: str
    control_value: Decimal | None
    directional_leader_value: Decimal | None
    absolute_difference: Decimal | None
    relative_difference: Decimal | None
    evidence_quality: Decimal
    directional_leader_variant_id: UUID | None
    directional_leader_key: str | None
    learning_memory_id: UUID | None
    measurement_start: AwareDatetime
    measurement_end: AwareDatetime
    evaluation_cutoff: AwareDatetime
    evaluated_at: AwareDatetime
    evaluation_revision: str
    evidence: dict[str, object]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class GrowthExperimentResponse(GrowthSchema):
    id: UUID
    business_id: UUID
    name: str
    hypothesis: str
    learning_key: str
    experiment_type: GrowthExperimentType
    status: GrowthExperimentStatus
    primary_metric: GrowthMetric
    attribution_classification: GrowthAttribution
    currency: str
    evaluation_window_days: int
    minimum_sample_size: int
    definition_version: int
    source_opportunity_id: UUID | None
    source_ai_action_id: UUID | None
    created_by_user_id: UUID | None
    measurement_start: AwareDatetime | None
    measurement_end: AwareDatetime | None
    evaluation_cutoff: AwareDatetime | None
    completed_at: AwareDatetime | None
    canceled_at: AwareDatetime | None
    variants: list[GrowthVariantResponse]
    result: GrowthExperimentResultResponse | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class GrowthExperimentPage(GrowthSchema):
    items: list[GrowthExperimentResponse]
    total: int
    page: int
    page_size: int


class GrowthLearningResponse(GrowthSchema):
    id: UUID
    content: str
    confidence: Decimal
    importance: int
    status: Literal["active", "superseded", "archived"]
    occurred_at: AwareDatetime | None
    last_reinforced_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class GrowthLearningPage(GrowthSchema):
    items: list[GrowthLearningResponse]
    total: int
    page: int
    page_size: int
