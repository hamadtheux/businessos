"""add growth learning experiments

Revision ID: b6f2c8d4e901
Revises: a4d9e7c2b610
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b6f2c8d4e901"
down_revision: str | Sequence[str] | None = "a4d9e7c2b610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_business_memories_id_business",
        "business_memories",
        ["id", "business_id"],
    )

    op.create_table(
        "growth_experiments",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("learning_key", sa.String(length=64), nullable=False),
        sa.Column("experiment_type", sa.String(length=24), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("primary_metric", sa.String(length=32), nullable=False),
        sa.Column(
            "attribution_classification", sa.String(length=32), nullable=False
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("evaluation_window_days", sa.Integer(), nullable=False),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False),
        sa.Column(
            "definition_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("source_opportunity_id", sa.Uuid(), nullable=True),
        sa.Column("source_ai_action_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("measurement_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("measurement_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluation_cutoff", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 180",
            name=op.f("ck_growth_experiments_valid_name"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(hypothesis)) BETWEEN 1 AND 2000",
            name=op.f("ck_growth_experiments_valid_hypothesis"),
        ),
        sa.CheckConstraint(
            "learning_key ~ '^[a-z][a-z0-9_]{0,63}$'",
            name=op.f("ck_growth_experiments_valid_learning_key"),
        ),
        sa.CheckConstraint(
            "experiment_type IN ('campaign','content')",
            name=op.f("ck_growth_experiments_valid_experiment_type"),
        ),
        sa.CheckConstraint(
            "status IN ('draft','ready','running','completed','evaluated','canceled')",
            name=op.f("ck_growth_experiments_valid_status"),
        ),
        sa.CheckConstraint(
            "primary_metric IN ('ctr','conversion_rate','cpc','cpa','roas')",
            name=op.f("ck_growth_experiments_valid_primary_metric"),
        ),
        sa.CheckConstraint(
            "attribution_classification IN ('provider_attributed','first_party_observed')",
            name=op.f("ck_growth_experiments_valid_attribution_classification"),
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_growth_experiments_valid_currency"),
        ),
        sa.CheckConstraint(
            "evaluation_window_days BETWEEN 1 AND 90",
            name=op.f("ck_growth_experiments_valid_evaluation_window_days"),
        ),
        sa.CheckConstraint(
            "minimum_sample_size BETWEEN 1 AND 1000000000",
            name=op.f("ck_growth_experiments_valid_minimum_sample_size"),
        ),
        sa.CheckConstraint(
            "definition_version BETWEEN 1 AND 10000",
            name=op.f("ck_growth_experiments_valid_definition_version"),
        ),
        sa.CheckConstraint(
            "measurement_end IS NULL OR measurement_start IS NOT NULL",
            name=op.f("ck_growth_experiments_measurement_end_requires_start"),
        ),
        sa.CheckConstraint(
            "measurement_end IS NULL OR measurement_end > measurement_start",
            name=op.f("ck_growth_experiments_valid_measurement_window"),
        ),
        sa.CheckConstraint(
            "evaluation_cutoff IS NULL OR (measurement_start IS NOT NULL AND "
            "measurement_end IS NOT NULL AND evaluation_cutoff >= measurement_start "
            "AND evaluation_cutoff >= measurement_end)",
            name=op.f("ck_growth_experiments_valid_evaluation_cutoff"),
        ),
        sa.CheckConstraint(
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
            name=op.f("ck_growth_experiments_consistent_lifecycle"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_growth_experiments_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_opportunity_id", "business_id"],
            ["opportunities.id", "opportunities.business_id"],
            name="fk_growth_experiments_opportunity_business",
        ),
        sa.ForeignKeyConstraint(
            ["source_ai_action_id", "business_id"],
            ["ai_actions.id", "ai_actions.business_id"],
            name="fk_growth_experiments_action_business",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_growth_experiments_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_growth_experiments")),
        sa.UniqueConstraint(
            "id", "business_id", name="uq_growth_experiments_id_business"
        ),
    )
    op.create_index(
        "ix_growth_experiments_business_status_updated",
        "growth_experiments",
        ["business_id", "status", "updated_at", "id"],
    )
    op.create_index(
        "ix_growth_experiments_business_learning_key",
        "growth_experiments",
        ["business_id", "learning_key", "created_at", "id"],
    )

    op.create_table(
        "growth_experiment_variants",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("variant_key", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column(
            "is_control", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("content_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "variant_key ~ '^[a-z][a-z0-9_]{0,31}$'",
            name=op.f("ck_growth_experiment_variants_valid_variant_key"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(label)) BETWEEN 1 AND 120",
            name=op.f("ck_growth_experiment_variants_valid_label"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_growth_experiment_variants_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "business_id"],
            ["growth_experiments.id", "growth_experiments.business_id"],
            name="fk_growth_experiment_variants_experiment_business",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "business_id"],
            ["marketing_campaigns.id", "marketing_campaigns.business_id"],
            name="fk_growth_experiment_variants_campaign_business",
        ),
        sa.ForeignKeyConstraint(
            ["content_id", "business_id"],
            ["marketing_content.id", "marketing_content.business_id"],
            name="fk_growth_experiment_variants_content_business",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_growth_experiment_variants")),
        sa.UniqueConstraint(
            "id",
            "business_id",
            name="uq_growth_experiment_variants_id_business",
        ),
        sa.UniqueConstraint(
            "experiment_id",
            "id",
            "business_id",
            name="uq_growth_experiment_variants_experiment_id_business",
        ),
        sa.UniqueConstraint(
            "experiment_id",
            "variant_key",
            name="uq_growth_experiment_variants_experiment_key",
        ),
    )
    op.create_index(
        "ix_growth_experiment_variants_one_control",
        "growth_experiment_variants",
        ["experiment_id"],
        unique=True,
        postgresql_where=sa.text("is_control"),
    )
    op.create_index(
        "ix_growth_experiment_variants_business_campaign",
        "growth_experiment_variants",
        ["business_id", "campaign_id", "experiment_id"],
    )

    op.create_table(
        "growth_experiment_results",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("classification", sa.String(length=48), nullable=False),
        sa.Column("primary_metric", sa.String(length=32), nullable=False),
        sa.Column(
            "attribution_classification", sa.String(length=32), nullable=False
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("control_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("directional_leader_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("absolute_difference", sa.Numeric(20, 6), nullable=True),
        sa.Column("relative_difference", sa.Numeric(20, 6), nullable=True),
        sa.Column("evidence_quality", sa.Numeric(4, 3), nullable=False),
        sa.Column("directional_leader_variant_id", sa.Uuid(), nullable=True),
        sa.Column("directional_leader_key", sa.String(length=32), nullable=True),
        sa.Column("learning_memory_id", sa.Uuid(), nullable=True),
        sa.Column("measurement_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("measurement_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluation_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluation_revision", sa.String(length=64), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "classification IN ('insufficient_evidence','no_material_difference',"
            "'observed_directional_difference','mixed_result')",
            name=op.f("ck_growth_experiment_results_valid_classification"),
        ),
        sa.CheckConstraint(
            "primary_metric IN ('ctr','conversion_rate','cpc','cpa','roas')",
            name=op.f("ck_growth_experiment_results_valid_primary_metric"),
        ),
        sa.CheckConstraint(
            "attribution_classification IN ('provider_attributed','first_party_observed')",
            name=op.f(
                "ck_growth_experiment_results_valid_attribution_classification"
            ),
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_growth_experiment_results_valid_currency"),
        ),
        sa.CheckConstraint(
            "evidence_quality BETWEEN 0.000 AND 1.000",
            name=op.f("ck_growth_experiment_results_valid_evidence_quality"),
        ),
        sa.CheckConstraint(
            "measurement_end > measurement_start AND "
            "evaluation_cutoff >= measurement_end AND "
            "evaluated_at >= evaluation_cutoff",
            name=op.f("ck_growth_experiment_results_valid_measurement_window"),
        ),
        sa.CheckConstraint(
            "char_length(evaluation_revision) = 64 AND "
            "evaluation_revision ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_growth_experiment_results_valid_evaluation_revision"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'object' AND pg_column_size(evidence) <= 32768",
            name=op.f("ck_growth_experiment_results_valid_evidence"),
        ),
        sa.CheckConstraint(
            "(classification = 'observed_directional_difference' AND "
            "directional_leader_variant_id IS NOT NULL AND "
            "directional_leader_key IS NOT NULL) OR "
            "(classification <> 'observed_directional_difference' AND "
            "directional_leader_variant_id IS NULL AND "
            "directional_leader_key IS NULL)",
            name=op.f("ck_growth_experiment_results_consistent_directional_leader"),
        ),
        sa.CheckConstraint(
            "directional_leader_key IS NULL OR "
            "directional_leader_key ~ '^[a-z][a-z0-9_]{0,31}$'",
            name=op.f("ck_growth_experiment_results_valid_directional_leader_key"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_growth_experiment_results_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "business_id"],
            ["growth_experiments.id", "growth_experiments.business_id"],
            name="fk_growth_experiment_results_experiment_business",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "directional_leader_variant_id", "business_id"],
            [
                "growth_experiment_variants.experiment_id",
                "growth_experiment_variants.id",
                "growth_experiment_variants.business_id",
            ],
            name="fk_growth_experiment_results_leader_variant_business",
        ),
        sa.ForeignKeyConstraint(
            ["learning_memory_id", "business_id"],
            ["business_memories.id", "business_memories.business_id"],
            name="fk_growth_experiment_results_learning_memory_business",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_growth_experiment_results")),
        sa.UniqueConstraint(
            "experiment_id", name="uq_growth_experiment_results_experiment"
        ),
        sa.UniqueConstraint(
            "id",
            "business_id",
            name="uq_growth_experiment_results_id_business",
        ),
    )
    op.create_index(
        "ix_growth_experiment_results_business_evaluated",
        "growth_experiment_results",
        ["business_id", "evaluated_at", "id"],
    )
    op.create_index(
        "ix_growth_experiment_results_business_learning_memory",
        "growth_experiment_results",
        ["business_id", "learning_memory_id", "id"],
    )


def downgrade() -> None:
    op.drop_table("growth_experiment_results")
    op.drop_table("growth_experiment_variants")
    op.drop_table("growth_experiments")
    op.drop_constraint(
        "uq_business_memories_id_business",
        "business_memories",
        type_="unique",
    )
