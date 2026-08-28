from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from typing import Final
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions.business_memory import BusinessMemoryError
from app.exceptions.growth_learning import (
    GrowthLearningNotFoundError,
    GrowthLearningPersistenceError,
    GrowthLearningStateError,
    GrowthLearningValidationError,
)
from app.models.ai_action import AIAction
from app.models.business_memory import BusinessMemory
from app.models.growth_learning import (
    GrowthExperiment,
    GrowthExperimentResult,
    GrowthExperimentVariant,
)
from app.models.marketing import Campaign, MarketingContent, MarketingPerformance
from app.models.opportunity import Opportunity
from app.schemas.growth_learning import (
    GrowthExperimentCreate,
    GrowthExperimentUpdate,
)
from app.services.business_memory import (
    create_system_memory,
    supersede_business_memory,
)
from app.services.operations import record_audit


METRIC_SAMPLE_FLOORS: Final = {
    "ctr": 1_000,
    "conversion_rate": 100,
    "cpc": 100,
    "cpa": 20,
    "roas": 20,
}
METRIC_SAMPLE_BASIS: Final = {
    "ctr": "impressions",
    "conversion_rate": "clicks",
    "cpc": "clicks",
    "cpa": "conversions",
    "roas": "conversions",
}
LOWER_IS_BETTER_METRICS: Final = frozenset({"cpc", "cpa"})
MATERIAL_RELATIVE_DIFFERENCE: Final = Decimal("0.100000")
RESULT_QUANTUM: Final = Decimal("0.000001")
CONFIDENCE_QUANTUM: Final = Decimal("0.001")
MAX_EVIDENCE_ROWS: Final = 10_000
MAX_LEARNING_EXPERIMENTS: Final = 50
MIN_LEARNING_EVIDENCE_QUALITY: Final = Decimal("0.750")


async def create_growth_experiment(
    session: AsyncSession,
    *,
    business_id: UUID,
    actor_user_id: UUID,
    data: GrowthExperimentCreate,
) -> GrowthExperiment:
    minimum_floor = METRIC_SAMPLE_FLOORS[data.primary_metric]
    if data.minimum_sample_size < minimum_floor:
        raise GrowthLearningValidationError(
            f"minimum_sample_size must be at least {minimum_floor} for {data.primary_metric}"
        )

    campaign_ids = {variant.campaign_id for variant in data.variants}
    try:
        campaigns = list(
            (
                await session.scalars(
                    select(Campaign).where(
                        Campaign.business_id == business_id,
                        Campaign.id.in_(campaign_ids),
                    )
                )
            ).all()
        )
    except SQLAlchemyError:
        raise GrowthLearningPersistenceError from None
    campaign_by_id = {campaign.id: campaign for campaign in campaigns}
    if set(campaign_by_id) != campaign_ids:
        raise GrowthLearningValidationError("variant campaign is invalid")

    currencies = {campaign.currency for campaign in campaigns}
    if len(currencies) != 1:
        raise GrowthLearningValidationError(
            "all experiment variants must use one currency"
        )
    currency = next(iter(currencies))

    if data.experiment_type == "content":
        content_ids = {
            variant.content_id
            for variant in data.variants
            if variant.content_id is not None
        }
        try:
            contents = list(
                (
                    await session.scalars(
                        select(MarketingContent).where(
                            MarketingContent.business_id == business_id,
                            MarketingContent.id.in_(content_ids),
                        )
                    )
                ).all()
            )
        except SQLAlchemyError:
            raise GrowthLearningPersistenceError from None
        content_by_id = {content.id: content for content in contents}
        if set(content_by_id) != content_ids:
            raise GrowthLearningValidationError("variant content is invalid")
        for variant in data.variants:
            content = content_by_id.get(variant.content_id)
            if content is None or content.campaign_id != variant.campaign_id:
                raise GrowthLearningValidationError(
                    "variant content must belong to its campaign"
                )

    await _validate_optional_source_references(
        session,
        business_id=business_id,
        source_opportunity_id=data.source_opportunity_id,
        source_ai_action_id=data.source_ai_action_id,
    )
    await _validate_learning_key_contract(
        session,
        business_id=business_id,
        learning_key=data.learning_key,
        experiment_type=data.experiment_type,
        primary_metric=data.primary_metric,
        attribution_classification=data.attribution_classification,
        currency=currency,
    )

    experiment = GrowthExperiment(
        business_id=business_id,
        name=data.name,
        hypothesis=data.hypothesis,
        learning_key=data.learning_key,
        experiment_type=data.experiment_type,
        status="draft",
        primary_metric=data.primary_metric,
        attribution_classification=data.attribution_classification,
        currency=currency,
        evaluation_window_days=data.evaluation_window_days,
        minimum_sample_size=data.minimum_sample_size,
        definition_version=1,
        source_opportunity_id=data.source_opportunity_id,
        source_ai_action_id=data.source_ai_action_id,
        created_by_user_id=actor_user_id,
        measurement_start=None,
        measurement_end=None,
        evaluation_cutoff=None,
        completed_at=None,
        canceled_at=None,
    )
    experiment.variants = [
        GrowthExperimentVariant(
            business_id=business_id,
            variant_key=variant.variant_key,
            label=variant.label,
            is_control=variant.is_control,
            campaign_id=variant.campaign_id,
            content_id=variant.content_id,
        )
        for variant in data.variants
    ]
    session.add(experiment)
    await _flush(session)
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        event_type="growth.experiment_created",
        entity_type="growth_experiment",
        entity_id=experiment.id,
        summary=(
            f"Created a draft {experiment.experiment_type}-level growth experiment "
            f"using {len(experiment.variants)} existing governed references."
        ),
    )
    return experiment


