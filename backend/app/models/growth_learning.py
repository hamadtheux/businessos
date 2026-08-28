from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.business_memory import BusinessMemory


class GrowthExperiment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A bounded, governed campaign- or content-level comparison."""

    __tablename__ = "growth_experiments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_opportunity_id", "business_id"],
            ["opportunities.id", "opportunities.business_id"],
            name="fk_growth_experiments_opportunity_business",
        ),
        ForeignKeyConstraint(
            ["source_ai_action_id", "business_id"],
            ["ai_actions.id", "ai_actions.business_id"],
            name="fk_growth_experiments_action_business",
        ),
        UniqueConstraint(
            "id", "business_id", name="uq_growth_experiments_id_business"
        ),
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 180", name="valid_name"
        ),
        CheckConstraint(
            "char_length(btrim(hypothesis)) BETWEEN 1 AND 2000",
            name="valid_hypothesis",
        ),
        CheckConstraint(
            "learning_key ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="valid_learning_key",
        ),
        CheckConstraint(
            "experiment_type IN ('campaign','content')",
            name="valid_experiment_type",
        ),
        CheckConstraint(
            "status IN ('draft','ready','running','completed','evaluated','canceled')",
            name="valid_status",
        ),
        CheckConstraint(
            "primary_metric IN ('ctr','conversion_rate','cpc','cpa','roas')",
            name="valid_primary_metric",
        ),
        CheckConstraint(
            "attribution_classification IN ('provider_attributed','first_party_observed')",
            name="valid_attribution_classification",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="valid_currency"),
        CheckConstraint(
            "evaluation_window_days BETWEEN 1 AND 90",
            name="valid_evaluation_window_days",
        ),
        CheckConstraint(
            "minimum_sample_size BETWEEN 1 AND 1000000000",
            name="valid_minimum_sample_size",
        ),
        CheckConstraint(
            "definition_version BETWEEN 1 AND 10000",
            name="valid_definition_version",
        ),
        CheckConstraint(
            "measurement_end IS NULL OR measurement_start IS NOT NULL",
            name="measurement_end_requires_start",
        ),
        CheckConstraint(
            "measurement_end IS NULL OR measurement_end > measurement_start",
            name="valid_measurement_window",
        ),
        CheckConstraint(
            "evaluation_cutoff IS NULL OR (measurement_start IS NOT NULL AND "
            "measurement_end IS NOT NULL AND evaluation_cutoff >= measurement_start "
            "AND evaluation_cutoff >= measurement_end)",
            name="valid_evaluation_cutoff",
        ),
        CheckConstraint(
            "(status IN ('draft','ready') AND measurement_start IS NULL AND "
            "measurement_end IS NULL AND evaluation_cutoff IS NULL AND "
            "completed_at IS NULL AND canceled_at IS NULL) OR "
            "(status = 'running' AND measurement_start IS NOT NULL AND "
            "measurement_end IS NOT NULL AND evaluation_cutoff IS NULL AND "
            "completed_at IS NULL AND canceled_at IS NULL) OR "
            "(status IN ('completed','evaluated') AND measurement_start IS NOT NULL "
            "AND measurement_end IS NOT NULL AND evaluation_cutoff IS NOT NULL "
            "AND completed_at IS NOT NULL AND canceled_at IS NULL) OR "
            "(status = 'canceled' AND evaluation_cutoff IS NULL AND "
            "completed_at IS NULL AND canceled_at IS NOT NULL)",
            name="consistent_lifecycle",
        ),
        Index(
            "ix_growth_experiments_business_status_updated",
            "business_id",
            "status",
            "updated_at",
            "id",
        ),
        Index(
            "ix_growth_experiments_business_learning_key",
            "business_id",
            "learning_key",
            "created_at",
            "id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    learning_key: Mapped[str] = mapped_column(String(64), nullable=False)
    experiment_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="draft", server_default="draft"
    )
    primary_metric: Mapped[str] = mapped_column(String(32), nullable=False)
    attribution_classification: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    evaluation_window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    source_opportunity_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source_ai_action_id: Mapped[UUID | None] = mapped_column(nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    measurement_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    measurement_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evaluation_cutoff: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    variants: Mapped[list["GrowthExperimentVariant"]] = relationship(
        back_populates="experiment",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="GrowthExperimentVariant.variant_key",
    )
    result: Mapped["GrowthExperimentResult | None"] = relationship(
        back_populates="experiment",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=False,
    )


class GrowthExperimentVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A stable variant that references existing tenant-owned execution state."""

    __tablename__ = "growth_experiment_variants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id", "business_id"],
            ["growth_experiments.id", "growth_experiments.business_id"],
            name="fk_growth_experiment_variants_experiment_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["campaign_id", "business_id"],
            ["marketing_campaigns.id", "marketing_campaigns.business_id"],
            name="fk_growth_experiment_variants_campaign_business",
        ),
        ForeignKeyConstraint(
            ["content_id", "business_id"],
            ["marketing_content.id", "marketing_content.business_id"],
            name="fk_growth_experiment_variants_content_business",
        ),
        UniqueConstraint(
            "id", "business_id", name="uq_growth_experiment_variants_id_business"
        ),
        UniqueConstraint(
            "experiment_id",
            "id",
            "business_id",
            name="uq_growth_experiment_variants_experiment_id_business",
        ),
        UniqueConstraint(
            "experiment_id",
            "variant_key",
            name="uq_growth_experiment_variants_experiment_key",
        ),
        CheckConstraint(
            "variant_key ~ '^[a-z][a-z0-9_]{0,31}$'",
            name="valid_variant_key",
        ),
        CheckConstraint(
            "char_length(btrim(label)) BETWEEN 1 AND 120", name="valid_label"
        ),
        Index(
            "ix_growth_experiment_variants_one_control",
            "experiment_id",
            unique=True,
            postgresql_where=text("is_control"),
        ),
        Index(
            "ix_growth_experiment_variants_business_campaign",
            "business_id",
            "campaign_id",
            "experiment_id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    experiment_id: Mapped[UUID] = mapped_column(nullable=False)
    variant_key: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    is_control: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    campaign_id: Mapped[UUID] = mapped_column(nullable=False)
    content_id: Mapped[UUID | None] = mapped_column(nullable=True)

    experiment: Mapped[GrowthExperiment] = relationship(back_populates="variants")


class GrowthExperimentResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable deterministic evaluation for a completed experiment."""

    __tablename__ = "growth_experiment_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id", "business_id"],
            ["growth_experiments.id", "growth_experiments.business_id"],
            name="fk_growth_experiment_results_experiment_business",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["experiment_id", "directional_leader_variant_id", "business_id"],
            [
                "growth_experiment_variants.experiment_id",
                "growth_experiment_variants.id",
                "growth_experiment_variants.business_id",
            ],
            name="fk_growth_experiment_results_leader_variant_business",
        ),
        ForeignKeyConstraint(
            ["learning_memory_id", "business_id"],
            ["business_memories.id", "business_memories.business_id"],
            name="fk_growth_experiment_results_learning_memory_business",
        ),
        UniqueConstraint(
            "experiment_id", name="uq_growth_experiment_results_experiment"
        ),
        UniqueConstraint(
            "id", "business_id", name="uq_growth_experiment_results_id_business"
        ),
        CheckConstraint(
            "classification IN ('insufficient_evidence','no_material_difference',"
            "'observed_directional_difference','mixed_result')",
            name="valid_classification",
        ),
        CheckConstraint(
            "primary_metric IN ('ctr','conversion_rate','cpc','cpa','roas')",
            name="valid_primary_metric",
        ),
        CheckConstraint(
            "attribution_classification IN ('provider_attributed','first_party_observed')",
            name="valid_attribution_classification",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="valid_currency"),
        CheckConstraint(
            "evidence_quality BETWEEN 0.000 AND 1.000",
            name="valid_evidence_quality",
        ),
        CheckConstraint(
            "measurement_end > measurement_start AND "
            "evaluation_cutoff >= measurement_end AND "
            "evaluated_at >= evaluation_cutoff",
            name="valid_measurement_window",
        ),
        CheckConstraint(
            "char_length(evaluation_revision) = 64 AND "
            "evaluation_revision ~ '^[0-9a-f]{64}$'",
            name="valid_evaluation_revision",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence) = 'object' AND pg_column_size(evidence) <= 32768",
            name="valid_evidence",
        ),
        CheckConstraint(
            "(classification = 'observed_directional_difference' AND "
            "directional_leader_variant_id IS NOT NULL AND "
            "directional_leader_key IS NOT NULL) OR "
            "(classification <> 'observed_directional_difference' AND "
            "directional_leader_variant_id IS NULL AND "
            "directional_leader_key IS NULL)",
            name="consistent_directional_leader",
        ),
        CheckConstraint(
            "directional_leader_key IS NULL OR "
            "directional_leader_key ~ '^[a-z][a-z0-9_]{0,31}$'",
            name="valid_directional_leader_key",
        ),
        Index(
            "ix_growth_experiment_results_business_evaluated",
            "business_id",
            "evaluated_at",
            "id",
        ),
        Index(
            "ix_growth_experiment_results_business_learning_memory",
            "business_id",
            "learning_memory_id",
            "id",
        ),
    )

    business_id: Mapped[UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    experiment_id: Mapped[UUID] = mapped_column(nullable=False)
    classification: Mapped[str] = mapped_column(String(48), nullable=False)
    primary_metric: Mapped[str] = mapped_column(String(32), nullable=False)
    attribution_classification: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    control_value: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    directional_leader_value: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    absolute_difference: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    relative_difference: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    evidence_quality: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False
    )
    directional_leader_variant_id: Mapped[UUID | None] = mapped_column(
        nullable=True
    )
    directional_leader_key: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    learning_memory_id: Mapped[UUID | None] = mapped_column(nullable=True)
    measurement_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    measurement_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evaluation_cutoff: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evaluation_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    experiment: Mapped[GrowthExperiment] = relationship(back_populates="result")
    learning_memory: Mapped["BusinessMemory | None"] = relationship(
        lazy="selectin", overlaps="experiment,result"
    )