async def list_growth_experiments(
    session: AsyncSession,
    *,
    business_id: UUID,
    page: int,
    page_size: int,
    status: str | None = None,
) -> tuple[list[GrowthExperiment], int]:
    if not 1 <= page_size <= 100 or page < 1:
        raise GrowthLearningValidationError("pagination is invalid")
    where = [GrowthExperiment.business_id == business_id]
    if status is not None:
        where.append(GrowthExperiment.status == status)
    statement = (
        select(GrowthExperiment)
        .options(
            selectinload(GrowthExperiment.variants),
            selectinload(GrowthExperiment.result),
        )
        .where(*where)
        .order_by(GrowthExperiment.updated_at.desc(), GrowthExperiment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    try:
        total = int(
            await session.scalar(
                select(func.count(GrowthExperiment.id)).where(*where)
            )
            or 0
        )
        values = list((await session.scalars(statement)).all())
    except SQLAlchemyError:
        raise GrowthLearningPersistenceError from None
    return values, total


async def get_growth_experiment(
    session: AsyncSession,
    *,
    business_id: UUID,
    experiment_id: UUID,
    for_update: bool = False,
) -> GrowthExperiment:
    statement = (
        select(GrowthExperiment)
        .options(
            selectinload(GrowthExperiment.variants),
            selectinload(GrowthExperiment.result),
        )
        .where(
            GrowthExperiment.business_id == business_id,
            GrowthExperiment.id == experiment_id,
        )
    )
    if for_update:
        statement = statement.with_for_update(of=GrowthExperiment)
    try:
        experiment = await session.scalar(statement)
    except SQLAlchemyError:
        raise GrowthLearningPersistenceError from None
    if experiment is None:
        raise GrowthLearningNotFoundError
    return experiment


async def update_growth_experiment(
    session: AsyncSession,
    *,
    business_id: UUID,
    experiment_id: UUID,
    actor_user_id: UUID,
    data: GrowthExperimentUpdate,
) -> GrowthExperiment:
    experiment = await get_growth_experiment(
        session,
        business_id=business_id,
        experiment_id=experiment_id,
        for_update=True,
    )
    if experiment.status != "draft":
        raise GrowthLearningStateError("experiment definition is frozen")
    values = data.model_dump(exclude_unset=True)
    proposed_minimum = values.get(
        "minimum_sample_size", experiment.minimum_sample_size
    )
    if proposed_minimum < METRIC_SAMPLE_FLOORS[experiment.primary_metric]:
        raise GrowthLearningValidationError("minimum sample is below the metric floor")
    for key, value in values.items():
        setattr(experiment, key, value)
    await _flush(session)
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        event_type="growth.experiment_updated",
        entity_type="growth_experiment",
        entity_id=experiment.id,
        summary="Updated a draft growth experiment definition.",
    )
    return experiment


async def mark_growth_experiment_ready(
    session: AsyncSession,
    *,
    business_id: UUID,
    experiment_id: UUID,
    actor_user_id: UUID,
) -> GrowthExperiment:
    experiment = await get_growth_experiment(
        session,
        business_id=business_id,
        experiment_id=experiment_id,
        for_update=True,
    )
    if experiment.status != "draft":
        raise GrowthLearningStateError("only draft experiments can become ready")
    _validate_persisted_variant_contract(experiment)
    experiment.status = "ready"
    await _flush(session)
    _record_transition_audit(session, experiment, actor_user_id, "ready")
    return experiment


async def start_growth_experiment(
    session: AsyncSession,
    *,
    business_id: UUID,
    experiment_id: UUID,
    actor_user_id: UUID,
    now: datetime | None = None,
) -> GrowthExperiment:
    experiment = await get_growth_experiment(
        session,
        business_id=business_id,
        experiment_id=experiment_id,
        for_update=True,
    )
    if experiment.status != "ready":
        raise GrowthLearningStateError("only ready experiments can start")
    _validate_persisted_variant_contract(experiment)
    instant = _utc_instant(now)
    measurement_start = _next_utc_day_boundary(instant)
    experiment.status = "running"
    experiment.measurement_start = measurement_start
    experiment.measurement_end = measurement_start + timedelta(
        days=experiment.evaluation_window_days
    )
    await _flush(session)
    _record_transition_audit(session, experiment, actor_user_id, "running")
    return experiment


async def complete_growth_experiment(
    session: AsyncSession,
    *,
    business_id: UUID,
    experiment_id: UUID,
    actor_user_id: UUID,
    now: datetime | None = None,
) -> GrowthExperiment:
    experiment = await get_growth_experiment(
        session,
        business_id=business_id,
        experiment_id=experiment_id,
        for_update=True,
    )
    if experiment.status != "running":
        raise GrowthLearningStateError("only running experiments can complete")
    instant = _utc_instant(now)
    if experiment.measurement_start is None or experiment.measurement_end is None:
        raise GrowthLearningStateError("experiment measurement window is invalid")
    if instant < experiment.measurement_end:
        raise GrowthLearningStateError(
            "the complete UTC-day measurement window has not ended"
        )
    experiment.status = "completed"
    experiment.evaluation_cutoff = instant
    experiment.completed_at = instant
    await _flush(session)
    _record_transition_audit(session, experiment, actor_user_id, "completed")
    return experiment


async def cancel_growth_experiment(
    session: AsyncSession,
    *,
    business_id: UUID,
    experiment_id: UUID,
    actor_user_id: UUID,
    now: datetime | None = None,
) -> GrowthExperiment:
    experiment = await get_growth_experiment(
        session,
        business_id=business_id,
        experiment_id=experiment_id,
        for_update=True,
    )
    if experiment.status not in {"draft", "ready", "running"}:
        raise GrowthLearningStateError("experiment cannot be canceled")
    instant = _utc_instant(now)
    if experiment.status == "running":
        experiment.measurement_start = None
        experiment.measurement_end = None
    experiment.status = "canceled"
    experiment.canceled_at = instant
    await _flush(session)
    _record_transition_audit(session, experiment, actor_user_id, "canceled")
    return experiment


async def evaluate_growth_experiment(
    session: AsyncSession,
    *,
    business_id: UUID,
    experiment_id: UUID,
    actor_user_id: UUID | None,
    now: datetime | None = None,
) -> GrowthExperimentResult:
    experiment = await get_growth_experiment(
        session,
        business_id=business_id,
        experiment_id=experiment_id,
        for_update=True,
    )
    if experiment.status == "evaluated" and experiment.result is not None:
        return experiment.result
    if experiment.status != "completed":
        raise GrowthLearningStateError("only completed experiments can be evaluated")
    if (
        experiment.measurement_start is None
        or experiment.measurement_end is None
        or experiment.evaluation_cutoff is None
    ):
        raise GrowthLearningStateError("experiment measurement window is invalid")

    evidence, summary = await _measure_experiment(session, experiment)
    evaluated_at = _utc_instant(now)
    if evaluated_at < experiment.evaluation_cutoff:
        raise GrowthLearningValidationError(
            "evaluation timestamp cannot precede the frozen cutoff"
        )
    revision = _evaluation_revision(evidence)
    result = GrowthExperimentResult(
        business_id=business_id,
        experiment_id=experiment.id,
        classification=summary["classification"],
        primary_metric=experiment.primary_metric,
        attribution_classification=experiment.attribution_classification,
        currency=experiment.currency,
        control_value=summary["control_value"],
        directional_leader_value=summary["directional_leader_value"],
        absolute_difference=summary["absolute_difference"],
        relative_difference=summary["relative_difference"],
        evidence_quality=summary["evidence_quality"],
        directional_leader_variant_id=summary["directional_leader_variant_id"],
        directional_leader_key=summary["directional_leader_key"],
        learning_memory_id=None,
        measurement_start=experiment.measurement_start,
        measurement_end=experiment.measurement_end,
        evaluation_cutoff=experiment.evaluation_cutoff,
        evaluated_at=evaluated_at,
        evaluation_revision=revision,
        evidence=evidence,
    )
    session.add(result)
    await _flush(session)

    if (
        result.classification != "insufficient_evidence"
        and result.evidence_quality >= MIN_LEARNING_EVIDENCE_QUALITY
    ):
        await _refresh_business_learning(
            session,
            experiment=experiment,
            result=result,
        )

    experiment.status = "evaluated"
    experiment.result = result
    await _flush(session)
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        event_type="growth.experiment_evaluated",
        entity_type="growth_experiment",
        entity_id=experiment.id,
        summary=(
            "Deterministically evaluated stored performance evidence as "
            f"{result.classification}; no external action was dispatched."
        ),
    )
    return result


async def list_growth_learnings(
    session: AsyncSession,
    *,
    business_id: UUID,
    page: int,
    page_size: int,
) -> tuple[list[BusinessMemory], int]:
    if not 1 <= page_size <= 100 or page < 1:
        raise GrowthLearningValidationError("pagination is invalid")
    where = (
        BusinessMemory.business_id == business_id,
        BusinessMemory.memory_type == "ai_learning",
        BusinessMemory.source_type == "system",
        BusinessMemory.status == "active",
        BusinessMemory.source_reference.like("growth-learning:%"),
    )
    try:
        total = int(
            await session.scalar(select(func.count(BusinessMemory.id)).where(*where))
            or 0
        )
        values = list(
            (
                await session.scalars(
                    select(BusinessMemory)
                    .where(*where)
                    .order_by(
                        BusinessMemory.updated_at.desc(), BusinessMemory.id.desc()
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
    except SQLAlchemyError:
        raise GrowthLearningPersistenceError from None
    return values, total


async def _measure_experiment(
    session: AsyncSession,
    experiment: GrowthExperiment,
) -> tuple[dict[str, object], dict[str, object]]:
    variant_match = and_(
        MarketingPerformance.business_id == GrowthExperimentVariant.business_id,
        MarketingPerformance.campaign_id == GrowthExperimentVariant.campaign_id,
        or_(
            GrowthExperimentVariant.content_id.is_(None),
            MarketingPerformance.content_id == GrowthExperimentVariant.content_id,
        ),
    )
    window = (
        GrowthExperimentVariant.business_id == experiment.business_id,
        GrowthExperimentVariant.experiment_id == experiment.id,
        MarketingPerformance.attribution_class
        == experiment.attribution_classification,
        MarketingPerformance.period_start >= experiment.measurement_start.date(),
        # Stored performance facts use whole dates. The durable experiment
        # window is therefore [measurement_start, measurement_end) in complete
        # UTC days; partial days can never leak into the comparison.
        MarketingPerformance.period_end < experiment.measurement_end.date(),
        # Reject pre-seeded future-dated facts. Late provider rows remain valid
        # when they are recorded after measurement starts and before the frozen
        # completion cutoff.
        MarketingPerformance.created_at >= experiment.measurement_start,
        MarketingPerformance.created_at <= experiment.evaluation_cutoff,
    )
    aggregate_statement = (
        select(
            GrowthExperimentVariant.id,
            func.count(MarketingPerformance.id),
            func.coalesce(func.sum(MarketingPerformance.spend), 0),
            func.coalesce(func.sum(MarketingPerformance.impressions), 0),
            func.coalesce(func.sum(MarketingPerformance.clicks), 0),
            func.coalesce(func.sum(MarketingPerformance.conversions), 0),
            func.coalesce(func.sum(MarketingPerformance.revenue), 0),
        )
        .join(MarketingPerformance, variant_match, isouter=True)
        .where(*window)
        .group_by(GrowthExperimentVariant.id)
        .order_by(GrowthExperimentVariant.id)
    )
    period_statement = (
        select(
            GrowthExperimentVariant.id,
            MarketingPerformance.channel,
            MarketingPerformance.period_start,
            MarketingPerformance.period_end,
        )
        .join(MarketingPerformance, variant_match)
        .where(*window)
        .order_by(
            GrowthExperimentVariant.id,
            MarketingPerformance.channel,
            MarketingPerformance.period_start,
            MarketingPerformance.period_end,
            MarketingPerformance.id,
        )
        .limit(MAX_EVIDENCE_ROWS + 1)
    )
    try:
        aggregate_rows = (await session.execute(aggregate_statement)).all()
        period_rows = (await session.execute(period_statement)).all()
    except SQLAlchemyError:
        raise GrowthLearningPersistenceError from None

    aggregate_by_variant = {row[0]: row for row in aggregate_rows}
    too_many_rows = sum(int(row[1]) for row in aggregate_rows) > MAX_EVIDENCE_ROWS
    overlaps: set[UUID] = set()
    previous_period_end: dict[tuple[UUID, str], object] = {}
    if not too_many_rows:
        for variant_id, channel, period_start, period_end in period_rows:
            key = (variant_id, channel)
            previous = previous_period_end.get(key)
            if previous is not None and period_start <= previous:
                overlaps.add(variant_id)
            if previous is None or period_end > previous:
                previous_period_end[key] = period_end

    metrics: list[dict[str, object]] = []
    for variant in experiment.variants:
        row = aggregate_by_variant.get(variant.id)
        row_count = int(row[1]) if row else 0
        spend = Decimal(row[2]) if row else Decimal("0")
        impressions = int(row[3]) if row else 0
        clicks = int(row[4]) if row else 0
        conversions = int(row[5]) if row else 0
        revenue = Decimal(row[6]) if row else Decimal("0")
        sample_size = _sample_size(
            experiment.primary_metric,
            impressions=impressions,
            clicks=clicks,
            conversions=conversions,
        )
        metric_value = _metric_value(
            experiment.primary_metric,
            spend=spend,
            impressions=impressions,
            clicks=clicks,
            conversions=conversions,
            revenue=revenue,
        )
        data_quality = (
            "too_many_rows"
            if too_many_rows
            else "overlapping_periods"
            if variant.id in overlaps
            else "complete"
        )
        sufficient = (
            data_quality == "complete"
            and metric_value is not None
            and sample_size >= experiment.minimum_sample_size
        )
        metrics.append(
            {
                "variant_id": str(variant.id),
                "variant_key": variant.variant_key,
                "is_control": variant.is_control,
                "performance_row_count": row_count,
                "sample_basis": METRIC_SAMPLE_BASIS[experiment.primary_metric],
                "sample_size": sample_size,
                "minimum_sample_size": experiment.minimum_sample_size,
                "metric_value": _decimal_string(metric_value),
                "spend": _decimal_string(spend),
                "impressions": impressions,
                "clicks": clicks,
                "conversions": conversions,
                "revenue": _decimal_string(revenue),
                "data_quality": data_quality,
                "sufficient": sufficient,
            }
        )

    quality = _evidence_quality(
        metrics,
        minimum_sample_size=experiment.minimum_sample_size,
        attribution_classification=experiment.attribution_classification,
    )
    comparison = _classify_metrics(metrics, primary_metric=experiment.primary_metric)
    evidence: dict[str, object] = {
        "formula_version": "growth-evaluation-v1",
        "primary_metric": experiment.primary_metric,
        "metric_direction": (
            "lower_is_better"
            if experiment.primary_metric in LOWER_IS_BETTER_METRICS
            else "higher_is_better"
        ),
        "attribution_classification": experiment.attribution_classification,
        "currency": experiment.currency,
        "measurement_start": experiment.measurement_start.isoformat(),
        "measurement_end": experiment.measurement_end.isoformat(),
        "evaluation_cutoff": experiment.evaluation_cutoff.isoformat(),
        "fact_created_at_policy": "measurement_start_through_evaluation_cutoff",
        "minimum_sample_size": experiment.minimum_sample_size,
        "sample_basis": METRIC_SAMPLE_BASIS[experiment.primary_metric],
        "material_relative_difference": _decimal_string(
            MATERIAL_RELATIVE_DIFFERENCE
        ),
        "statistical_significance_test": None,
        "causal_claim_allowed": False,
        "variant_metrics": metrics,
    }
    comparison["evidence_quality"] = quality
    return evidence, comparison


def _classify_metrics(
    metrics: list[dict[str, object]], *, primary_metric: str
) -> dict[str, object]:
    control = next(item for item in metrics if item["is_control"])
    default: dict[str, object] = {
        "classification": "insufficient_evidence",
        "control_value": _decimal_value(control["metric_value"]),
        "directional_leader_value": None,
        "absolute_difference": None,
        "relative_difference": None,
        "directional_leader_variant_id": None,
        "directional_leader_key": None,
    }
    if not all(item["sufficient"] for item in metrics):
        return default

    control_value = _decimal_value(control["metric_value"])
    if control_value is None:
        return default
    challengers = [item for item in metrics if not item["is_control"]]
    metric_values = [(_decimal_value(item["metric_value"]), item) for item in challengers]
    if any(value is None for value, _item in metric_values):
        return default

    lower_is_better = primary_metric in LOWER_IS_BETTER_METRICS
    signed: list[tuple[Decimal, dict[str, object]]] = []
    for value, challenger in metric_values:
        assert value is not None
        improvement = control_value - value if lower_is_better else value - control_value
        signed.append((improvement, challenger))

    materially_better = [
        (change, item)
        for change, item in signed
        if change > 0 and _is_material(change, control_value)
    ]
    materially_worse = [
        (change, item)
        for change, item in signed
        if change < 0 and _is_material(change, control_value)
    ]
    if materially_better and materially_worse:
        return default | {"classification": "mixed_result"}
    if not materially_better and not materially_worse:
        return default | {"classification": "no_material_difference"}

    if materially_better:
        change, leader = max(materially_better, key=lambda pair: pair[0])
        leader_value = _decimal_value(leader["metric_value"])
    else:
        change, comparator = min(materially_worse, key=lambda pair: pair[0])
        leader = control
        leader_value = control_value
        change = abs(change)
    relative = (
        (abs(change) / abs(control_value)).quantize(
            RESULT_QUANTUM, rounding=ROUND_HALF_UP
        )
        if control_value != 0
        else None
    )
    return default | {
        "classification": "observed_directional_difference",
        "directional_leader_value": leader_value,
        "absolute_difference": abs(change).quantize(
            RESULT_QUANTUM, rounding=ROUND_HALF_UP
        ),
        "relative_difference": relative,
        "directional_leader_variant_id": UUID(str(leader["variant_id"])),
        "directional_leader_key": str(leader["variant_key"]),
    }


async def _refresh_business_learning(
    session: AsyncSession,
    *,
    experiment: GrowthExperiment,
    result: GrowthExperimentResult,
) -> None:
    lock_key = (
        f"growth-learning:{experiment.business_id}:{experiment.learning_key}:"
        f"{experiment.primary_metric}:{experiment.attribution_classification}:"
        f"{experiment.currency}"
    )
    try:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )
        rows = (
            await session.execute(
                select(GrowthExperimentResult, GrowthExperiment)
                .join(
                    GrowthExperiment,
                    and_(
                        GrowthExperiment.id == GrowthExperimentResult.experiment_id,
                        GrowthExperiment.business_id
                        == GrowthExperimentResult.business_id,
                    ),
                )
                .where(
                    GrowthExperiment.business_id == experiment.business_id,
                    GrowthExperiment.learning_key == experiment.learning_key,
                    GrowthExperiment.experiment_type == experiment.experiment_type,
                    GrowthExperiment.primary_metric == experiment.primary_metric,
                    GrowthExperiment.attribution_classification
                    == experiment.attribution_classification,
                    GrowthExperiment.currency == experiment.currency,
                    GrowthExperimentResult.classification
                    != "insufficient_evidence",
                    GrowthExperimentResult.evidence_quality
                    >= MIN_LEARNING_EVIDENCE_QUALITY,
                )
                .order_by(
                    GrowthExperimentResult.evaluated_at.asc(),
                    GrowthExperimentResult.id.asc(),
                )
                .limit(MAX_LEARNING_EXPERIMENTS)
            )
        ).all()
    except SQLAlchemyError:
        raise GrowthLearningPersistenceError from None

    if not any(stored_result.id == result.id for stored_result, _ in rows):
        rows.append((result, experiment))
    rows = rows[-MAX_LEARNING_EXPERIMENTS:]
    prior_memory_ids = {
        stored_result.learning_memory_id
        for stored_result, _stored_experiment in rows
        if stored_result.learning_memory_id is not None
    }
    prior_memories: list[BusinessMemory] = []
    if prior_memory_ids:
        try:
            prior_memories = list(
                (
                    await session.scalars(
                        select(BusinessMemory)
                        .where(
                            BusinessMemory.business_id == experiment.business_id,
                            BusinessMemory.id.in_(prior_memory_ids),
                            BusinessMemory.status == "active",
                        )
                        .order_by(
                            BusinessMemory.created_at.desc(), BusinessMemory.id.desc()
                        )
                        .limit(MAX_LEARNING_EXPERIMENTS)
                    )
                ).all()
            )
        except SQLAlchemyError:
            raise GrowthLearningPersistenceError from None

    content, confidence = _learning_content_and_confidence(experiment, rows)
    source_reference = (
        f"growth-learning:{experiment.learning_key}:{experiment.primary_metric}:"
        f"{experiment.attribution_classification}:{experiment.currency}"
    )
    try:
        memory = await create_system_memory(
            session,
            experiment.business_id,
            memory_type="ai_learning",
            content=content,
            confidence=confidence,
            source_reference=source_reference,
            importance=4,
            occurred_at=result.evaluated_at,
        )
        result.learning_memory_id = memory.id
        await _flush(session)
        for prior in prior_memories:
            if prior.id != memory.id:
                await supersede_business_memory(
                    session,
                    experiment.business_id,
                    prior.id,
                    memory.id,
                )
    except BusinessMemoryError:
        raise GrowthLearningPersistenceError from None


def _learning_content_and_confidence(
    experiment: GrowthExperiment,
    rows: list[tuple[GrowthExperimentResult, GrowthExperiment]],
) -> tuple[str, Decimal]:
    classifications = [stored.classification for stored, _ in rows]
    leader_keys = [
        stored.directional_leader_key
        for stored, _ in rows
        if stored.classification == "observed_directional_difference"
        and stored.directional_leader_key is not None
    ]
    count = len(rows)
    common_prefix = (
        f"Across {count} completed {experiment.experiment_type}-level "
        f"comparison{'s' if count != 1 else ''} for learning key "
        f"'{experiment.learning_key}', using {experiment.attribution_classification} "
        f"{experiment.primary_metric} evidence"
    )
    if len(leader_keys) == count and len(set(leader_keys)) == 1:
        pattern = "directional"
        content = (
            f"{common_prefix}, variant '{leader_keys[0]}' showed the strongest "
            "observed directional result. This is a bounded observed comparison, "
            "not statistical significance or proof that the variant caused the outcome."
        )
        consistency = Decimal("1")
    elif all(value == "no_material_difference" for value in classifications):
        pattern = "no_difference"
        content = (
            f"{common_prefix}, no material difference met the server-defined 10% "
            "threshold. This does not prove the variants are equivalent."
        )
        consistency = Decimal("1")
    else:
        pattern = "mixed"
        content = (
            f"{common_prefix}, results were mixed and do not support a stable variant "
            "preference. No causal or statistically significant conclusion is claimed."
        )
        counts: dict[str, int] = defaultdict(int)
        for value in leader_keys:
            counts[value] += 1
        consistency = Decimal(max(counts.values(), default=0)) / Decimal(count)

    minimum_quality = min(stored.evidence_quality for stored, _ in rows)
    repetition = min(Decimal("1"), Decimal(count) / Decimal("3"))
    attribution_factor = (
        Decimal("0.900")
        if experiment.attribution_classification == "provider_attributed"
        else Decimal("0.800")
    )
    confidence = (
        Decimal("0.350") * minimum_quality
        + Decimal("0.250") * repetition
        + Decimal("0.250") * consistency
        + Decimal("0.150") * attribution_factor
    )
    if pattern == "mixed":
        confidence *= Decimal("0.850")
    return content, min(Decimal("1"), confidence).quantize(
        CONFIDENCE_QUANTUM, rounding=ROUND_HALF_UP
    )


async def _validate_optional_source_references(
    session: AsyncSession,
    *,
    business_id: UUID,
    source_opportunity_id: UUID | None,
    source_ai_action_id: UUID | None,
) -> None:
    try:
        if source_opportunity_id is not None:
            owned = await session.scalar(
                select(Opportunity.id).where(
                    Opportunity.business_id == business_id,
                    Opportunity.id == source_opportunity_id,
                )
            )
            if owned is None:
                raise GrowthLearningValidationError("source opportunity is invalid")
        if source_ai_action_id is not None:
            owned = await session.scalar(
                select(AIAction.id).where(
                    AIAction.business_id == business_id,
                    AIAction.id == source_ai_action_id,
                )
            )
            if owned is None:
                raise GrowthLearningValidationError("source action is invalid")
    except GrowthLearningValidationError:
        raise
    except SQLAlchemyError:
        raise GrowthLearningPersistenceError from None


async def _validate_learning_key_contract(
    session: AsyncSession,
    *,
    business_id: UUID,
    learning_key: str,
    experiment_type: str,
    primary_metric: str,
    attribution_classification: str,
    currency: str,
) -> None:
    try:
        existing = await session.scalar(
            select(GrowthExperiment)
            .where(
                GrowthExperiment.business_id == business_id,
                GrowthExperiment.learning_key == learning_key,
            )
            .order_by(GrowthExperiment.created_at.desc(), GrowthExperiment.id.desc())
            .limit(1)
        )
    except SQLAlchemyError:
        raise GrowthLearningPersistenceError from None
    if existing is None:
        return
    if (
        existing.experiment_type != experiment_type
        or existing.primary_metric != primary_metric
        or existing.attribution_classification != attribution_classification
        or existing.currency != currency
    ):
        raise GrowthLearningValidationError(
            "learning_key already identifies an incompatible evidence contract"
        )


def _validate_persisted_variant_contract(experiment: GrowthExperiment) -> None:
    if not 2 <= len(experiment.variants) <= 5:
        raise GrowthLearningValidationError("experiment requires two to five variants")
    if sum(variant.is_control for variant in experiment.variants) != 1:
        raise GrowthLearningValidationError("experiment requires exactly one control")
    keys = [variant.variant_key for variant in experiment.variants]
    refs = [(variant.campaign_id, variant.content_id) for variant in experiment.variants]
    if len(keys) != len(set(keys)) or len(refs) != len(set(refs)):
        raise GrowthLearningValidationError("experiment variants are not unique")
    if experiment.experiment_type == "campaign" and any(
        variant.content_id is not None for variant in experiment.variants
    ):
        raise GrowthLearningValidationError("campaign experiment variant is invalid")
    if experiment.experiment_type == "content" and any(
        variant.content_id is None for variant in experiment.variants
    ):
        raise GrowthLearningValidationError("content experiment variant is invalid")


def _sample_size(
    metric: str, *, impressions: int, clicks: int, conversions: int
) -> int:
    if metric == "ctr":
        return impressions
    if metric in {"conversion_rate", "cpc"}:
        return clicks
    return conversions


def _metric_value(
    metric: str,
    *,
    spend: Decimal,
    impressions: int,
    clicks: int,
    conversions: int,
    revenue: Decimal,
) -> Decimal | None:
    numerator: Decimal
    denominator: Decimal
    if metric == "ctr":
        numerator, denominator = Decimal(clicks) * 100, Decimal(impressions)
    elif metric == "conversion_rate":
        numerator, denominator = Decimal(conversions) * 100, Decimal(clicks)
    elif metric == "cpc":
        numerator, denominator = spend, Decimal(clicks)
    elif metric == "cpa":
        numerator, denominator = spend, Decimal(conversions)
    else:
        numerator, denominator = revenue, spend
    if denominator <= 0:
        return None
    return (numerator / denominator).quantize(
        RESULT_QUANTUM, rounding=ROUND_HALF_UP
    )


def _evidence_quality(
    metrics: list[dict[str, object]],
    *,
    minimum_sample_size: int,
    attribution_classification: str,
) -> Decimal:
    minimum_sample = min(int(item["sample_size"]) for item in metrics)
    minimum_rows = min(int(item["performance_row_count"]) for item in metrics)
    sample_coverage = min(
        Decimal("1"), Decimal(minimum_sample) / Decimal(minimum_sample_size)
    )
    observation_coverage = min(Decimal("1"), Decimal(minimum_rows) / Decimal("2"))
    attribution_factor = (
        Decimal("0.900")
        if attribution_classification == "provider_attributed"
        else Decimal("0.800")
    )
    data_quality_factor = (
        Decimal("1")
        if all(item["data_quality"] == "complete" for item in metrics)
        else Decimal("0")
    )
    quality = (
        Decimal("0.500") * sample_coverage
        + Decimal("0.200") * observation_coverage
        + Decimal("0.200") * attribution_factor
        + Decimal("0.100") * data_quality_factor
    )
    return min(Decimal("1"), quality).quantize(
        CONFIDENCE_QUANTUM, rounding=ROUND_HALF_UP
    )


def _is_material(change: Decimal, control_value: Decimal) -> bool:
    if control_value == 0:
        return change != 0
    return abs(change) / abs(control_value) >= MATERIAL_RELATIVE_DIFFERENCE


def _decimal_string(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _decimal_value(value: object) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _evaluation_revision(evidence: dict[str, object]) -> str:
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _utc_instant(value: datetime | None) -> datetime:
    instant = value or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise GrowthLearningValidationError("timestamp must include a timezone")
    return instant.astimezone(UTC)


def _next_utc_day_boundary(instant: datetime) -> datetime:
    boundary = instant.replace(hour=0, minute=0, second=0, microsecond=0)
    return boundary if instant == boundary else boundary + timedelta(days=1)


def _record_transition_audit(
    session: AsyncSession,
    experiment: GrowthExperiment,
    actor_user_id: UUID,
    status: str,
) -> None:
    record_audit(
        session,
        business_id=experiment.business_id,
        actor_user_id=actor_user_id,
        event_type=f"growth.experiment_{status}",
        entity_type="growth_experiment",
        entity_id=experiment.id,
        summary=(
            f"Moved growth experiment to {status}; this transition performed no "
            "provider write or external action execution."
        ),
    )


async def _flush(session: AsyncSession) -> None:
    try:
        await session.flush()
    except SQLAlchemyError:
        raise GrowthLearningPersistenceError from None
