from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import and_, case, exists, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from pydantic import ValidationError

from app.agents.provider import AIAgentProvider
from app.domain.background_jobs import initial_opportunity_analysis_job_key
from app.domain.business_industries import get_business_industry, is_healthcare_business_type
from app.exceptions.background_jobs import BackgroundJobError
from app.exceptions.automation_intelligence import (
    AutomationIntelligencePersistenceError,
    AutomationIntelligenceProviderError,
)
from app.exceptions.business_brain import BusinessBrainAssemblyError
from app.exceptions.marketing import MarketingAIError
from app.models.automation_intelligence import MarketingAutomationRun
from app.models.business import Business
from app.models.catalog_item import CatalogItem
from app.models.commerce import CommerceEvent
from app.models.customer import Customer
from app.models.integration import IntegrationConnection
from app.models.marketing import (
    Campaign,
    Competitor,
    CompetitorObservation,
    MarketingContent,
    MarketingPerformance,
    MarketingTrend,
    ProductCampaignPerformance,
)
from app.models.notification import Notification
from app.models.opportunity import Opportunity
from app.models.order import Order, OrderLineItem, OrderRefund
from app.services.automation_intelligence import get_marketing_run
from app.services.background_jobs import enqueue_job
from app.services.billing import BillingEntitlementError, require_feature
from app.services.business_brain_assembly import build_business_brain_manifest
from app.schemas.marketing import ScheduledContentProposal
from app.services.marketing import _execute_cmo
from app.services.operations import record_audit


BUSINESS_GROWTH_LOOKBACK_DAYS = 7
INVENTORY_VELOCITY_LOOKBACK_DAYS = 14
MAX_BUSINESS_GROWTH_OPPORTUNITIES = 20
MAX_OPPORTUNITIES_PER_DETECTOR = 4
MAX_DETECTOR_CANDIDATES = 100

REALIZED_PAYMENT_STATES = ("paid", "partially_refunded", "refunded")
INVENTORY_SALE_PAYMENT_STATES = ("paid", "partially_refunded")

REVENUE_MIN_BASELINE_ORDERS = 5
REVENUE_MIN_BASELINE_NET = Decimal("500.00")
REVENUE_MIN_ABSOLUTE_DECLINE = Decimal("200.00")
REVENUE_DECLINE_RATIO = Decimal("0.30")

PRODUCT_MIN_BASELINE_ORDERS = 3
PRODUCT_MIN_BASELINE_UNITS = 8
PRODUCT_MIN_BASELINE_REVENUE = Decimal("100.00")
PRODUCT_MIN_UNIT_DECLINE = 4
PRODUCT_UNIT_DECLINE_RATIO = Decimal("0.40")
PRODUCT_REVENUE_DECLINE_RATIO = Decimal("0.35")

AD_MIN_SPEND_PER_PERIOD = Decimal("100.00")
AD_MIN_CLICKS_PER_PERIOD = 50
AD_MIN_BASELINE_CONVERSIONS = Decimal("3.0000")
AD_ROAS_DECLINE_RATIO = Decimal("0.35")
AD_CONVERSION_RATE_DECLINE_RATIO = Decimal("0.40")

REFUND_MIN_ORDERS_PER_PERIOD = 5
REFUND_MIN_REVENUE_PER_PERIOD = Decimal("500.00")
REFUND_MIN_RECENT_COUNT = 2
REFUND_MIN_RECENT_AMOUNT = Decimal("100.00")
REFUND_MIN_RECENT_RATE = Decimal("0.10")
REFUND_MIN_RATE_INCREASE = Decimal("0.05")
REFUND_RATE_MULTIPLIER = Decimal("2.00")

INVENTORY_MIN_ORDER_COUNT = 3
INVENTORY_MIN_UNITS_SOLD = 7
INVENTORY_MAX_DAYS_COVER = Decimal("7.00")

REPEAT_PURCHASE_MIN_ORDERS = 3
REPEAT_PURCHASE_MIN_INTERVALS = 2
REPEAT_PURCHASE_MIN_CADENCE_DAYS = Decimal("1.00")
REPEAT_PURCHASE_OVERDUE_RATIO = Decimal("1.50")
REPEAT_PURCHASE_MAX_HISTORY_ORDERS = 50

HIGH_VALUE_LOOKBACK_DAYS = 365
HIGH_VALUE_MIN_COHORT_SIZE = 10
HIGH_VALUE_MIN_PERCENTILE = Decimal("0.800")

CUSTOMER_VALUE_COMPARISON_DAYS = 28
CUSTOMER_VALUE_MIN_BASELINE_ORDERS = 3
CUSTOMER_VALUE_MIN_ORDER_DECLINE = 1
CUSTOMER_VALUE_REVENUE_DECLINE_RATIO = Decimal("0.40")
CUSTOMER_VALUE_ORDER_DECLINE_RATIO = Decimal("0.34")

AFFINITY_LOOKBACK_DAYS = 180
AFFINITY_MIN_ELIGIBLE_ORDERS = 20
AFFINITY_MIN_SOURCE_ORDERS = 5
AFFINITY_MIN_TARGET_ORDERS = 3
AFFINITY_MIN_PAIR_ORDERS = 3
AFFINITY_MIN_DIRECTIONAL_CONFIDENCE = Decimal("0.30")
AFFINITY_MIN_LIFT = Decimal("1.25")
AFFINITY_MAX_DIRECTION_CANDIDATES = 12
AFFINITY_CUSTOMERS_PER_DIRECTION = 5
AFFINITY_SELLABLE_AVAILABILITY = ("in_stock", "preorder", "backorder")

CHECKOUT_ABANDONMENT_LOOKBACK_DAYS = 14
CHECKOUT_ABANDONMENT_GRACE_HOURS = 4

PRODUCT_INTEREST_LOOKBACK_DAYS = 14
PRODUCT_INTEREST_MIN_EVENTS = 3
PRODUCT_INTEREST_MIN_ACTIVE_DAYS = 2


@dataclass(frozen=True, slots=True)
class _ComparisonWindow:
    baseline_start: datetime
    baseline_end: datetime
    recent_start: datetime
    recent_end: datetime
    window_key: str


@dataclass(frozen=True, slots=True)
class _GrowthSignal:
    dedupe_key: str
    title: str
    description: str
    category: str
    source: str
    source_entity_type: str | None
    source_entity_id: UUID | None
    reason: str
    confidence: Decimal
    recommendation: str
    provenance: list[dict[str, object]]
    priority: str = "medium"
    currency: str | None = None
    customer_id: UUID | None = None
    estimated_value: Decimal | None = None
    rank_score: Decimal = Decimal("0.500")


_GROWTH_PRIORITY_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "urgent": 3,
}


def _rank_business_growth_signals(
    detector_results: tuple[list[_GrowthSignal], ...],
) -> list[_GrowthSignal]:
    """Globally rank bounded detector signals without comparing raw currencies."""
    candidates = [
        signal
        for detector_signals in detector_results
        for signal in detector_signals[:MAX_OPPORTUNITIES_PER_DETECTOR]
    ]
    candidates.sort(
        key=lambda signal: (
            -_GROWTH_PRIORITY_RANK.get(signal.priority, 0),
            -signal.rank_score,
            -signal.confidence,
            signal.dedupe_key,
        )
    )
    return candidates[:MAX_BUSINESS_GROWTH_OPPORTUNITIES]


async def generate_bounded_content_plan(
    session: AsyncSession,
    *,
    business_id: UUID,
    run_id: UUID,
    provider: AIAgentProvider | None,
    now: datetime | None = None,
) -> MarketingAutomationRun:
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    run = await get_marketing_run(
        session, business_id=business_id, run_id=run_id, lock=True
    )
    if run.run_type != "content_plan":
        raise AutomationIntelligencePersistenceError("marketing_run_type_invalid")
    if run.status in {"completed", "provider_unavailable", "blocked_entitlement"}:
        return run
    try:
        await require_feature(session, business_id=business_id, key="marketing_cmo")
    except BillingEntitlementError:
        _finish(run, "blocked_entitlement", instant, "feature_not_entitled")
        await _flush(session)
        return run
    if provider is None:
        _finish(run, "provider_unavailable", instant, "provider_unavailable")
        await _flush(session)
        return run
    run.status = "running"
    run.started_at = run.started_at or instant
    run.failure_code = None
    await _flush(session)

    proposal_key = f"content-plan:{run.id}:0"
    try:
        existing = await session.scalar(select(MarketingContent).where(
            MarketingContent.business_id == business_id,
            MarketingContent.proposal_key == proposal_key,
        ))
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("content_plan_context_failed") from None
    if existing is not None:
        run.proposal_count = 0
        _finish(run, "completed", instant, None)
        await _flush(session)
        return run
    try:
        manifest = await build_business_brain_manifest(session, business_id)
    except BusinessBrainAssemblyError:
        raise AutomationIntelligencePersistenceError(
            "brain_manifest_unavailable"
        ) from None

    lookback_start = datetime.combine(run.window_start, time.min, tzinfo=UTC) - _lookback()
    try:
        business = await session.scalar(select(Business).where(Business.id == business_id))
        connected = list((await session.scalars(select(
            IntegrationConnection.connector_type
        ).where(
            IntegrationConnection.business_id == business_id,
            IntegrationConnection.status == "connected",
        ).order_by(IntegrationConnection.connector_type))).all())
        observations = list((await session.scalars(
            select(CompetitorObservation).where(
                CompetitorObservation.business_id == business_id,
                CompetitorObservation.observed_at >= lookback_start,
            ).order_by(
                CompetitorObservation.observed_at.desc(),
                CompetitorObservation.id,
            ).limit(3)
        )).all())
        trends = list((await session.scalars(
            select(MarketingTrend).where(
                MarketingTrend.business_id == business_id,
                MarketingTrend.status.in_(("detected", "reviewed")),
                MarketingTrend.observed_at >= lookback_start,
            ).order_by(
                MarketingTrend.relevance_score.desc(),
                MarketingTrend.observed_at.desc(),
                MarketingTrend.id,
            ).limit(3)
        )).all())
        performance = list((await session.scalars(
            select(MarketingPerformance).where(
                MarketingPerformance.business_id == business_id,
                MarketingPerformance.period_end >= run.window_start - _lookback(),
            ).order_by(
                MarketingPerformance.period_end.desc(),
                MarketingPerformance.id,
            ).limit(3)
        )).all())
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("content_plan_context_failed") from None
    if business is None or business.id != business_id:
        raise AutomationIntelligencePersistenceError("business_not_found")

    channel = _content_channel(connected)
    industry = get_business_industry(business.business_type)
    guardrail = _industry_guardrail(business.business_type)
    operational_evidence = _content_operational_evidence(
        observations=observations,
        trends=trends,
        performance=performance,
    )
    evidence_ledger = "\n".join(
        f"- {item['source_id']}: {str(item['summary'])[:180]}"
        for item in operational_evidence
    ) or "- none: no recent operational evidence was available"
    task = (
        f"Prepare one review-ready {channel} content proposal for the week "
        f"{run.window_start.isoformat()} through {run.window_end.isoformat()}. "
        "Use Business Brain and only the following bounded operational evidence where relevant:\n"
        f"{evidence_ledger}\n"
        "Return recommendations as an empty list and put a single JSON object in summary with "
        "exactly these named fields: title, body, cta, creative_brief, recommended_channel, "
        "generation_reasoning, evidence_source_ids. evidence_source_ids must be selected only "
        "from the identifiers above, and recommended_channel must match the requested channel. "
        "Do not return workflow status fields and do not publish anything. "
        f"{guardrail}"
    )
    try:
        execution_result = await _execute_cmo(
            session, business_id, task, provider
        )
        proposal = ScheduledContentProposal.model_validate_json(
            execution_result.output.summary
        )
    except (MarketingAIError, ValidationError):
        raise AutomationIntelligenceProviderError("content_plan_provider_failed") from None
    if proposal.recommended_channel != channel:
        raise AutomationIntelligenceProviderError("content_plan_provider_failed")
    allowed_evidence_ids = {
        str(item["source_id"]) for item in operational_evidence
    }
    if not set(proposal.evidence_source_ids).issubset(allowed_evidence_ids):
        raise AutomationIntelligenceProviderError("content_plan_provider_failed")
    root_id = uuid4()
    selected_evidence = [
        {**item, "provenance_role": "provider_cited"}
        for item in operational_evidence
        if str(item["source_id"]) in proposal.evidence_source_ids
    ]
    context_evidence = {
        "classification": "trusted_context_assembly",
        "source_type": "business_brain_and_permitted_memory",
        "source_id": execution_result.context_revision,
        "summary": (
            f"Runtime assembled {execution_result.business_brain_source_count} Business Brain "
            f"and {execution_result.memory_source_count} permitted memory sources."
        ),
        "business_brain_revision": manifest.revision,
        "provenance_role": "provided_to_model",
    }
    content = MarketingContent(
        id=root_id,
        business_id=business_id,
        campaign_id=None,
        channel=channel,
        content_type="social_post" if channel in {"instagram", "facebook", "linkedin", "tiktok"} else "blog_draft",
        title=proposal.title,
        body=proposal.body,
        cta=proposal.cta,
        language=business.locale if _valid_locale(business.locale) else "en",
        status="review",
        ai_generated=True,
        version=1,
        parent_content_id=None,
        root_content_id=root_id,
        created_by_user_id=None,
        proposal_key=proposal_key,
        creative_brief=proposal.creative_brief,
        generation_reasoning=proposal.generation_reasoning,
        recommended_for=f"Weekly {industry.label if industry else business.business_type} content plan"[:500],
        source_evidence=[context_evidence, *selected_evidence],
    )
    session.add(content)
    session.add(Notification(
        business_id=business_id,
        recipient_user_id=None,
        category="content_review",
        title="AI weekly content proposal ready",
        message=f"Review “{content.title}”. It has not been published.",
        priority="normal",
        read=False,
        related_entity_type="marketing_content",
        related_entity_id=content.id,
    ))
    run.proposal_count = 1
    _finish(run, "completed", instant, None)
    await _flush(session)
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=None,
        event_type="marketing.content_plan_generated",
        entity_type="marketing_automation_run",
        entity_id=run.id,
        summary="Generated one bounded content proposal for review; nothing was published.",
    )
    return run


async def analyze_bounded_campaign_opportunities(
    session: AsyncSession,
    *,
    business_id: UUID,
    run_id: UUID,
    now: datetime | None = None,
) -> MarketingAutomationRun:
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    run = await get_marketing_run(
        session, business_id=business_id, run_id=run_id, lock=True
    )
    if run.run_type not in {"campaign_opportunities", "business_growth"}:
        raise AutomationIntelligencePersistenceError("marketing_run_type_invalid")
    if run.status in {"completed", "blocked_entitlement"}:
        return run
    if run.run_type == "business_growth":
        return await _analyze_bounded_business_growth(
            session,
            business_id=business_id,
            run=run,
            instant=instant,
        )
    try:
        await require_feature(session, business_id=business_id, key="campaigns")
    except BillingEntitlementError:
        _finish(run, "blocked_entitlement", instant, "feature_not_entitled")
        await _flush(session)
        return run
    run.status = "running"
    run.started_at = run.started_at or instant
    start_at = datetime.combine(run.window_start, time.min, tzinfo=UTC) - _lookback()
    try:
        observations = list((await session.execute(
            select(CompetitorObservation, Competitor)
            .join(
                Competitor,
                (Competitor.id == CompetitorObservation.competitor_id)
                & (Competitor.business_id == CompetitorObservation.business_id),
            )
            .where(
                CompetitorObservation.business_id == business_id,
                CompetitorObservation.observed_at >= start_at,
            )
            .order_by(CompetitorObservation.observed_at.desc(), CompetitorObservation.id)
            .limit(5)
        )).all())
        trends = list((await session.scalars(
            select(MarketingTrend).where(
                MarketingTrend.business_id == business_id,
                MarketingTrend.status.in_(("detected", "reviewed")),
                MarketingTrend.observed_at >= start_at,
            ).order_by(
                MarketingTrend.relevance_score.desc(),
                MarketingTrend.observed_at.desc(),
                MarketingTrend.id,
            ).limit(5)
        )).all())
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("opportunity_context_failed") from None

    created = 0
    for observation, competitor in observations:
        key = f"campaign-opportunity:competitor:{observation.id}:{run.window_start}"
        created += int(await _create_opportunity_if_missing(
            session,
            business_id=business_id,
            dedupe_key=key,
            title=f"Respond to {competitor.name}: {observation.title}"[:180],
            description=(
                f"Sourced public competitor signal: {observation.summary} "
                "Review a differentiated campaign or content response; no external action has occurred."
            )[:3000],
            category="competitor_insight",
            source="competitor",
            source_entity_type="competitor_observation",
            source_entity_id=observation.id,
            reason="A recent sourced competitor observation may affect positioning.",
            confidence=None,
            recommendation="Review a differentiated campaign and content proposal.",
            provenance=[{
                "source_type": observation.source_type,
                "source_id": str(observation.id),
                "source_reference": observation.source_reference,
                "observed_at": observation.observed_at.isoformat(),
            }],
        ))
    for trend in trends:
        key = f"campaign-opportunity:trend:{trend.id}:{run.window_start}"
        created += int(await _create_opportunity_if_missing(
            session,
            business_id=business_id,
            dedupe_key=key,
            title=f"Campaign opportunity: {trend.title}"[:180],
            description=(
                f"Sourced trend signal: {trend.description} Review an evidence-backed campaign proposal; "
                "no performance or external execution is implied."
            )[:3000],
            category="trend_opportunity",
            source="trend",
            source_entity_type="marketing_trend",
            source_entity_id=trend.id,
            reason="A relevant sourced trend is available for campaign planning.",
            confidence=trend.confidence,
            recommendation="Ask the AI CMO to prepare a campaign proposal for review.",
            provenance=[{
                "source_type": trend.source,
                "source_id": str(trend.id),
                "source_reference": trend.source_reference,
                "observed_at": trend.observed_at.isoformat(),
            }],
        ))
    run.proposal_count = created
    _finish(run, "completed", instant, None)
    await _flush(session)
    return run


async def _analyze_bounded_business_growth(
    session: AsyncSession,
    *,
    business_id: UUID,
    run: MarketingAutomationRun,
    instant: datetime,
) -> MarketingAutomationRun:
    """Run bounded, deterministic commerce detectors without taking external action."""
    try:
        await require_feature(session, business_id=business_id, key="campaigns")
    except BillingEntitlementError:
        _finish(run, "blocked_entitlement", instant, "feature_not_entitled")
        await _flush(session)
        return run

    run.status = "running"
    run.started_at = run.started_at or instant
    run.failure_code = None
    await _flush(session)

    try:
        business_type = await session.scalar(
            select(Business.business_type).where(Business.id == business_id)
        )
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError(
            "business_growth_context_failed"
        ) from None
    if business_type is None:
        raise AutomationIntelligencePersistenceError("business_not_found")

    industry = get_business_industry(business_type)
    if industry is None or industry.group != "commerce":
        run.proposal_count = 0
        _finish(run, "completed", instant, None)
        await _flush(session)
        return run

    window = _business_growth_comparison_window(run)
    try:
        revenue_signals = await _detect_revenue_declines(
            session, business_id=business_id, window=window
        )
        product_signals = await _detect_product_demand_declines(
            session, business_id=business_id, window=window
        )
        advertising_signals = await _detect_advertising_inefficiency(
            session, business_id=business_id, window=window
        )
        refund_signals = await _detect_refund_anomalies(
            session, business_id=business_id, window=window
        )
        inventory_signals = await _detect_inventory_risks(
            session, business_id=business_id, window=window
        )

        repeat_candidates = await _detect_repeat_purchase_due(
            session,
            business_id=business_id,
            window=window,
            limit=MAX_DETECTOR_CANDIDATES,
        )
        high_value_signals = await _detect_high_value_customer_at_risk(
            session,
            business_id=business_id,
            window=window,
            cadence_candidates=repeat_candidates,
        )
        customer_value_signals = await _detect_customer_value_declines(
            session,
            business_id=business_id,
            window=window,
        )
        product_affinity_signals = await _detect_product_affinity_cross_sell(
            session,
            business_id=business_id,
            window=window,
        )
        checkout_recovery_signals = (
            await _detect_checkout_abandonment_recovery(
                session,
                business_id=business_id,
                window=window,
            )
        )
        product_interest_signals = await _detect_repeated_product_interest(
            session,
            business_id=business_id,
            window=window,
        )

        promoted_pairs = {
            (signal.customer_id, signal.currency)
            for signal in high_value_signals
            if signal.customer_id is not None
        }
        repeat_signals = [
            signal
            for signal in repeat_candidates
            if (signal.customer_id, signal.currency) not in promoted_pairs
        ][:MAX_OPPORTUNITIES_PER_DETECTOR]

        detector_results = (
            revenue_signals,
            product_signals,
            advertising_signals,
            refund_signals,
            inventory_signals,
            repeat_signals,
            high_value_signals,
            customer_value_signals,
            product_affinity_signals,
            checkout_recovery_signals,
            product_interest_signals,
        )
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError(
            "business_growth_context_failed"
        ) from None

    signals = _rank_business_growth_signals(detector_results)
    created = 0
    for signal in signals:
        created += int(await _create_opportunity_if_missing(
            session,
            business_id=business_id,
            dedupe_key=signal.dedupe_key,
            title=signal.title,
            description=signal.description,
            category=signal.category,
            source=signal.source,
            source_entity_type=signal.source_entity_type,
            source_entity_id=signal.source_entity_id,
            reason=signal.reason,
            confidence=signal.confidence,
            recommendation=signal.recommendation,
            provenance=signal.provenance,
            priority=signal.priority,
            currency=signal.currency,
            customer_id=signal.customer_id,
            estimated_value=signal.estimated_value,
            suggested_action="analyze_business_opportunity",
            enqueue_initial_analysis=True,
        ))

    run.proposal_count = created
    _finish(run, "completed", instant, None)
    await _flush(session)
    return run


def _business_growth_comparison_window(
    run: MarketingAutomationRun,
) -> _ComparisonWindow:
    recent_end = datetime.combine(
        run.window_end + timedelta(days=1), time.min, tzinfo=UTC
    )
    recent_start = recent_end - timedelta(days=BUSINESS_GROWTH_LOOKBACK_DAYS)
    baseline_end = recent_start
    baseline_start = baseline_end - timedelta(days=BUSINESS_GROWTH_LOOKBACK_DAYS)
    return _ComparisonWindow(
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        recent_start=recent_start,
        recent_end=recent_end,
        window_key=run.window_start.isoformat(),
    )


def _authoritative_order_occurred_at():
    """Manual orders use local creation time; provider orders require provider time."""
    return case(
        (Order.source == "manual", Order.created_at),
        else_=Order.provider_created_at,
    )


def _retained_order_revenue():
    return case(
        (Order.payment_status == "refunded", Decimal("0.00")),
        else_=func.greatest(
            Order.total - Order.refunded_amount,
            Decimal("0.00"),
        ),
    )


def _sellable_product_predicates(item) -> tuple[object, ...]:
    """Return the shared authoritative boundary for recommendable products."""
    return (
        item.item_type == "product",
        item.status == "active",
        item.published.is_(True),
        item.availability.in_(AFFINITY_SELLABLE_AVAILABILITY),
        or_(
            item.availability.in_(("preorder", "backorder")),
            item.inventory_quantity.is_(None),
            item.inventory_quantity > 0,
        ),
    )


def _is_sellable_product_state(
    *,
    item_type: str,
    status: str,
    published: bool,
    availability: str,
    inventory_quantity: int | None,
) -> bool:
    if (
        item_type != "product"
        or status != "active"
        or not published
        or availability not in AFFINITY_SELLABLE_AVAILABILITY
    ):
        return False
    return (
        availability in {"preorder", "backorder"}
        or inventory_quantity is None
        or inventory_quantity > 0
    )


def _window_provenance(
    window: _ComparisonWindow,
) -> dict[str, object]:
    return {
        "window_start": window.recent_start.isoformat(),
        "window_end": window.recent_end.isoformat(),
        "window_end_inclusive": False,
        "baseline_start": window.baseline_start.isoformat(),
        "baseline_end": window.baseline_end.isoformat(),
        "baseline_end_inclusive": False,
    }


def _as_decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _confidence(
    *, base: Decimal, evidence: Decimal, severity: Decimal
) -> Decimal:
    value = min(Decimal("0.990"), base + evidence + severity)
    return value.quantize(Decimal("0.001"))


async def _detect_revenue_declines(
    session: AsyncSession,
    *,
    business_id: UUID,
    window: _ComparisonWindow,
) -> list[_GrowthSignal]:
    occurred_at = _authoritative_order_occurred_at()
    net_revenue = _retained_order_revenue()
    recent = and_(
        occurred_at >= window.recent_start,
        occurred_at < window.recent_end,
    )
    baseline = and_(
        occurred_at >= window.baseline_start,
        occurred_at < window.baseline_end,
    )
    recent_order_count = func.count(Order.id).filter(recent).label(
        "recent_order_count"
    )
    baseline_order_count = func.count(Order.id).filter(baseline).label(
        "baseline_order_count"
    )
    recent_net_revenue = func.coalesce(
        func.sum(net_revenue).filter(recent), Decimal("0.00")
    ).label("recent_net_revenue")
    baseline_net_revenue = func.coalesce(
        func.sum(net_revenue).filter(baseline), Decimal("0.00")
    ).label("baseline_net_revenue")
    rows = (await session.execute(
        select(
            Order.currency,
            recent_order_count,
            baseline_order_count,
            recent_net_revenue,
            baseline_net_revenue,
        )
        .where(
            Order.business_id == business_id,
            Order.status != "canceled",
            Order.payment_status.in_(REALIZED_PAYMENT_STATES),
            occurred_at >= window.baseline_start,
            occurred_at < window.recent_end,
        )
        .group_by(Order.currency)
        .having(baseline_order_count >= REVENUE_MIN_BASELINE_ORDERS)
        .order_by(
            (baseline_net_revenue - recent_net_revenue).desc(),
            Order.currency,
        )
        .limit(MAX_DETECTOR_CANDIDATES)
    )).all()

    ranked: list[tuple[Decimal, _GrowthSignal]] = []
    for row in rows:
        baseline_orders = int(row.baseline_order_count or 0)
        recent_orders = int(row.recent_order_count or 0)
        baseline_net = _as_decimal(row.baseline_net_revenue)
        recent_net = _as_decimal(row.recent_net_revenue)
        if baseline_orders < REVENUE_MIN_BASELINE_ORDERS:
            continue
        if baseline_net < REVENUE_MIN_BASELINE_NET or recent_net >= baseline_net:
            continue
        decline_amount = baseline_net - recent_net
        decline_ratio = decline_amount / baseline_net
        if (
            decline_amount < REVENUE_MIN_ABSOLUTE_DECLINE
            or decline_ratio < REVENUE_DECLINE_RATIO
        ):
            continue
        currency = str(row.currency)
        evidence_boost = min(
            Decimal("0.150"),
            Decimal(max(0, baseline_orders - REVENUE_MIN_BASELINE_ORDERS))
            * Decimal("0.015"),
        )
        severity_boost = min(
            Decimal("0.190"),
            (decline_ratio - REVENUE_DECLINE_RATIO) * Decimal("0.40"),
        )
        signal = _GrowthSignal(
            dedupe_key=(
                f"business-growth:revenue-decline:{currency}:{window.window_key}"
            ),
            title=f"Paid-order revenue decline detected ({currency})",
            description=(
                f"Observed retained paid-order revenue in {currency} declined "
                f"{decline_ratio:.1%} versus the immediately preceding comparable "
                "period. This is a first-party signal, not a causal conclusion."
            ),
            category="revenue_decline",
            source="commerce",
            source_entity_type=None,
            source_entity_id=None,
            reason=(
                f"Retained revenue fell from {baseline_net:.2f} {currency} across "
                f"{baseline_orders} eligible orders to {recent_net:.2f} {currency} "
                f"across {recent_orders} eligible orders."
            ),
            confidence=_confidence(
                base=Decimal("0.650"),
                evidence=evidence_boost,
                severity=severity_boost,
            ),
            recommendation=(
                "Ask the Copilot to analyze the observed decline and propose "
                "evidence-backed recovery options for review."
            ),
            provenance=[{
                "classification": "first_party_observed",
                "detector": "revenue_decline",
                **_window_provenance(window),
                "currency": currency,
                "recent_order_count": recent_orders,
                "baseline_order_count": baseline_orders,
                "recent_net_revenue": str(recent_net),
                "baseline_net_revenue": str(baseline_net),
                "absolute_decline": str(decline_amount),
                "decline_ratio": str(decline_ratio),
                "order_timestamp_policy": (
                    "manual_created_at_else_provider_created_at_required"
                ),
                "eligible_payment_states": list(REALIZED_PAYMENT_STATES),
                "refund_treatment": "refunded_orders_zero_else_total_minus_refunded_amount",
            }],
            priority="high" if decline_ratio >= Decimal("0.50") else "medium",
            currency=currency,
        )
        ranked.append((decline_amount, signal))
    ranked.sort(key=lambda item: (-item[0], item[1].dedupe_key))
    return [item[1] for item in ranked[:MAX_OPPORTUNITIES_PER_DETECTOR]]


async def _detect_product_demand_declines(
    session: AsyncSession,
    *,
    business_id: UUID,
    window: _ComparisonWindow,
) -> list[_GrowthSignal]:
    occurred_at = _authoritative_order_occurred_at()
    recent = and_(
        occurred_at >= window.recent_start,
        occurred_at < window.recent_end,
    )
    baseline = and_(
        occurred_at >= window.baseline_start,
        occurred_at < window.baseline_end,
    )
    recorded_line_revenue = func.greatest(
        OrderLineItem.unit_price * OrderLineItem.quantity
        - OrderLineItem.discount_amount,
        Decimal("0.00"),
    )
    recent_orders = func.count(func.distinct(Order.id)).filter(recent).label(
        "recent_order_count"
    )
    baseline_orders = func.count(func.distinct(Order.id)).filter(baseline).label(
        "baseline_order_count"
    )
    recent_units = func.coalesce(
        func.sum(OrderLineItem.quantity).filter(recent), 0
    ).label("recent_units")
    baseline_units = func.coalesce(
        func.sum(OrderLineItem.quantity).filter(baseline), 0
    ).label("baseline_units")
    recent_revenue = func.coalesce(
        func.sum(recorded_line_revenue).filter(recent), Decimal("0.00")
    ).label("recent_recorded_revenue")
    baseline_revenue = func.coalesce(
        func.sum(recorded_line_revenue).filter(baseline), Decimal("0.00")
    ).label("baseline_recorded_revenue")
    rows = (await session.execute(
        select(
            CatalogItem.id.label("catalog_item_id"),
            CatalogItem.name.label("catalog_item_name"),
            Order.currency,
            recent_orders,
            baseline_orders,
            recent_units,
            baseline_units,
            recent_revenue,
            baseline_revenue,
        )
        .join(
            OrderLineItem,
            (OrderLineItem.order_id == Order.id)
            & (OrderLineItem.business_id == Order.business_id),
        )
        .join(
            CatalogItem,
            (CatalogItem.id == OrderLineItem.catalog_item_id)
            & (CatalogItem.business_id == OrderLineItem.business_id),
        )
        .where(
            Order.business_id == business_id,
            OrderLineItem.business_id == business_id,
            CatalogItem.business_id == business_id,
            CatalogItem.item_type == "product",
            Order.status != "canceled",
            Order.payment_status.in_(REALIZED_PAYMENT_STATES),
            occurred_at >= window.baseline_start,
            occurred_at < window.recent_end,
        )
        .group_by(
            CatalogItem.id,
            CatalogItem.name,
            Order.currency,
        )
        .having(
            baseline_orders >= PRODUCT_MIN_BASELINE_ORDERS,
            baseline_units >= PRODUCT_MIN_BASELINE_UNITS,
        )
        .order_by(
            (baseline_revenue - recent_revenue).desc(),
            CatalogItem.id,
            Order.currency,
        )
        .limit(MAX_DETECTOR_CANDIDATES)
    )).all()

    ranked: list[tuple[Decimal, int, _GrowthSignal]] = []
    for row in rows:
        baseline_order_count = int(row.baseline_order_count or 0)
        recent_order_count = int(row.recent_order_count or 0)
        baseline_unit_count = int(row.baseline_units or 0)
        recent_unit_count = int(row.recent_units or 0)
        baseline_recorded = _as_decimal(row.baseline_recorded_revenue)
        recent_recorded = _as_decimal(row.recent_recorded_revenue)
        if (
            baseline_order_count < PRODUCT_MIN_BASELINE_ORDERS
            or baseline_unit_count < PRODUCT_MIN_BASELINE_UNITS
            or baseline_recorded < PRODUCT_MIN_BASELINE_REVENUE
            or recent_unit_count >= baseline_unit_count
            or recent_recorded >= baseline_recorded
        ):
            continue
        unit_decline = baseline_unit_count - recent_unit_count
        unit_decline_ratio = Decimal(unit_decline) / Decimal(baseline_unit_count)
        revenue_decline = baseline_recorded - recent_recorded
        revenue_decline_ratio = revenue_decline / baseline_recorded
        if (
            unit_decline < PRODUCT_MIN_UNIT_DECLINE
            or unit_decline_ratio < PRODUCT_UNIT_DECLINE_RATIO
            or revenue_decline_ratio < PRODUCT_REVENUE_DECLINE_RATIO
        ):
            continue
        item_id = row.catalog_item_id
        item_name = str(row.catalog_item_name)
        currency = str(row.currency)
        signal = _GrowthSignal(
            dedupe_key=(
                "business-growth:product-demand-decline:"
                f"{item_id}:{currency}:{window.window_key}"
            ),
            title=f"Product demand decline: {item_name}"[:180],
            description=(
                f"Observed paid-order units for {item_name} declined "
                f"{unit_decline_ratio:.1%}, while recorded line revenue in "
                f"{currency} declined {revenue_decline_ratio:.1%}, versus the "
                "preceding comparable period."
            )[:3000],
            category="product_demand_decline",
            source="commerce",
            source_entity_type="catalog_item",
            source_entity_id=item_id,
            reason=(
                f"Observed units changed from {baseline_unit_count} across "
                f"{baseline_order_count} orders to {recent_unit_count} across "
                f"{recent_order_count} orders; recorded line revenue changed "
                f"from {baseline_recorded:.2f} to {recent_recorded:.2f} {currency}."
            ),
            confidence=_confidence(
                base=Decimal("0.640"),
                evidence=min(
                    Decimal("0.160"),
                    Decimal(max(0, baseline_order_count - PRODUCT_MIN_BASELINE_ORDERS))
                    * Decimal("0.020"),
                ),
                severity=min(
                    Decimal("0.180"),
                    (unit_decline_ratio - PRODUCT_UNIT_DECLINE_RATIO)
                    * Decimal("0.35"),
                ),
            ),
            recommendation=(
                "Ask the Copilot to review product positioning, availability, "
                "pricing, and campaign evidence before proposing a response."
            ),
            provenance=[{
                "classification": "first_party_observed",
                "detector": "product_demand_decline",
                **_window_provenance(window),
                "catalog_item_id": str(item_id),
                "currency": currency,
                "recent_order_count": recent_order_count,
                "baseline_order_count": baseline_order_count,
                "recent_units": recent_unit_count,
                "baseline_units": baseline_unit_count,
                "recent_recorded_line_revenue": str(recent_recorded),
                "baseline_recorded_line_revenue": str(baseline_recorded),
                "unit_decline_ratio": str(unit_decline_ratio),
                "recorded_revenue_decline_ratio": str(revenue_decline_ratio),
                "order_timestamp_policy": (
                    "manual_created_at_else_provider_created_at_required"
                ),
            }],
            priority="high" if unit_decline_ratio >= Decimal("0.60") else "medium",
            currency=currency,
        )
        ranked.append((revenue_decline, unit_decline, signal))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2].dedupe_key))
    return [item[2] for item in ranked[:MAX_OPPORTUNITIES_PER_DETECTOR]]


async def _detect_advertising_inefficiency(
    session: AsyncSession,
    *,
    business_id: UUID,
    window: _ComparisonWindow,
) -> list[_GrowthSignal]:
    recent_start = window.recent_start.date()
    recent_end = (window.recent_end - timedelta(microseconds=1)).date()
    baseline_start = window.baseline_start.date()
    baseline_end = (window.baseline_end - timedelta(microseconds=1)).date()
    recent = and_(
        ProductCampaignPerformance.period_start >= recent_start,
        ProductCampaignPerformance.period_end <= recent_end,
    )
    baseline = and_(
        ProductCampaignPerformance.period_start >= baseline_start,
        ProductCampaignPerformance.period_end <= baseline_end,
    )

    def metric_sum(column, label: str, condition):
        return func.coalesce(func.sum(column).filter(condition), 0).label(label)

    recent_spend = metric_sum(
        ProductCampaignPerformance.spend, "recent_spend", recent
    )
    baseline_spend = metric_sum(
        ProductCampaignPerformance.spend, "baseline_spend", baseline
    )
    recent_clicks = metric_sum(
        ProductCampaignPerformance.clicks, "recent_clicks", recent
    )
    baseline_clicks = metric_sum(
        ProductCampaignPerformance.clicks, "baseline_clicks", baseline
    )
    recent_conversions = metric_sum(
        ProductCampaignPerformance.conversions, "recent_conversions", recent
    )
    baseline_conversions = metric_sum(
        ProductCampaignPerformance.conversions, "baseline_conversions", baseline
    )
    recent_value = metric_sum(
        ProductCampaignPerformance.conversion_value,
        "recent_conversion_value",
        recent,
    )
    baseline_value = metric_sum(
        ProductCampaignPerformance.conversion_value,
        "baseline_conversion_value",
        baseline,
    )
    recent_slices = func.count(ProductCampaignPerformance.id).filter(recent).label(
        "recent_slice_count"
    )
    baseline_slices = func.count(ProductCampaignPerformance.id).filter(
        baseline
    ).label("baseline_slice_count")

    rows = (await session.execute(
        select(
            ProductCampaignPerformance.provider,
            ProductCampaignPerformance.campaign_id,
            ProductCampaignPerformance.catalog_item_id,
            Campaign.currency,
            CatalogItem.name.label("catalog_item_name"),
            recent_spend,
            baseline_spend,
            recent_clicks,
            baseline_clicks,
            recent_conversions,
            baseline_conversions,
            recent_value,
            baseline_value,
            recent_slices,
            baseline_slices,
        )
        .join(
            Campaign,
            (Campaign.id == ProductCampaignPerformance.campaign_id)
            & (Campaign.business_id == ProductCampaignPerformance.business_id),
        )
        .join(
            CatalogItem,
            (CatalogItem.id == ProductCampaignPerformance.catalog_item_id)
            & (CatalogItem.business_id == ProductCampaignPerformance.business_id),
        )
        .where(
            ProductCampaignPerformance.business_id == business_id,
            Campaign.business_id == business_id,
            CatalogItem.business_id == business_id,
            ProductCampaignPerformance.attribution_class == "provider_attributed",
            ProductCampaignPerformance.period_start >= baseline_start,
            ProductCampaignPerformance.period_end <= recent_end,
        )
        .group_by(
            ProductCampaignPerformance.provider,
            ProductCampaignPerformance.campaign_id,
            ProductCampaignPerformance.catalog_item_id,
            Campaign.currency,
            CatalogItem.name,
        )
        .having(
            baseline_spend >= AD_MIN_SPEND_PER_PERIOD,
            recent_spend >= AD_MIN_SPEND_PER_PERIOD,
            baseline_clicks >= AD_MIN_CLICKS_PER_PERIOD,
            recent_clicks >= AD_MIN_CLICKS_PER_PERIOD,
            baseline_conversions >= AD_MIN_BASELINE_CONVERSIONS,
        )
        .order_by(
            recent_spend.desc(),
            ProductCampaignPerformance.provider,
            ProductCampaignPerformance.campaign_id,
            ProductCampaignPerformance.catalog_item_id,
        )
        .limit(MAX_DETECTOR_CANDIDATES)
    )).all()

    ranked: list[tuple[Decimal, Decimal, _GrowthSignal]] = []
    for row in rows:
        base_spend = _as_decimal(row.baseline_spend)
        current_spend = _as_decimal(row.recent_spend)
        base_clicks = int(row.baseline_clicks or 0)
        current_clicks = int(row.recent_clicks or 0)
        base_conversions = _as_decimal(row.baseline_conversions)
        current_conversions = _as_decimal(row.recent_conversions)
        base_value = _as_decimal(row.baseline_conversion_value)
        current_value = _as_decimal(row.recent_conversion_value)
        if (
            base_spend < AD_MIN_SPEND_PER_PERIOD
            or current_spend < AD_MIN_SPEND_PER_PERIOD
            or base_clicks < AD_MIN_CLICKS_PER_PERIOD
            or current_clicks < AD_MIN_CLICKS_PER_PERIOD
            or base_conversions < AD_MIN_BASELINE_CONVERSIONS
            or base_value <= 0
        ):
            continue
        baseline_roas = base_value / base_spend
        recent_roas = current_value / current_spend
        baseline_conversion_rate = base_conversions / Decimal(base_clicks)
        recent_conversion_rate = current_conversions / Decimal(current_clicks)
        roas_decline = max(
            Decimal("0"),
            Decimal("1") - (recent_roas / baseline_roas),
        )
        conversion_rate_decline = max(
            Decimal("0"),
            Decimal("1")
            - (recent_conversion_rate / baseline_conversion_rate),
        )
        if (
            roas_decline < AD_ROAS_DECLINE_RATIO
            and conversion_rate_decline < AD_CONVERSION_RATE_DECLINE_RATIO
        ):
            continue
        provider = str(row.provider)
        campaign_id = row.campaign_id
        item_id = row.catalog_item_id
        item_name = str(row.catalog_item_name)
        currency = str(row.currency)
        deterioration = max(roas_decline, conversion_rate_decline)
        signal = _GrowthSignal(
            dedupe_key=(
                "business-growth:ad-inefficiency:"
                f"{provider}:{campaign_id}:{item_id}:{currency}:{window.window_key}"
            ),
            title=f"Provider-attributed ad inefficiency: {item_name}"[:180],
            description=(
                f"{provider.title()} provider-attributed performance for "
                f"{item_name} deteriorated versus the preceding comparable "
                "period. This does not claim that advertising caused any "
                "first-party sales outcome."
            )[:3000],
            category="advertising_inefficiency",
            source="provider_attribution",
            source_entity_type="catalog_item",
            source_entity_id=item_id,
            reason=(
                f"Provider-attributed ROAS changed from {baseline_roas:.4f} to "
                f"{recent_roas:.4f}; conversion rate changed from "
                f"{baseline_conversion_rate:.4f} to {recent_conversion_rate:.4f}."
            ),
            confidence=_confidence(
                base=Decimal("0.670"),
                evidence=min(
                    Decimal("0.130"),
                    Decimal(max(0, min(base_clicks, current_clicks) - AD_MIN_CLICKS_PER_PERIOD))
                    * Decimal("0.001"),
                ),
                severity=min(
                    Decimal("0.180"),
                    max(
                        Decimal("0"),
                        deterioration - min(
                            AD_ROAS_DECLINE_RATIO,
                            AD_CONVERSION_RATE_DECLINE_RATIO,
                        ),
                    ) * Decimal("0.35"),
                ),
            ),
            recommendation=(
                "Ask the Copilot to review provider-attributed targeting, "
                "creative, spend, and product evidence before proposing changes."
            ),
            provenance=[{
                "classification": "provider_attributed",
                "detector": "advertising_inefficiency",
                **_window_provenance(window),
                "provider": provider,
                "campaign_id": str(campaign_id),
                "catalog_item_id": str(item_id),
                "currency": currency,
                "recent_slice_count": int(row.recent_slice_count or 0),
                "baseline_slice_count": int(row.baseline_slice_count or 0),
                "recent_spend": str(current_spend),
                "baseline_spend": str(base_spend),
                "recent_clicks": current_clicks,
                "baseline_clicks": base_clicks,
                "recent_conversions": str(current_conversions),
                "baseline_conversions": str(base_conversions),
                "recent_conversion_value": str(current_value),
                "baseline_conversion_value": str(base_value),
                "recent_provider_attributed_roas": str(recent_roas),
                "baseline_provider_attributed_roas": str(baseline_roas),
                "recent_provider_attributed_conversion_rate": str(
                    recent_conversion_rate
                ),
                "baseline_provider_attributed_conversion_rate": str(
                    baseline_conversion_rate
                ),
                "provider_attribution_disclaimer": (
                    "Provider-attributed performance only; no causal claim about sales."
                ),
            }],
            priority="high" if deterioration >= Decimal("0.60") else "medium",
            currency=currency,
        )
        ranked.append((current_spend, deterioration, signal))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2].dedupe_key))
    return [item[2] for item in ranked[:MAX_OPPORTUNITIES_PER_DETECTOR]]


async def _detect_refund_anomalies(
    session: AsyncSession,
    *,
    business_id: UUID,
    window: _ComparisonWindow,
) -> list[_GrowthSignal]:
    occurred_at = _authoritative_order_occurred_at()
    recent_orders_window = and_(
        occurred_at >= window.recent_start,
        occurred_at < window.recent_end,
    )
    baseline_orders_window = and_(
        occurred_at >= window.baseline_start,
        occurred_at < window.baseline_end,
    )
    recent_order_count = func.count(Order.id).filter(
        recent_orders_window
    ).label("recent_order_count")
    baseline_order_count = func.count(Order.id).filter(
        baseline_orders_window
    ).label("baseline_order_count")
    recent_revenue = func.coalesce(
        func.sum(Order.total).filter(recent_orders_window), Decimal("0.00")
    ).label("recent_paid_order_revenue")
    baseline_revenue = func.coalesce(
        func.sum(Order.total).filter(baseline_orders_window), Decimal("0.00")
    ).label("baseline_paid_order_revenue")
    revenue_rows = (await session.execute(
        select(
            Order.currency,
            recent_order_count,
            baseline_order_count,
            recent_revenue,
            baseline_revenue,
        )
        .where(
            Order.business_id == business_id,
            Order.status != "canceled",
            Order.payment_status.in_(REALIZED_PAYMENT_STATES),
            occurred_at >= window.baseline_start,
            occurred_at < window.recent_end,
        )
        .group_by(Order.currency)
        .order_by(baseline_revenue.desc(), Order.currency)
        .limit(MAX_DETECTOR_CANDIDATES)
    )).all()

    recent_refunds_window = and_(
        OrderRefund.occurred_at >= window.recent_start,
        OrderRefund.occurred_at < window.recent_end,
    )
    baseline_refunds_window = and_(
        OrderRefund.occurred_at >= window.baseline_start,
        OrderRefund.occurred_at < window.baseline_end,
    )
    recent_refund_count = func.count(OrderRefund.id).filter(
        recent_refunds_window
    ).label("recent_refund_count")
    baseline_refund_count = func.count(OrderRefund.id).filter(
        baseline_refunds_window
    ).label("baseline_refund_count")
    recent_refund_amount = func.coalesce(
        func.sum(OrderRefund.amount).filter(recent_refunds_window),
        Decimal("0.00"),
    ).label("recent_refund_amount")
    baseline_refund_amount = func.coalesce(
        func.sum(OrderRefund.amount).filter(baseline_refunds_window),
        Decimal("0.00"),
    ).label("baseline_refund_amount")
    refund_rows = (await session.execute(
        select(
            OrderRefund.currency,
            recent_refund_count,
            baseline_refund_count,
            recent_refund_amount,
            baseline_refund_amount,
        )
        .join(
            Order,
            (Order.id == OrderRefund.order_id)
            & (Order.business_id == OrderRefund.business_id),
        )
        .where(
            OrderRefund.business_id == business_id,
            Order.business_id == business_id,
            OrderRefund.currency == Order.currency,
            Order.status != "canceled",
            Order.payment_status.in_(REALIZED_PAYMENT_STATES),
            OrderRefund.occurred_at >= window.baseline_start,
            OrderRefund.occurred_at < window.recent_end,
        )
        .group_by(OrderRefund.currency)
        .order_by(recent_refund_amount.desc(), OrderRefund.currency)
        .limit(MAX_DETECTOR_CANDIDATES)
    )).all()
    refunds_by_currency = {str(row.currency): row for row in refund_rows}

    ranked: list[tuple[Decimal, Decimal, _GrowthSignal]] = []
    for revenue_row in revenue_rows:
        currency = str(revenue_row.currency)
        refund_row = refunds_by_currency.get(currency)
        if refund_row is None:
            continue
        recent_orders = int(revenue_row.recent_order_count or 0)
        baseline_orders = int(revenue_row.baseline_order_count or 0)
        recent_paid_revenue = _as_decimal(
            revenue_row.recent_paid_order_revenue
        )
        baseline_paid_revenue = _as_decimal(
            revenue_row.baseline_paid_order_revenue
        )
        recent_refunds = int(refund_row.recent_refund_count or 0)
        baseline_refunds = int(refund_row.baseline_refund_count or 0)
        recent_amount = _as_decimal(refund_row.recent_refund_amount)
        baseline_amount = _as_decimal(refund_row.baseline_refund_amount)
        if (
            recent_orders < REFUND_MIN_ORDERS_PER_PERIOD
            or baseline_orders < REFUND_MIN_ORDERS_PER_PERIOD
            or recent_paid_revenue < REFUND_MIN_REVENUE_PER_PERIOD
            or baseline_paid_revenue < REFUND_MIN_REVENUE_PER_PERIOD
            or recent_refunds < REFUND_MIN_RECENT_COUNT
            or recent_amount < REFUND_MIN_RECENT_AMOUNT
        ):
            continue
        recent_rate = recent_amount / recent_paid_revenue
        baseline_rate = baseline_amount / baseline_paid_revenue
        rate_increase = recent_rate - baseline_rate
        relative_increase_supported = (
            baseline_rate == 0
            or recent_rate >= baseline_rate * REFUND_RATE_MULTIPLIER
        )
        if (
            recent_rate < REFUND_MIN_RECENT_RATE
            or rate_increase < REFUND_MIN_RATE_INCREASE
            or not relative_increase_supported
        ):
            continue
        signal = _GrowthSignal(
            dedupe_key=(
                f"business-growth:refund-anomaly:{currency}:{window.window_key}"
            ),
            title=f"Refund-rate anomaly detected ({currency})",
            description=(
                f"Observed refunds were {recent_rate:.1%} of eligible paid-order "
                f"revenue in {currency}, up from {baseline_rate:.1%} in the "
                "preceding comparable period."
            ),
            category="refund_anomaly",
            source="commerce",
            source_entity_type=None,
            source_entity_id=None,
            reason=(
                f"Recent refunds totaled {recent_amount:.2f} {currency} across "
                f"{recent_refunds} refunds, compared with {baseline_amount:.2f} "
                f"{currency} across {baseline_refunds} refunds in the baseline."
            ),
            confidence=_confidence(
                base=Decimal("0.660"),
                evidence=min(
                    Decimal("0.140"),
                    Decimal(max(0, recent_refunds - REFUND_MIN_RECENT_COUNT))
                    * Decimal("0.025"),
                ),
                severity=min(
                    Decimal("0.180"),
                    (rate_increase - REFUND_MIN_RATE_INCREASE)
                    * Decimal("0.80"),
                ),
            ),
            recommendation=(
                "Ask the Copilot to analyze refund reasons, affected products, "
                "and fulfillment evidence before proposing corrective actions."
            ),
            provenance=[{
                "classification": "first_party_observed",
                "detector": "refund_anomaly",
                **_window_provenance(window),
                "currency": currency,
                "recent_order_count": recent_orders,
                "baseline_order_count": baseline_orders,
                "recent_paid_order_revenue": str(recent_paid_revenue),
                "baseline_paid_order_revenue": str(baseline_paid_revenue),
                "recent_refund_count": recent_refunds,
                "baseline_refund_count": baseline_refunds,
                "recent_refund_amount": str(recent_amount),
                "baseline_refund_amount": str(baseline_amount),
                "recent_refund_rate": str(recent_rate),
                "baseline_refund_rate": str(baseline_rate),
                "refund_rate_increase": str(rate_increase),
                "refund_timestamp": "order_refunds.occurred_at",
                "revenue_timestamp_policy": (
                    "manual_created_at_else_provider_created_at_required"
                ),
            }],
            priority="high" if recent_rate >= Decimal("0.20") else "medium",
            currency=currency,
        )
        ranked.append((rate_increase, recent_amount, signal))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2].dedupe_key))
    return [item[2] for item in ranked[:MAX_OPPORTUNITIES_PER_DETECTOR]]


async def _detect_inventory_risks(
    session: AsyncSession,
    *,
    business_id: UUID,
    window: _ComparisonWindow,
) -> list[_GrowthSignal]:
    occurred_at = _authoritative_order_occurred_at()
    velocity_start = window.recent_end - timedelta(
        days=INVENTORY_VELOCITY_LOOKBACK_DAYS
    )
    units_sold = func.coalesce(func.sum(OrderLineItem.quantity), 0).label(
        "units_sold"
    )
    order_count = func.count(func.distinct(Order.id)).label("order_count")
    rows = (await session.execute(
        select(
            CatalogItem.id.label("catalog_item_id"),
            CatalogItem.name.label("catalog_item_name"),
            CatalogItem.inventory_quantity,
            units_sold,
            order_count,
        )
        .join(
            OrderLineItem,
            (OrderLineItem.order_id == Order.id)
            & (OrderLineItem.business_id == Order.business_id),
        )
        .join(
            CatalogItem,
            (CatalogItem.id == OrderLineItem.catalog_item_id)
            & (CatalogItem.business_id == OrderLineItem.business_id),
        )
        .where(
            Order.business_id == business_id,
            OrderLineItem.business_id == business_id,
            CatalogItem.business_id == business_id,
            CatalogItem.item_type == "product",
            CatalogItem.status == "active",
            CatalogItem.inventory_quantity.is_not(None),
            Order.status != "canceled",
            Order.payment_status.in_(INVENTORY_SALE_PAYMENT_STATES),
            occurred_at >= velocity_start,
            occurred_at < window.recent_end,
        )
        .group_by(
            CatalogItem.id,
            CatalogItem.name,
            CatalogItem.inventory_quantity,
        )
        .having(
            order_count >= INVENTORY_MIN_ORDER_COUNT,
            units_sold >= INVENTORY_MIN_UNITS_SOLD,
        )
        .order_by(
            CatalogItem.inventory_quantity,
            units_sold.desc(),
            CatalogItem.id,
        )
        .limit(MAX_DETECTOR_CANDIDATES)
    )).all()

    ranked: list[tuple[Decimal, Decimal, _GrowthSignal]] = []
    for row in rows:
        inventory = row.inventory_quantity
        sold = int(row.units_sold or 0)
        orders = int(row.order_count or 0)
        if (
            inventory is None
            or sold < INVENTORY_MIN_UNITS_SOLD
            or orders < INVENTORY_MIN_ORDER_COUNT
        ):
            continue
        daily_velocity = Decimal(sold) / Decimal(
            INVENTORY_VELOCITY_LOOKBACK_DAYS
        )
        if daily_velocity <= 0:
            continue
        days_cover = Decimal(int(inventory)) / daily_velocity
        if days_cover > INVENTORY_MAX_DAYS_COVER:
            continue
        item_id = row.catalog_item_id
        item_name = str(row.catalog_item_name)
        signal = _GrowthSignal(
            dedupe_key=(
                f"business-growth:inventory-risk:{item_id}:{window.window_key}"
            ),
            title=f"Inventory coverage risk: {item_name}"[:180],
            description=(
                f"Known inventory for {item_name} represents an estimated "
                f"{days_cover:.2f} days of cover at the observed "
                f"{INVENTORY_VELOCITY_LOOKBACK_DAYS}-day paid-order sales velocity."
            )[:3000],
            category="inventory_risk",
            source="commerce",
            source_entity_type="catalog_item",
            source_entity_id=item_id,
            reason=(
                f"Known inventory is {int(inventory)} units; {sold} units were "
                f"observed across {orders} eligible paid orders over "
                f"{INVENTORY_VELOCITY_LOOKBACK_DAYS} days."
            ),
            confidence=_confidence(
                base=Decimal("0.680"),
                evidence=min(
                    Decimal("0.140"),
                    Decimal(max(0, orders - INVENTORY_MIN_ORDER_COUNT))
                    * Decimal("0.020"),
                ),
                severity=min(
                    Decimal("0.160"),
                    (INVENTORY_MAX_DAYS_COVER - days_cover)
                    / INVENTORY_MAX_DAYS_COVER
                    * Decimal("0.160"),
                ),
            ),
            recommendation=(
                "Ask the Copilot to review replenishment lead time, current "
                "availability, and demand evidence before proposing action."
            ),
            provenance=[{
                "classification": "first_party_observed",
                "detector": "inventory_risk",
                "window_start": velocity_start.isoformat(),
                "window_end": window.recent_end.isoformat(),
                "window_end_inclusive": False,
                "catalog_item_id": str(item_id),
                "known_inventory_quantity": int(inventory),
                "observed_units_sold": sold,
                "observed_order_count": orders,
                "observed_average_daily_units": str(daily_velocity),
                "estimated_days_of_cover": str(days_cover),
                "inventory_scope": "catalog_item",
                "unknown_inventory_policy": "excluded",
                "order_timestamp_policy": (
                    "manual_created_at_else_provider_created_at_required"
                ),
            }],
            priority="high" if days_cover <= Decimal("3.00") else "medium",
            currency=None,
        )
        ranked.append((days_cover, -daily_velocity, signal))
    ranked.sort(key=lambda item: (item[0], item[1], item[2].dedupe_key))
    return [item[2] for item in ranked[:MAX_OPPORTUNITIES_PER_DETECTOR]]



async def _detect_checkout_abandonment_recovery(
    session: AsyncSession,
    *,
    business_id: UUID,
    window: _ComparisonWindow,
) -> list[_GrowthSignal]:
    """
    Detect unresolved, explicitly recorded checkout abandonment.

    The canonical ``checkout_abandoned`` event is required. Cart/session state is
    not inferred from metadata, and anonymous events never become customer-linked
    outreach Opportunities. A four-hour grace period avoids treating fresh intent
    as abandoned recovery work.
    """
    abandonment_start = window.recent_end - timedelta(
        days=CHECKOUT_ABANDONMENT_LOOKBACK_DAYS
    )
    abandonment_cutoff = window.recent_end - timedelta(
        hours=CHECKOUT_ABANDONMENT_GRACE_HOURS
    )
    purchase_occurred_at = _authoritative_order_occurred_at()
    retained_revenue = _retained_order_revenue()

    matching_product_purchase = exists(
        select(OrderLineItem.id).where(
            OrderLineItem.business_id == business_id,
            OrderLineItem.order_id == Order.id,
            OrderLineItem.catalog_item_id
            == CommerceEvent.catalog_item_id,
        )
    )
    purchase_resolved = exists(
        select(Order.id).where(
            Order.business_id == business_id,
            Order.customer_id == CommerceEvent.customer_id,
            Order.status != "canceled",
            Order.payment_status.in_(REALIZED_PAYMENT_STATES),
            purchase_occurred_at.is_not(None),
            purchase_occurred_at >= CommerceEvent.occurred_at,
            purchase_occurred_at < window.recent_end,
            retained_revenue > Decimal("0.00"),
            or_(
                CommerceEvent.catalog_item_id.is_(None),
                matching_product_purchase,
            ),
        )
    )

    rows = (await session.execute(
        select(
            CommerceEvent.id.label("event_id"),
            CommerceEvent.customer_id,
            CommerceEvent.catalog_item_id,
            CommerceEvent.occurred_at,
            CatalogItem.name.label("catalog_item_name"),
            CatalogItem.item_type.label("catalog_item_type"),
            CatalogItem.status.label("catalog_item_status"),
            CatalogItem.published.label("catalog_item_published"),
            CatalogItem.availability.label("catalog_item_availability"),
            CatalogItem.inventory_quantity.label(
                "catalog_item_inventory_quantity"
            ),
        )
        .join(
            Customer,
            (Customer.id == CommerceEvent.customer_id)
            & (Customer.business_id == CommerceEvent.business_id),
        )
        .outerjoin(
            CatalogItem,
            (CatalogItem.id == CommerceEvent.catalog_item_id)
            & (CatalogItem.business_id == CommerceEvent.business_id),
        )
        .where(
            CommerceEvent.business_id == business_id,
            Customer.business_id == business_id,
            Customer.active.is_(True),
            Customer.status == "active",
            CommerceEvent.event_type == "checkout_abandoned",
            CommerceEvent.customer_id.is_not(None),
            CommerceEvent.occurred_at >= abandonment_start,
            CommerceEvent.occurred_at <= abandonment_cutoff,
            or_(
                CommerceEvent.catalog_item_id.is_(None),
                and_(*_sellable_product_predicates(CatalogItem)),
            ),
            ~purchase_resolved,
        )
        .order_by(
            CommerceEvent.occurred_at.desc(),
            CommerceEvent.id.desc(),
        )
        .limit(MAX_DETECTOR_CANDIDATES)
    )).all()

    ranked: list[tuple[Decimal, Decimal, float, _GrowthSignal]] = []
    for row in rows:
        event_id = row.event_id
        customer_id = row.customer_id
        catalog_item_id = row.catalog_item_id
        observed_at = row.occurred_at
        if event_id is None or customer_id is None or observed_at is None:
            continue
        if catalog_item_id is not None and not _is_sellable_product_state(
            item_type=str(row.catalog_item_type),
            status=str(row.catalog_item_status),
            published=bool(row.catalog_item_published),
            availability=str(row.catalog_item_availability),
            inventory_quantity=row.catalog_item_inventory_quantity,
        ):
            continue

        elapsed_hours = (
            Decimal(str(
                (window.recent_end - observed_at).total_seconds()
            ))
            / Decimal("3600")
        )
        if elapsed_hours < Decimal(CHECKOUT_ABANDONMENT_GRACE_HOURS):
            continue
        if elapsed_hours > Decimal(
            CHECKOUT_ABANDONMENT_LOOKBACK_DAYS * 24
        ):
            continue

        product_linked = catalog_item_id is not None
        confidence = _confidence(
            base=Decimal("0.720"),
            evidence=Decimal("0.050") if product_linked else Decimal("0.000"),
            severity=min(
                Decimal("0.130"),
                (
                    elapsed_hours
                    - Decimal(CHECKOUT_ABANDONMENT_GRACE_HOURS)
                )
                / Decimal("48")
                * Decimal("0.130"),
            ),
        )
        rank_score = min(
            Decimal("0.940"),
            Decimal("0.650")
            + (Decimal("0.060") if product_linked else Decimal("0.000"))
            + min(
                Decimal("0.160"),
                (
                    elapsed_hours
                    - Decimal(CHECKOUT_ABANDONMENT_GRACE_HOURS)
                )
                / Decimal("48")
                * Decimal("0.160"),
            ),
        ).quantize(Decimal("0.001"))

        product_name = (
            str(row.catalog_item_name)
            if product_linked and row.catalog_item_name
            else None
        )
        entity_type = "catalog_item" if product_linked else "customer"
        entity_id = catalog_item_id if product_linked else customer_id
        product_context = (
            f" for {product_name}" if product_name else ""
        )
        signal = _GrowthSignal(
            dedupe_key=(
                "business-growth:checkout-abandonment-recovery:"
                f"{customer_id}:{event_id}"
            ),
            title=(
                f"Observed checkout recovery opportunity{product_context}"
            )[:180],
            description=(
                "A first-party checkout_abandoned event remains unresolved by "
                "a subsequent eligible retained-revenue purchase after the "
                f"{CHECKOUT_ABANDONMENT_GRACE_HOURS}-hour grace period. This "
                "is observed incomplete purchase intent, not a churn signal or "
                "a prediction of future behavior."
            )[:3000],
            category="checkout_abandonment_recovery",
            source="commerce",
            source_entity_type=entity_type,
            source_entity_id=entity_id,
            customer_id=customer_id,
            reason=(
                f"The explicit checkout abandonment was observed "
                f"{elapsed_hours:.2f} hours before the analysis boundary and "
                "no resolving eligible purchase was observed afterward."
            )[:3000],
            confidence=confidence,
            recommendation=(
                "Ask the Sales Copilot to review current order history, product "
                "context, consent, and business policy before proposing any "
                "checkout-recovery follow-up."
            ),
            provenance=[{
                "classification": "first_party_observed",
                "detector": "checkout_abandonment_recovery",
                "source_type": "commerce_events",
                "source_id": str(event_id),
                "observed_at": observed_at.isoformat(),
                "event_type": "checkout_abandoned",
                "catalog_item_id": (
                    str(catalog_item_id) if catalog_item_id else None
                ),
                "lookback_days": CHECKOUT_ABANDONMENT_LOOKBACK_DAYS,
                "grace_period_hours": CHECKOUT_ABANDONMENT_GRACE_HOURS,
                "elapsed_hours": str(
                    elapsed_hours.quantize(Decimal("0.001"))
                ),
                "purchase_resolved": False,
                "purchase_resolution_scope": (
                    "subsequent_same_product_eligible_purchase"
                    if product_linked
                    else "subsequent_customer_eligible_purchase"
                ),
                "product_sellability_scope": (
                    "active_published_sellable_catalog_item"
                    if product_linked
                    else "not_product_linked"
                ),
                "order_timestamp_policy": (
                    "manual_created_at_else_provider_created_at_required"
                ),
                "eligible_payment_states": list(REALIZED_PAYMENT_STATES),
                "refund_treatment": "positive_retained_revenue_only",
            }],
            priority=(
                "high" if elapsed_hours >= Decimal("24") else "medium"
            ),
            currency=None,
            rank_score=rank_score,
        )
        ranked.append((
            rank_score,
            confidence,
            observed_at.astimezone(UTC).timestamp(),
            signal,
        ))

    ranked.sort(
        key=lambda item: (
            -_GROWTH_PRIORITY_RANK.get(item[3].priority, 0),
            -item[0],
            -item[1],
            -item[2],
            item[3].dedupe_key,
        )
    )
    selected: list[_GrowthSignal] = []
    seen_customers: set[UUID] = set()
    for _score, _confidence_value, _observed_timestamp, signal in ranked:
        if signal.customer_id is None or signal.customer_id in seen_customers:
            continue
        seen_customers.add(signal.customer_id)
        selected.append(signal)
        if len(selected) >= MAX_OPPORTUNITIES_PER_DETECTOR:
            break
    return selected


async def _detect_repeated_product_interest(
    session: AsyncSession,
    *,
    business_id: UUID,
    window: _ComparisonWindow,
) -> list[_GrowthSignal]:
    """
    Detect repeated identified-customer product views without a later purchase.

    Three views across at least two UTC days are required. The detector uses only
    relational customer/catalog identities and never reads event metadata or
    anonymous session hashes.
    """
    interest_start = window.recent_end - timedelta(
        days=PRODUCT_INTEREST_LOOKBACK_DAYS
    )
    event_count = func.count(CommerceEvent.id).label("event_count")
    first_observed_at = func.min(CommerceEvent.occurred_at).label(
        "first_observed_at"
    )
    last_observed_at = func.max(CommerceEvent.occurred_at).label(
        "last_observed_at"
    )
    distinct_observed_days = func.count(func.distinct(func.date_trunc(
        "day",
        func.timezone("UTC", CommerceEvent.occurred_at),
    ))).label("distinct_observed_days")

    interest_candidates = (
        select(
            CommerceEvent.customer_id.label("customer_id"),
            CommerceEvent.catalog_item_id.label("catalog_item_id"),
            event_count,
            first_observed_at,
            last_observed_at,
            distinct_observed_days,
        )
        .where(
            CommerceEvent.business_id == business_id,
            CommerceEvent.event_type == "product_viewed",
            CommerceEvent.customer_id.is_not(None),
            CommerceEvent.catalog_item_id.is_not(None),
            CommerceEvent.occurred_at >= interest_start,
            CommerceEvent.occurred_at < window.recent_end,
        )
        .group_by(
            CommerceEvent.customer_id,
            CommerceEvent.catalog_item_id,
        )
        .having(
            event_count >= PRODUCT_INTEREST_MIN_EVENTS,
            distinct_observed_days >= PRODUCT_INTEREST_MIN_ACTIVE_DAYS,
        )
        .subquery("product_interest_candidates")
    )

    purchase_occurred_at = _authoritative_order_occurred_at()
    retained_revenue = _retained_order_revenue()
    purchase_resolved = exists(
        select(Order.id)
        .join(
            OrderLineItem,
            (OrderLineItem.order_id == Order.id)
            & (OrderLineItem.business_id == Order.business_id),
        )
        .where(
            Order.business_id == business_id,
            OrderLineItem.business_id == business_id,
            Order.customer_id == interest_candidates.c.customer_id,
            OrderLineItem.catalog_item_id
            == interest_candidates.c.catalog_item_id,
            Order.status != "canceled",
            Order.payment_status.in_(REALIZED_PAYMENT_STATES),
            purchase_occurred_at.is_not(None),
            purchase_occurred_at >= interest_candidates.c.first_observed_at,
            purchase_occurred_at < window.recent_end,
            retained_revenue > Decimal("0.00"),
        )
    )

    rows = (await session.execute(
        select(
            interest_candidates.c.customer_id,
            interest_candidates.c.catalog_item_id,
            interest_candidates.c.event_count,
            interest_candidates.c.first_observed_at,
            interest_candidates.c.last_observed_at,
            interest_candidates.c.distinct_observed_days,
            CatalogItem.name.label("catalog_item_name"),
            CatalogItem.item_type.label("catalog_item_type"),
            CatalogItem.status.label("catalog_item_status"),
            CatalogItem.published.label("catalog_item_published"),
            CatalogItem.availability.label("catalog_item_availability"),
            CatalogItem.inventory_quantity.label(
                "catalog_item_inventory_quantity"
            ),
        )
        .join(
            Customer,
            (Customer.id == interest_candidates.c.customer_id)
            & (Customer.business_id == business_id),
        )
        .join(
            CatalogItem,
            (CatalogItem.id == interest_candidates.c.catalog_item_id)
            & (CatalogItem.business_id == business_id),
        )
        .where(
            Customer.business_id == business_id,
            Customer.active.is_(True),
            Customer.status == "active",
            CatalogItem.business_id == business_id,
            *_sellable_product_predicates(CatalogItem),
            ~purchase_resolved,
        )
        .order_by(
            interest_candidates.c.event_count.desc(),
            interest_candidates.c.distinct_observed_days.desc(),
            interest_candidates.c.last_observed_at.desc(),
            interest_candidates.c.customer_id,
            interest_candidates.c.catalog_item_id,
        )
        .limit(MAX_DETECTOR_CANDIDATES)
    )).all()

    ranked: list[tuple[Decimal, Decimal, float, _GrowthSignal]] = []
    for row in rows:
        customer_id = row.customer_id
        catalog_item_id = row.catalog_item_id
        views = int(row.event_count or 0)
        active_days = int(row.distinct_observed_days or 0)
        first_observed = row.first_observed_at
        last_observed = row.last_observed_at
        if (
            customer_id is None
            or catalog_item_id is None
            or first_observed is None
            or last_observed is None
            or views < PRODUCT_INTEREST_MIN_EVENTS
            or active_days < PRODUCT_INTEREST_MIN_ACTIVE_DAYS
        ):
            continue
        if not _is_sellable_product_state(
            item_type=str(row.catalog_item_type),
            status=str(row.catalog_item_status),
            published=bool(row.catalog_item_published),
            availability=str(row.catalog_item_availability),
            inventory_quantity=row.catalog_item_inventory_quantity,
        ):
            continue
        if (
            first_observed < interest_start
            or last_observed >= window.recent_end
        ):
            continue

        hours_since_last = max(
            Decimal("0.000"),
            Decimal(str(
                (window.recent_end - last_observed).total_seconds()
            ))
            / Decimal("3600"),
        )
        recency_boost = max(
            Decimal("0.000"),
            Decimal("0.080")
            * (
                Decimal(PRODUCT_INTEREST_LOOKBACK_DAYS * 24)
                - hours_since_last
            )
            / Decimal(PRODUCT_INTEREST_LOOKBACK_DAYS * 24),
        )
        confidence = _confidence(
            base=Decimal("0.660"),
            evidence=min(
                Decimal("0.170"),
                Decimal(views - PRODUCT_INTEREST_MIN_EVENTS)
                * Decimal("0.025")
                + Decimal(active_days - PRODUCT_INTEREST_MIN_ACTIVE_DAYS)
                * Decimal("0.025"),
            ),
            severity=recency_boost,
        )
        rank_score = min(
            Decimal("0.950"),
            Decimal("0.600")
            + min(
                Decimal("0.180"),
                Decimal(views - PRODUCT_INTEREST_MIN_EVENTS)
                * Decimal("0.030"),
            )
            + min(
                Decimal("0.100"),
                Decimal(active_days - PRODUCT_INTEREST_MIN_ACTIVE_DAYS)
                * Decimal("0.025"),
            )
            + recency_boost,
        ).quantize(Decimal("0.001"))

        last_utc = last_observed.astimezone(UTC)
        episode_start = last_utc.date() - timedelta(
            days=last_utc.date().weekday()
        )
        product_name = str(row.catalog_item_name)
        signal = _GrowthSignal(
            dedupe_key=(
                "business-growth:repeated-product-interest:"
                f"{customer_id}:{catalog_item_id}:{episode_start.isoformat()}"
            ),
            title=f"Repeated product interest: {product_name}"[:180],
            description=(
                f"A customer viewed {product_name} {views} times across "
                f"{active_days} distinct UTC days in the bounded "
                f"{PRODUCT_INTEREST_LOOKBACK_DAYS}-day window, with no "
                "subsequent eligible retained-revenue purchase of that product. "
                "This is observed product interest, not purchase propensity or "
                "a prediction that the customer will buy."
            )[:3000],
            category="repeated_product_interest",
            source="commerce",
            source_entity_type="catalog_item",
            source_entity_id=catalog_item_id,
            customer_id=customer_id,
            reason=(
                f"Observed {views} product_viewed events from "
                f"{first_observed.isoformat()} through "
                f"{last_observed.isoformat()} across {active_days} days; "
                "no resolving same-product purchase was observed."
            )[:3000],
            confidence=confidence,
            recommendation=(
                "Ask the Sales Copilot to review product relevance, current "
                "availability, order history, consent, and business policy "
                "before proposing any follow-up."
            ),
            provenance=[{
                "classification": "first_party_observed",
                "detector": "repeated_product_interest",
                "source_type": "commerce_events",
                "source_id": str(catalog_item_id),
                "observed_at": last_observed.isoformat(),
                "event_type": "product_viewed",
                "catalog_item_id": str(catalog_item_id),
                "event_count": views,
                "product_view_count": views,
                "distinct_observed_days": active_days,
                "first_observed_at": first_observed.isoformat(),
                "last_observed_at": last_observed.isoformat(),
                "lookback_days": PRODUCT_INTEREST_LOOKBACK_DAYS,
                "purchase_resolved": False,
                "purchase_resolution_scope": (
                    "subsequent_same_product_eligible_purchase"
                ),
                "product_sellability_scope": (
                    "active_published_sellable_catalog_item"
                ),
                "order_timestamp_policy": (
                    "manual_created_at_else_provider_created_at_required"
                ),
                "eligible_payment_states": list(REALIZED_PAYMENT_STATES),
                "refund_treatment": "positive_retained_revenue_only",
            }],
            priority=(
                "high" if views >= 6 and active_days >= 3 else "medium"
            ),
            currency=None,
            rank_score=rank_score,
        )
        ranked.append((
            rank_score,
            confidence,
            last_utc.timestamp(),
            signal,
        ))

    ranked.sort(
        key=lambda item: (
            -_GROWTH_PRIORITY_RANK.get(item[3].priority, 0),
            -item[0],
            -item[1],
            -item[2],
            item[3].dedupe_key,
        )
    )
    selected: list[_GrowthSignal] = []
    seen_customers: set[UUID] = set()
    for _score, _confidence_value, _last_timestamp, signal in ranked:
        if signal.customer_id is None or signal.customer_id in seen_customers:
            continue
        seen_customers.add(signal.customer_id)
        selected.append(signal)
        if len(selected) >= MAX_OPPORTUNITIES_PER_DETECTOR:
            break
    return selected


def _median_decimal(values: list[Decimal]) -> Decimal:
    if not values:
        raise ValueError("median_requires_values")
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")


async def _detect_repeat_purchase_due(
    session: AsyncSession,
    *,
    business_id: UUID,
    window: _ComparisonWindow,
    limit: int = MAX_OPPORTUNITIES_PER_DETECTOR,
) -> list[_GrowthSignal]:
    """
    Detect customers materially beyond their own observed repeat-purchase cadence.

    This is deterministic first-party purchase intelligence, not churn prediction.
    Fully refunded / zero-retained-revenue orders do not establish purchase cadence.

    ``limit`` exists so other deterministic customer-intelligence detectors can
    reuse the bounded cadence result set without issuing a second cadence query.
    Normal detector execution remains capped to MAX_OPPORTUNITIES_PER_DETECTOR.
    """
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("repeat_purchase_limit_invalid")
    if limit < 1 or limit > MAX_DETECTOR_CANDIDATES:
        raise ValueError("repeat_purchase_limit_invalid")

    occurred_at = _authoritative_order_occurred_at()
    retained_revenue = _retained_order_revenue()

    purchase_count = func.count(Order.id).label("purchase_count")
    last_purchase_at = func.max(occurred_at).label("last_purchase_at")
    observed_retained_revenue = func.coalesce(
        func.sum(retained_revenue),
        Decimal("0.00"),
    ).label("observed_retained_revenue")

    candidate_rows = (await session.execute(
        select(
            Order.customer_id,
            Order.currency,
            purchase_count,
            last_purchase_at,
            observed_retained_revenue,
        )
        .join(
            Customer,
            (Customer.id == Order.customer_id)
            & (Customer.business_id == Order.business_id),
        )
        .where(
            Order.business_id == business_id,
            Customer.business_id == business_id,
            Customer.active.is_(True),
            Customer.status == "active",
            Order.customer_id.is_not(None),
            Order.status != "canceled",
            Order.payment_status.in_(REALIZED_PAYMENT_STATES),
            occurred_at.is_not(None),
            occurred_at < window.recent_end,
            retained_revenue > Decimal("0.00"),
        )
        .group_by(
            Order.customer_id,
            Order.currency,
        )
        .having(
            purchase_count >= REPEAT_PURCHASE_MIN_ORDERS,
        )
        .order_by(
            last_purchase_at.asc(),
            purchase_count.desc(),
            Order.customer_id,
            Order.currency,
        )
        .limit(MAX_DETECTOR_CANDIDATES)
    )).all()

    candidate_pairs = {
        (row.customer_id, str(row.currency))
        for row in candidate_rows
        if row.customer_id is not None
    }
    if not candidate_pairs:
        return []

    history_rank = func.row_number().over(
        partition_by=(Order.customer_id, Order.currency),
        order_by=(occurred_at.desc(), Order.id.desc()),
    ).label("history_rank")

    history_subquery = (
        select(
            Order.id.label("order_id"),
            Order.customer_id.label("customer_id"),
            Order.currency.label("currency"),
            occurred_at.label("occurred_at"),
            retained_revenue.label("retained_revenue"),
            history_rank,
        )
        .where(
            Order.business_id == business_id,
            Order.customer_id.is_not(None),
            tuple_(Order.customer_id, Order.currency).in_(tuple(candidate_pairs)),
            Order.status != "canceled",
            Order.payment_status.in_(REALIZED_PAYMENT_STATES),
            occurred_at.is_not(None),
            occurred_at < window.recent_end,
            retained_revenue > Decimal("0.00"),
        )
        .subquery()
    )

    history_rows = (await session.execute(
        select(
            history_subquery.c.order_id,
            history_subquery.c.customer_id,
            history_subquery.c.currency,
            history_subquery.c.occurred_at,
            history_subquery.c.retained_revenue,
        )
        .where(
            history_subquery.c.history_rank
            <= REPEAT_PURCHASE_MAX_HISTORY_ORDERS
        )
        .order_by(
            history_subquery.c.customer_id,
            history_subquery.c.currency,
            history_subquery.c.occurred_at,
            history_subquery.c.order_id,
        )
    )).all()

    history_by_pair: dict[tuple[UUID, str], list[object]] = {}
    for row in history_rows:
        if row.customer_id is None:
            continue
        pair = (row.customer_id, str(row.currency))
        if pair not in candidate_pairs:
            continue
        history_by_pair.setdefault(pair, []).append(row)

    candidate_by_pair = {
        (row.customer_id, str(row.currency)): row
        for row in candidate_rows
        if row.customer_id is not None
    }

    ranked: list[tuple[Decimal, Decimal, _GrowthSignal]] = []

    for pair in sorted(
        candidate_pairs,
        key=lambda item: (str(item[0]), item[1]),
    ):
        customer_id, currency = pair
        rows = history_by_pair.get(pair, [])
        candidate = candidate_by_pair.get(pair)

        if candidate is None or len(rows) < REPEAT_PURCHASE_MIN_ORDERS:
            continue

        purchase_count_value = int(candidate.purchase_count or 0)
        if purchase_count_value < REPEAT_PURCHASE_MIN_ORDERS:
            continue

        intervals: list[Decimal] = []
        for previous, current in zip(rows, rows[1:]):
            previous_at = previous.occurred_at
            current_at = current.occurred_at
            if previous_at is None or current_at is None:
                continue
            seconds = Decimal(str(
                (current_at - previous_at).total_seconds()
            ))
            if seconds <= 0:
                continue
            intervals.append(seconds / Decimal("86400"))

        if len(intervals) < REPEAT_PURCHASE_MIN_INTERVALS:
            continue

        median_cadence_days = _median_decimal(intervals)
        if median_cadence_days < REPEAT_PURCHASE_MIN_CADENCE_DAYS:
            continue

        last_row = rows[-1]
        last_purchase = last_row.occurred_at
        if last_purchase is None:
            continue

        days_since_last_purchase = (
            Decimal(str(
                (window.recent_end - last_purchase).total_seconds()
            ))
            / Decimal("86400")
        )
        if days_since_last_purchase <= 0:
            continue

        overdue_ratio = days_since_last_purchase / median_cadence_days
        if overdue_ratio < REPEAT_PURCHASE_OVERDUE_RATIO:
            continue

        observed_value = _as_decimal(
            candidate.observed_retained_revenue
        )

        evidence_boost = min(
            Decimal("0.140"),
            Decimal(
                max(
                    0,
                    len(intervals) - REPEAT_PURCHASE_MIN_INTERVALS,
                )
            )
            * Decimal("0.020"),
        )
        severity_boost = min(
            Decimal("0.170"),
            (
                overdue_ratio - REPEAT_PURCHASE_OVERDUE_RATIO
            )
            * Decimal("0.100"),
        )

        confidence = _confidence(
            base=Decimal("0.680"),
            evidence=evidence_boost,
            severity=severity_boost,
        )

        rank_score = min(
            Decimal("0.990"),
            Decimal("0.560")
            + min(
                Decimal("0.180"),
                Decimal(
                    max(
                        0,
                        purchase_count_value - REPEAT_PURCHASE_MIN_ORDERS,
                    )
                )
                * Decimal("0.025"),
            )
            + min(
                Decimal("0.220"),
                (
                    overdue_ratio - REPEAT_PURCHASE_OVERDUE_RATIO
                )
                * Decimal("0.120"),
            ),
        ).quantize(Decimal("0.001"))

        signal = _GrowthSignal(
            dedupe_key=(
                "business-growth:repeat-purchase-due:"
                f"{customer_id}:{currency}:{last_row.order_id}"
            ),
            title="Repeat purchase cadence exceeded",
            description=(
                "Observed time since the customer's latest retained-revenue "
                f"purchase is {overdue_ratio:.2f}x that customer's median "
                "purchase interval across recent eligible purchases. This is "
                "an observed cadence signal, not a churn prediction."
            )[:3000],
            category="repeat_purchase_due",
            source="commerce",
            source_entity_type="customer",
            source_entity_id=customer_id,
            customer_id=customer_id,
            reason=(
                f"{purchase_count_value} eligible purchases in {currency} "
                f"establish {len(intervals)} positive purchase intervals; "
                f"median cadence is {median_cadence_days:.2f} days and "
                f"{days_since_last_purchase:.2f} days have elapsed since the "
                "latest eligible purchase."
            )[:3000],
            confidence=confidence,
            recommendation=(
                "Ask the Sales Copilot to review the observed purchase history "
                "and relevant customer context before proposing a retention or "
                "reorder follow-up."
            ),
            provenance=[{
                "classification": "first_party_observed",
                "detector": "repeat_purchase_due",
                "source_type": "orders",
                "source_id": str(last_row.order_id),
                "observed_at": last_purchase.isoformat(),
                "currency": currency,
                "purchase_count": purchase_count_value,
                "purchase_interval_count": len(intervals),
                "median_purchase_interval_days": str(
                    median_cadence_days.quantize(Decimal("0.001"))
                ),
                "days_since_last_purchase": str(
                    days_since_last_purchase.quantize(Decimal("0.001"))
                ),
                "purchase_overdue_ratio": str(
                    overdue_ratio.quantize(Decimal("0.001"))
                ),
                "observed_retained_revenue": str(observed_value),
                "order_timestamp_policy": (
                    "manual_created_at_else_provider_created_at_required"
                ),
                "eligible_payment_states": list(REALIZED_PAYMENT_STATES),
                "refund_treatment": (
                    "positive_retained_revenue_only;"
                    "refunded_orders_zero_else_total_minus_refunded_amount"
                ),
            }],
            priority=(
                "high"
                if overdue_ratio >= Decimal("2.50")
                and purchase_count_value >= 4
                else "medium"
            ),
            currency=currency,
            rank_score=rank_score,
        )
        ranked.append((rank_score, confidence, signal))

    ranked.sort(
        key=lambda item: (
            -item[0],
            -item[1],
            item[2].dedupe_key,
        )
    )
    return [
        item[2]
        for item in ranked[:limit]
    ]




async def _detect_customer_value_declines(
    session: AsyncSession,
    *,
    business_id: UUID,
    window: _ComparisonWindow,
) -> list[_GrowthSignal]:
    """
    Detect material customer purchase-value decline using two comparable
    same-currency 28-day first-party retained-revenue windows.

    This is observed purchase behavior, not predicted CLV or churn.
    Revenue decline alone is insufficient; purchase frequency must also decline.
    """
    occurred_at = _authoritative_order_occurred_at()
    retained_revenue = _retained_order_revenue()

    recent_end = window.recent_end
    recent_start = recent_end - timedelta(
        days=CUSTOMER_VALUE_COMPARISON_DAYS
    )
    baseline_end = recent_start
    baseline_start = baseline_end - timedelta(
        days=CUSTOMER_VALUE_COMPARISON_DAYS
    )

    recent = and_(
        occurred_at >= recent_start,
        occurred_at < recent_end,
    )
    baseline = and_(
        occurred_at >= baseline_start,
        occurred_at < baseline_end,
    )

    recent_order_count = func.count(Order.id).filter(recent).label(
        "recent_order_count"
    )
    baseline_order_count = func.count(Order.id).filter(baseline).label(
        "baseline_order_count"
    )

    recent_net_revenue = func.coalesce(
        func.sum(retained_revenue).filter(recent),
        Decimal("0.00"),
    ).label("recent_net_revenue")
    baseline_net_revenue = func.coalesce(
        func.sum(retained_revenue).filter(baseline),
        Decimal("0.00"),
    ).label("baseline_net_revenue")

    revenue_decline_ratio_expression = (
        (baseline_net_revenue - recent_net_revenue)
        / func.nullif(baseline_net_revenue, Decimal("0.00"))
    )
    order_decline_ratio_expression = (
        (baseline_order_count - recent_order_count) * Decimal("1.0")
        / func.nullif(baseline_order_count, 0)
    )

    rows = (await session.execute(
        select(
            Order.customer_id,
            Order.currency,
            recent_order_count,
            baseline_order_count,
            recent_net_revenue,
            baseline_net_revenue,
        )
        .join(
            Customer,
            (Customer.id == Order.customer_id)
            & (Customer.business_id == Order.business_id),
        )
        .where(
            Order.business_id == business_id,
            Customer.business_id == business_id,
            Customer.active.is_(True),
            Customer.status == "active",
            Order.customer_id.is_not(None),
            Order.status != "canceled",
            Order.payment_status.in_(REALIZED_PAYMENT_STATES),
            occurred_at.is_not(None),
            occurred_at >= baseline_start,
            occurred_at < recent_end,
            retained_revenue > Decimal("0.00"),
        )
        .group_by(
            Order.customer_id,
            Order.currency,
        )
        .having(
            baseline_order_count >= CUSTOMER_VALUE_MIN_BASELINE_ORDERS,
        )
        .order_by(
            revenue_decline_ratio_expression.desc(),
            order_decline_ratio_expression.desc(),
            baseline_order_count.desc(),
            Order.customer_id,
            Order.currency,
        )
        .limit(MAX_DETECTOR_CANDIDATES)
    )).all()

    ranked: list[tuple[Decimal, Decimal, _GrowthSignal]] = []

    for row in rows:
        customer_id = row.customer_id
        if customer_id is None:
            continue

        currency = str(row.currency)
        recent_orders = int(row.recent_order_count or 0)
        baseline_orders = int(row.baseline_order_count or 0)

        recent_net = _as_decimal(row.recent_net_revenue)
        baseline_net = _as_decimal(row.baseline_net_revenue)

        if (
            baseline_orders < CUSTOMER_VALUE_MIN_BASELINE_ORDERS
            or baseline_net <= 0
            or recent_orders >= baseline_orders
            or recent_net >= baseline_net
        ):
            continue

        order_decline = baseline_orders - recent_orders
        order_decline_ratio = (
            Decimal(order_decline) / Decimal(baseline_orders)
        )

        revenue_decline = baseline_net - recent_net
        revenue_decline_ratio = revenue_decline / baseline_net

        if (
            order_decline < CUSTOMER_VALUE_MIN_ORDER_DECLINE
            or order_decline_ratio < CUSTOMER_VALUE_ORDER_DECLINE_RATIO
            or revenue_decline_ratio < CUSTOMER_VALUE_REVENUE_DECLINE_RATIO
        ):
            continue

        evidence_boost = min(
            Decimal("0.150"),
            Decimal(
                max(
                    0,
                    baseline_orders - CUSTOMER_VALUE_MIN_BASELINE_ORDERS,
                )
            )
            * Decimal("0.025"),
        )

        severity_boost = min(
            Decimal("0.180"),
            (
                revenue_decline_ratio
                - CUSTOMER_VALUE_REVENUE_DECLINE_RATIO
            )
            * Decimal("0.220")
            + (
                order_decline_ratio
                - CUSTOMER_VALUE_ORDER_DECLINE_RATIO
            )
            * Decimal("0.160"),
        )

        confidence = _confidence(
            base=Decimal("0.660"),
            evidence=evidence_boost,
            severity=severity_boost,
        )

        rank_score = min(
            Decimal("0.990"),
            Decimal("0.560")
            + min(
                Decimal("0.230"),
                (
                    revenue_decline_ratio
                    - CUSTOMER_VALUE_REVENUE_DECLINE_RATIO
                )
                * Decimal("0.380"),
            )
            + min(
                Decimal("0.160"),
                (
                    order_decline_ratio
                    - CUSTOMER_VALUE_ORDER_DECLINE_RATIO
                )
                * Decimal("0.250"),
            )
            + min(
                Decimal("0.040"),
                Decimal(
                    max(
                        0,
                        baseline_orders
                        - CUSTOMER_VALUE_MIN_BASELINE_ORDERS,
                    )
                )
                * Decimal("0.010"),
            ),
        ).quantize(Decimal("0.001"))

        signal = _GrowthSignal(
            dedupe_key=(
                "business-growth:customer-value-decline:"
                f"{customer_id}:{currency}:{window.window_key}"
            ),
            title="Customer purchase value decline detected",
            description=(
                "Observed retained purchase revenue and purchase frequency for "
                "a customer both declined materially versus the immediately "
                f"preceding {CUSTOMER_VALUE_COMPARISON_DAYS}-day period in the "
                "same currency. This is observed purchase behavior, not "
                "predicted customer lifetime value or a churn prediction."
            )[:3000],
            category="customer_value_decline",
            source="commerce",
            source_entity_type="customer",
            source_entity_id=customer_id,
            customer_id=customer_id,
            reason=(
                f"Eligible retained-revenue purchases declined from "
                f"{baseline_orders} to {recent_orders}; observed retained "
                f"revenue declined from {baseline_net:.2f} to "
                f"{recent_net:.2f} {currency}."
            )[:3000],
            confidence=confidence,
            recommendation=(
                "Ask the Sales Copilot to review purchase history, product "
                "context, customer interactions, and business policy before "
                "proposing a retention or re-engagement response."
            ),
            provenance=[{
                "classification": "first_party_observed",
                "detector": "customer_value_decline",
                "source_type": "orders",
                "window_start": recent_start.isoformat(),
                "window_end": recent_end.isoformat(),
                "window_end_inclusive": False,
                "baseline_start": baseline_start.isoformat(),
                "baseline_end": baseline_end.isoformat(),
                "baseline_end_inclusive": False,
                "currency": currency,
                "recent_order_count": recent_orders,
                "baseline_order_count": baseline_orders,
                "recent_net_revenue": str(recent_net),
                "baseline_net_revenue": str(baseline_net),
                "absolute_decline": str(revenue_decline),
                "decline_ratio": str(
                    revenue_decline_ratio.quantize(Decimal("0.001"))
                ),
                "purchase_count_decline_ratio": str(
                    order_decline_ratio.quantize(Decimal("0.001"))
                ),
                "comparison_window_days": CUSTOMER_VALUE_COMPARISON_DAYS,
                "order_timestamp_policy": (
                    "manual_created_at_else_provider_created_at_required"
                ),
                "eligible_payment_states": list(REALIZED_PAYMENT_STATES),
                "refund_treatment": (
                    "positive_retained_revenue_only;"
                    "refunded_orders_zero_else_total_minus_refunded_amount"
                ),
                "customer_value_scope": (
                    "observed_comparable_retained_revenue_not_predicted_clv"
                ),
            }],
            priority=(
                "high"
                if (
                    revenue_decline_ratio >= Decimal("0.60")
                    and order_decline_ratio >= Decimal("0.50")
                )
                else "medium"
            ),
            currency=currency,
            rank_score=rank_score,
        )

        ranked.append((rank_score, confidence, signal))

    ranked.sort(
        key=lambda item: (
            -item[0],
            -item[1],
            item[2].dedupe_key,
        )
    )

    return [
        item[2]
        for item in ranked[:MAX_OPPORTUNITIES_PER_DETECTOR]
    ]


async def _detect_high_value_customer_at_risk(
    session: AsyncSession,
    *,
    business_id: UUID,
    window: _ComparisonWindow,
    cadence_candidates: list[_GrowthSignal],
) -> list[_GrowthSignal]:
    """
    Promote materially overdue repeat customers whose observed retained revenue
    ranks in the upper portion of a meaningful same-currency business cohort.

    This detector uses observed first-party value only. It does not calculate or
    claim predicted customer lifetime value.
    """
    cadence_by_pair = {
        (signal.customer_id, signal.currency): signal
        for signal in cadence_candidates
        if (
            signal.category == "repeat_purchase_due"
            and signal.customer_id is not None
            and signal.currency is not None
        )
    }
    if not cadence_by_pair:
        return []

    occurred_at = _authoritative_order_occurred_at()
    retained_revenue = _retained_order_revenue()
    value_start = window.recent_end - timedelta(days=HIGH_VALUE_LOOKBACK_DAYS)

    customer_values = (
        select(
            Order.customer_id.label("customer_id"),
            Order.currency.label("currency"),
            func.count(Order.id).label("value_window_purchase_count"),
            func.coalesce(
                func.sum(retained_revenue),
                Decimal("0.00"),
            ).label("observed_retained_revenue"),
        )
        .join(
            Customer,
            (Customer.id == Order.customer_id)
            & (Customer.business_id == Order.business_id),
        )
        .where(
            Order.business_id == business_id,
            Customer.business_id == business_id,
            Customer.active.is_(True),
            Customer.status == "active",
            Order.customer_id.is_not(None),
            Order.status != "canceled",
            Order.payment_status.in_(REALIZED_PAYMENT_STATES),
            occurred_at.is_not(None),
            occurred_at >= value_start,
            occurred_at < window.recent_end,
            retained_revenue > Decimal("0.00"),
        )
        .group_by(
            Order.customer_id,
            Order.currency,
        )
        .subquery()
    )

    ranked_values = (
        select(
            customer_values.c.customer_id,
            customer_values.c.currency,
            customer_values.c.value_window_purchase_count,
            customer_values.c.observed_retained_revenue,
            func.count().over(
                partition_by=customer_values.c.currency
            ).label("cohort_size"),
            func.percent_rank().over(
                partition_by=customer_values.c.currency,
                order_by=customer_values.c.observed_retained_revenue,
            ).label("observed_value_percentile"),
        )
        .subquery()
    )

    candidate_pairs = tuple(
        sorted(
            (
                (customer_id, currency)
                for customer_id, currency in cadence_by_pair
                if customer_id is not None and currency is not None
            ),
            key=lambda item: (str(item[0]), item[1]),
        )
    )
    if not candidate_pairs:
        return []

    rows = (await session.execute(
        select(
            ranked_values.c.customer_id,
            ranked_values.c.currency,
            ranked_values.c.value_window_purchase_count,
            ranked_values.c.observed_retained_revenue,
            ranked_values.c.cohort_size,
            ranked_values.c.observed_value_percentile,
        )
        .where(
            tuple_(
                ranked_values.c.customer_id,
                ranked_values.c.currency,
            ).in_(candidate_pairs),
            ranked_values.c.cohort_size >= HIGH_VALUE_MIN_COHORT_SIZE,
            ranked_values.c.observed_value_percentile
            >= HIGH_VALUE_MIN_PERCENTILE,
        )
        .order_by(
            ranked_values.c.observed_value_percentile.desc(),
            ranked_values.c.observed_retained_revenue.desc(),
            ranked_values.c.customer_id,
            ranked_values.c.currency,
        )
        .limit(MAX_DETECTOR_CANDIDATES)
    )).all()

    ranked: list[tuple[Decimal, Decimal, _GrowthSignal]] = []

    for row in rows:
        customer_id = row.customer_id
        currency = str(row.currency)

        if customer_id is None:
            continue

        repeat_signal = cadence_by_pair.get((customer_id, currency))
        if repeat_signal is None or not repeat_signal.provenance:
            continue

        cohort_size = int(row.cohort_size or 0)
        percentile = _as_decimal(row.observed_value_percentile)
        observed_value = _as_decimal(row.observed_retained_revenue)
        value_window_purchase_count = int(
            row.value_window_purchase_count or 0
        )

        if (
            cohort_size < HIGH_VALUE_MIN_COHORT_SIZE
            or percentile < HIGH_VALUE_MIN_PERCENTILE
            or observed_value <= 0
        ):
            continue

        cadence_evidence = repeat_signal.provenance[0]

        try:
            purchase_count = int(cadence_evidence["purchase_count"])
            overdue_ratio = Decimal(
                str(cadence_evidence["purchase_overdue_ratio"])
            )
            source_id = str(cadence_evidence["source_id"])
            observed_at = str(cadence_evidence["observed_at"])
        except (KeyError, TypeError, ValueError):
            continue

        if (
            purchase_count < REPEAT_PURCHASE_MIN_ORDERS
            or overdue_ratio < REPEAT_PURCHASE_OVERDUE_RATIO
            or not source_id
        ):
            continue

        cohort_evidence = min(
            Decimal("0.120"),
            Decimal(max(0, cohort_size - HIGH_VALUE_MIN_COHORT_SIZE))
            * Decimal("0.005"),
        )
        value_severity = min(
            Decimal("0.100"),
            (percentile - HIGH_VALUE_MIN_PERCENTILE)
            * Decimal("0.500"),
        )
        cadence_severity = min(
            Decimal("0.080"),
            (overdue_ratio - REPEAT_PURCHASE_OVERDUE_RATIO)
            * Decimal("0.060"),
        )

        confidence = _confidence(
            base=Decimal("0.700"),
            evidence=cohort_evidence,
            severity=value_severity + cadence_severity,
        )

        rank_score = min(
            Decimal("0.990"),
            Decimal("0.700")
            + min(
                Decimal("0.170"),
                (percentile - HIGH_VALUE_MIN_PERCENTILE)
                * Decimal("0.850"),
            )
            + min(
                Decimal("0.120"),
                (overdue_ratio - REPEAT_PURCHASE_OVERDUE_RATIO)
                * Decimal("0.080"),
            ),
        ).quantize(Decimal("0.001"))

        signal = _GrowthSignal(
            dedupe_key=(
                "business-growth:high-value-customer-at-risk:"
                f"{customer_id}:{currency}:{source_id}"
            ),
            title="High-value repeat customer cadence risk",
            description=(
                "A repeat customer who is materially beyond their own observed "
                "purchase cadence also ranks in the upper portion of the "
                f"business's same-currency {HIGH_VALUE_LOOKBACK_DAYS}-day "
                "observed retained-revenue cohort. This is observed customer "
                "value, not predicted lifetime value or a churn prediction."
            )[:3000],
            category="high_value_customer_at_risk",
            source="commerce",
            source_entity_type="customer",
            source_entity_id=customer_id,
            customer_id=customer_id,
            reason=(
                f"The customer ranks at observed value percentile "
                f"{percentile:.3f} among {cohort_size} customers in {currency}; "
                f"observed retained revenue over the trailing "
                f"{HIGH_VALUE_LOOKBACK_DAYS} days is "
                f"{observed_value:.2f} {currency}, and the customer's purchase "
                f"cadence is overdue by {overdue_ratio:.2f}x."
            )[:3000],
            confidence=confidence,
            recommendation=(
                "Ask the Sales Copilot to review customer history, recent "
                "interactions, product context, and business policy before "
                "proposing a retention or reorder follow-up."
            ),
            provenance=[{
                "classification": "first_party_observed",
                "detector": "high_value_customer_at_risk",
                "source_type": "orders",
                "source_id": source_id,
                "observed_at": observed_at,
                "currency": currency,
                "purchase_count": purchase_count,
                "value_window_purchase_count": value_window_purchase_count,
                "purchase_overdue_ratio": str(
                    overdue_ratio.quantize(Decimal("0.001"))
                ),
                "observed_retained_revenue": str(observed_value),
                "observed_value_percentile": str(
                    percentile.quantize(Decimal("0.001"))
                ),
                "cohort_size": cohort_size,
                "value_lookback_days": HIGH_VALUE_LOOKBACK_DAYS,
                "order_timestamp_policy": (
                    "manual_created_at_else_provider_created_at_required"
                ),
                "eligible_payment_states": list(REALIZED_PAYMENT_STATES),
                "refund_treatment": (
                    "positive_retained_revenue_only;"
                    "refunded_orders_zero_else_total_minus_refunded_amount"
                ),
                "customer_value_scope": (
                    "observed_trailing_retained_revenue_not_predicted_clv"
                ),
            }],
            priority=(
                "high"
                if (
                    percentile >= Decimal("0.900")
                    or overdue_ratio >= Decimal("2.500")
                )
                else "medium"
            ),
            currency=currency,
            rank_score=rank_score,
        )

        ranked.append((rank_score, confidence, signal))

    ranked.sort(
        key=lambda item: (
            -item[0],
            -item[1],
            item[2].dedupe_key,
        )
    )
    return [
        item[2]
        for item in ranked[:MAX_OPPORTUNITIES_PER_DETECTOR]
    ]



async def _detect_product_affinity_cross_sell(
    session: AsyncSession,
    *,
    business_id: UUID,
    window: _ComparisonWindow,
) -> list[_GrowthSignal]:
    """
    Detect bounded directional product affinity from first-party paid orders.

    Product identity uses only tenant-owned OrderLineItem.catalog_item_id.
    Variant/external IDs are intentionally excluded because they do not provide
    a safe standalone provider/account identity for cross-order matching.

    Co-purchase is an observed association only, never proof of causation.
    """
    occurred_at = _authoritative_order_occurred_at()
    retained_revenue = _retained_order_revenue()
    affinity_start = window.recent_end - timedelta(days=AFFINITY_LOOKBACK_DAYS)

    # One row per eligible order/product prevents duplicate line items for the
    # same catalog product from inflating pair support.
    order_products = (
        select(
            Order.id.label("order_id"),
            OrderLineItem.catalog_item_id.label("catalog_item_id"),
        )
        .join(
            OrderLineItem,
            (OrderLineItem.order_id == Order.id)
            & (OrderLineItem.business_id == Order.business_id),
        )
        .where(
            Order.business_id == business_id,
            OrderLineItem.business_id == business_id,
            OrderLineItem.catalog_item_id.is_not(None),
            Order.status != "canceled",
            Order.payment_status.in_(REALIZED_PAYMENT_STATES),
            occurred_at.is_not(None),
            occurred_at >= affinity_start,
            occurred_at < window.recent_end,
            retained_revenue > Decimal("0.00"),
        )
        .group_by(
            Order.id,
            OrderLineItem.catalog_item_id,
        )
        .subquery("affinity_order_products")
    )

    source_products = order_products.alias("affinity_source_products")
    target_products = order_products.alias("affinity_target_products")

    product_order_counts = (
        select(
            order_products.c.catalog_item_id,
            func.count(
                func.distinct(order_products.c.order_id)
            ).label("product_order_count"),
        )
        .group_by(order_products.c.catalog_item_id)
        .subquery("affinity_product_order_counts")
    )

    source_counts = product_order_counts.alias("affinity_source_counts")
    target_counts = product_order_counts.alias("affinity_target_counts")

    eligible_order_count = (
        select(
            func.count(func.distinct(order_products.c.order_id))
        )
        .scalar_subquery()
    )

    target_item = aliased(CatalogItem)
    pair_support = func.count(
        func.distinct(source_products.c.order_id)
    ).label("co_purchase_order_count")

    pair_rows = (await session.execute(
        select(
            source_products.c.catalog_item_id.label(
                "source_catalog_item_id"
            ),
            target_products.c.catalog_item_id.label(
                "target_catalog_item_id"
            ),
            target_item.name.label("target_catalog_item_name"),
            target_item.availability.label("target_availability"),
            pair_support,
            source_counts.c.product_order_count.label(
                "source_order_count"
            ),
            target_counts.c.product_order_count.label(
                "target_order_count"
            ),
            eligible_order_count.label("eligible_order_count"),
        )
        .join(
            target_products,
            (target_products.c.order_id == source_products.c.order_id)
            & (
                target_products.c.catalog_item_id
                != source_products.c.catalog_item_id
            ),
        )
        .join(
            source_counts,
            source_counts.c.catalog_item_id
            == source_products.c.catalog_item_id,
        )
        .join(
            target_counts,
            target_counts.c.catalog_item_id
            == target_products.c.catalog_item_id,
        )
        .join(
            target_item,
            (target_item.id == target_products.c.catalog_item_id)
            & (target_item.business_id == business_id),
        )
        .where(
            target_item.business_id == business_id,
            *_sellable_product_predicates(target_item),
        )
        .group_by(
            source_products.c.catalog_item_id,
            target_products.c.catalog_item_id,
            target_item.name,
            target_item.availability,
            source_counts.c.product_order_count,
            target_counts.c.product_order_count,
        )
        .order_by(
            pair_support.desc(),
            source_products.c.catalog_item_id,
            target_products.c.catalog_item_id,
        )
        .limit(MAX_DETECTOR_CANDIDATES)
    )).all()

    qualified_pairs: list[
        tuple[
            Decimal,
            Decimal,
            int,
            UUID,
            UUID,
            str,
            str,
            int,
            int,
            int,
        ]
    ] = []

    for row in pair_rows:
        source_item_id = row.source_catalog_item_id
        target_item_id = row.target_catalog_item_id

        if source_item_id is None or target_item_id is None:
            continue
        if source_item_id == target_item_id:
            continue

        total_orders = int(row.eligible_order_count or 0)
        source_orders = int(row.source_order_count or 0)
        target_orders = int(row.target_order_count or 0)
        co_purchase_orders = int(row.co_purchase_order_count or 0)

        if (
            total_orders < AFFINITY_MIN_ELIGIBLE_ORDERS
            or source_orders < AFFINITY_MIN_SOURCE_ORDERS
            or target_orders < AFFINITY_MIN_TARGET_ORDERS
            or co_purchase_orders < AFFINITY_MIN_PAIR_ORDERS
        ):
            continue

        directional_confidence = (
            Decimal(co_purchase_orders) / Decimal(source_orders)
        )
        target_base_rate = (
            Decimal(target_orders) / Decimal(total_orders)
        )

        if target_base_rate <= 0:
            continue

        lift = directional_confidence / target_base_rate

        if (
            directional_confidence
            < AFFINITY_MIN_DIRECTIONAL_CONFIDENCE
            or lift < AFFINITY_MIN_LIFT
        ):
            continue

        qualified_pairs.append((
            lift,
            directional_confidence,
            co_purchase_orders,
            source_item_id,
            target_item_id,
            str(row.target_catalog_item_name),
            str(row.target_availability),
            total_orders,
            source_orders,
            target_orders,
        ))

    qualified_pairs.sort(
        key=lambda item: (
            -item[0],
            -item[1],
            -item[2],
            str(item[3]),
            str(item[4]),
        )
    )
    qualified_pairs = qualified_pairs[:AFFINITY_MAX_DIRECTION_CANDIDATES]

    if not qualified_pairs:
        return []

    ranked: list[
        tuple[Decimal, Decimal, float, int, _GrowthSignal]
    ] = []

    for (
        lift,
        directional_confidence,
        co_purchase_orders,
        source_item_id,
        target_item_id,
        target_item_name,
        target_availability,
        total_orders,
        source_orders,
        target_orders,
    ) in qualified_pairs:
        source_line = aliased(OrderLineItem)
        target_order = aliased(Order)
        target_line = aliased(OrderLineItem)

        target_occurred_at = case(
            (target_order.source == "manual", target_order.created_at),
            else_=target_order.provider_created_at,
        )
        target_retained_revenue = case(
            (
                target_order.payment_status == "refunded",
                Decimal("0.00"),
            ),
            else_=func.greatest(
                target_order.total - target_order.refunded_amount,
                Decimal("0.00"),
            ),
        )

        target_purchase_exists = exists(
            select(target_order.id)
            .join(
                target_line,
                (target_line.order_id == target_order.id)
                & (
                    target_line.business_id
                    == target_order.business_id
                ),
            )
            .where(
                target_order.business_id == business_id,
                target_line.business_id == business_id,
                target_order.customer_id == Order.customer_id,
                target_line.catalog_item_id == target_item_id,
                target_order.status != "canceled",
                target_order.payment_status.in_(REALIZED_PAYMENT_STATES),
                target_occurred_at.is_not(None),
                target_occurred_at < window.recent_end,
                target_retained_revenue > Decimal("0.00"),
            )
        )

        source_purchase_count = func.count(
            func.distinct(Order.id)
        ).label("source_purchase_count")
        last_source_purchase_at = func.max(occurred_at).label(
            "last_source_purchase_at"
        )

        customer_rows = (await session.execute(
            select(
                Order.customer_id,
                source_purchase_count,
                last_source_purchase_at,
            )
            .join(
                source_line,
                (source_line.order_id == Order.id)
                & (source_line.business_id == Order.business_id),
            )
            .join(
                Customer,
                (Customer.id == Order.customer_id)
                & (Customer.business_id == Order.business_id),
            )
            .where(
                Order.business_id == business_id,
                source_line.business_id == business_id,
                Customer.business_id == business_id,
                Customer.active.is_(True),
                Customer.status == "active",
                Order.customer_id.is_not(None),
                source_line.catalog_item_id == source_item_id,
                Order.status != "canceled",
                Order.payment_status.in_(REALIZED_PAYMENT_STATES),
                occurred_at.is_not(None),
                occurred_at >= affinity_start,
                occurred_at < window.recent_end,
                retained_revenue > Decimal("0.00"),
                ~target_purchase_exists,
            )
            .group_by(Order.customer_id)
            .order_by(
                last_source_purchase_at.desc(),
                source_purchase_count.desc(),
                Order.customer_id,
            )
            .limit(AFFINITY_CUSTOMERS_PER_DIRECTION)
        )).all()

        for customer_row in customer_rows:
            customer_id = customer_row.customer_id
            last_source_purchase = customer_row.last_source_purchase_at
            customer_source_purchases = int(
                customer_row.source_purchase_count or 0
            )

            if (
                customer_id is None
                or last_source_purchase is None
                or customer_source_purchases < 1
            ):
                continue

            evidence_boost = min(
                Decimal("0.150"),
                Decimal(
                    max(
                        0,
                        co_purchase_orders - AFFINITY_MIN_PAIR_ORDERS,
                    )
                )
                * Decimal("0.025"),
            )

            severity_boost = min(
                Decimal("0.170"),
                (
                    directional_confidence
                    - AFFINITY_MIN_DIRECTIONAL_CONFIDENCE
                )
                * Decimal("0.240")
                + (
                    lift - AFFINITY_MIN_LIFT
                )
                * Decimal("0.035"),
            )

            confidence = _confidence(
                base=Decimal("0.650"),
                evidence=evidence_boost,
                severity=severity_boost,
            )

            rank_score = min(
                Decimal("0.990"),
                Decimal("0.560")
                + min(
                    Decimal("0.160"),
                    Decimal(
                        max(
                            0,
                            co_purchase_orders
                            - AFFINITY_MIN_PAIR_ORDERS,
                        )
                    )
                    * Decimal("0.025"),
                )
                + min(
                    Decimal("0.160"),
                    (
                        directional_confidence
                        - AFFINITY_MIN_DIRECTIONAL_CONFIDENCE
                    )
                    * Decimal("0.300"),
                )
                + min(
                    Decimal("0.110"),
                    (
                        lift - AFFINITY_MIN_LIFT
                    )
                    * Decimal("0.050"),
                ),
            ).quantize(Decimal("0.001"))

            source_purchase_key = (
                last_source_purchase.astimezone(UTC)
                .strftime("%Y%m%dT%H%M%S")
            )

            signal = _GrowthSignal(
                dedupe_key=(
                    "business-growth:product-affinity-cross-sell:"
                    f"{customer_id}:{source_item_id}:"
                    f"{target_item_id}:{source_purchase_key}"
                ),
                title=(
                    f"Observed cross-sell affinity: {target_item_name}"
                )[:180],
                description=(
                    "First-party paid-order history shows a directional "
                    "association between two catalog products. Customers who "
                    "purchased the source product also purchased the target "
                    f"product in {directional_confidence:.1%} of eligible "
                    "source-product orders, with observed lift "
                    f"{lift:.2f}x above the target product's base purchase "
                    "rate. This is an association, not proof of causation."
                )[:3000],
                category="product_affinity_cross_sell",
                source="commerce",
                source_entity_type="catalog_item",
                source_entity_id=target_item_id,
                customer_id=customer_id,
                reason=(
                    f"The product pair appeared together in "
                    f"{co_purchase_orders} eligible orders. The source "
                    f"product appeared in {source_orders} orders, the target "
                    f"product appeared in {target_orders} orders, and the "
                    f"customer has {customer_source_purchases} observed "
                    "source-product purchases with no recorded eligible "
                    "purchase of the target product."
                )[:3000],
                confidence=confidence,
                recommendation=(
                    "Ask the Sales Copilot to review the customer's current "
                    "order history, product relevance, business policy, and "
                    "current product availability before proposing a "
                    "cross-sell follow-up."
                ),
                provenance=[{
                    "classification": "first_party_observed",
                    "detector": "product_affinity_cross_sell",
                    "source_type": "order_line_items",
                    "source_id": str(target_item_id),
                    "observed_at": last_source_purchase.isoformat(),
                    "source_catalog_item_id": str(source_item_id),
                    "target_catalog_item_id": str(target_item_id),
                    "eligible_order_count": total_orders,
                    "source_order_count": source_orders,
                    "target_order_count": target_orders,
                    "co_purchase_order_count": co_purchase_orders,
                    "directional_confidence": str(
                        directional_confidence.quantize(
                            Decimal("0.001")
                        )
                    ),
                    "affinity_lift": str(
                        lift.quantize(Decimal("0.001"))
                    ),
                    "affinity_lookback_days": AFFINITY_LOOKBACK_DAYS,
                    "customer_source_purchase_count": (
                        customer_source_purchases
                    ),
                    "last_source_purchase_at": (
                        last_source_purchase.isoformat()
                    ),
                    "target_availability": target_availability,
                    "product_identity_scope": (
                        "tenant_catalog_item_id_only;"
                        "external_variant_identity_excluded"
                    ),
                    "affinity_disclaimer": (
                        "Observed co-purchase association only; "
                        "no causal claim."
                    ),
                    "order_timestamp_policy": (
                        "manual_created_at_else_provider_created_at_required"
                    ),
                    "eligible_payment_states": list(
                        REALIZED_PAYMENT_STATES
                    ),
                    "refund_treatment": (
                        "positive_retained_revenue_orders_only"
                    ),
                }],
                priority=(
                    "high"
                    if (
                        directional_confidence >= Decimal("0.500")
                        and lift >= Decimal("2.000")
                        and co_purchase_orders >= 5
                    )
                    else "medium"
                ),
                currency=None,
                rank_score=rank_score,
            )

            ranked.append((
                rank_score,
                confidence,
                last_source_purchase.astimezone(UTC).timestamp(),
                customer_source_purchases,
                signal,
            ))

    ranked.sort(
        key=lambda item: (
            -item[0],
            -item[1],
            -item[2],
            -item[3],
            item[4].dedupe_key,
        )
    )

    # Prevent one customer from receiving several competing affinity
    # Opportunities in the same detector run.
    selected: list[_GrowthSignal] = []
    seen_customers: set[UUID] = set()

    for (
        _rank_score,
        _confidence_value,
        _last_source_purchase,
        _customer_source_purchases,
        signal,
    ) in ranked:
        if signal.customer_id is None:
            continue
        if signal.customer_id in seen_customers:
            continue

        seen_customers.add(signal.customer_id)
        selected.append(signal)

        if len(selected) >= MAX_OPPORTUNITIES_PER_DETECTOR:
            break

    return selected


async def _create_opportunity_if_missing(
    session: AsyncSession,
    *,
    business_id: UUID,
    dedupe_key: str,
    title: str,
    description: str,
    category: str,
    source: str,
    source_entity_type: str | None,
    source_entity_id: UUID | None,
    reason: str,
    confidence: Decimal | None,
    recommendation: str,
    provenance: list[dict[str, object]],
    suggested_action: str = "generate_campaign_proposal",
    priority: str = "medium",
    estimated_value: Decimal | None = None,
    currency: str | None = None,
    customer_id: UUID | None = None,
    enqueue_initial_analysis: bool = False,
) -> bool:
    """
    Atomically create one business-scoped Opportunity.

    The unique (business_id, dedupe_key) database boundary is authoritative.
    Concurrent workers therefore converge on one canonical Opportunity.
    """
    opportunity_id = uuid4()

    try:
        inserted_id = await session.scalar(
            pg_insert(Opportunity)
            .values(
                id=opportunity_id,
                business_id=business_id,
                title=title,
                description=description,
                category=category,
                source=source,
                priority=priority,
                estimated_value=estimated_value,
                currency=currency,
                status="open",
                customer_id=customer_id,
                lead_id=None,
                source_entity_type=source_entity_type,
                source_entity_id=source_entity_id,
                reason=reason,
                confidence=confidence,
                recommendation=recommendation,
                suggested_action=suggested_action,
                provenance=provenance,
                dedupe_key=dedupe_key,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    Opportunity.business_id,
                    Opportunity.dedupe_key,
                ]
            )
            .returning(Opportunity.id)
        )
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError(
            "opportunity_persist_failed"
        ) from None

    if inserted_id is None:
        return False
    if enqueue_initial_analysis:
        try:
            await enqueue_job(
                session,
                business_id=business_id,
                job_type="analyze_business_opportunity",
                idempotency_key=initial_opportunity_analysis_job_key(inserted_id),
                opportunity_id=inserted_id,
            )
        except BackgroundJobError:
            raise AutomationIntelligencePersistenceError(
                "opportunity_analysis_enqueue_failed"
            ) from None
    return True


def _content_channel(connected: list[str]) -> str:
    mapping = {
        "instagram": "instagram", "facebook": "facebook",
        "gmail": "email", "whatsapp_business": "whatsapp",
    }
    for connector in connected:
        if connector in mapping:
            return mapping[connector]
    return "website"


def _content_operational_evidence(
    *,
    observations: list[CompetitorObservation],
    trends: list[MarketingTrend],
    performance: list[MarketingPerformance],
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for item in observations:
        evidence.append({
            "classification": "public_competitor_observation",
            "source_type": "competitor_observation",
            "source_id": f"competitor_observation:{item.id}",
            "source_reference": item.source_reference,
            "observed_at": item.observed_at.isoformat(),
            "summary": f"{item.title}: {item.summary}"[:1200],
            "provenance_role": "provided_to_model",
        })
    for item in trends:
        evidence.append({
            "classification": "sourced_public_signal",
            "source_type": "marketing_trend",
            "source_id": f"marketing_trend:{item.id}",
            "source_reference": item.source_reference,
            "observed_at": item.observed_at.isoformat(),
            "summary": f"{item.title}: {item.description}"[:1200],
            "provenance_role": "provided_to_model",
        })
    for item in performance:
        evidence.append({
            "classification": "first_party_observed",
            "source_type": "marketing_performance",
            "source_id": f"marketing_performance:{item.id}",
            "source_reference": None,
            "observed_at": item.period_end.isoformat(),
            "summary": (
                f"{item.channel} for {item.period_start.isoformat()} through "
                f"{item.period_end.isoformat()}: impressions={item.impressions}, "
                f"clicks={item.clicks}, conversions={item.conversions}, "
                f"spend={item.spend}, revenue={item.revenue}."
            ),
            "provenance_role": "provided_to_model",
        })
    return evidence


def _industry_guardrail(business_type: str) -> str:
    industry = get_business_industry(business_type)
    if is_healthcare_business_type(business_type):
        return (
            "Use service and appointment-availability language only. Never include patient identities, "
            "clinical information, diagnosis, notes, or other PHI."
        )
    if industry and industry.group == "professional_services":
        return "Use services, clients, providers, and bookings terminology."
    if industry and industry.group == "commerce":
        return "Reference products or order/inventory signals only when they exist in trusted context."
    if industry and industry.group == "real_estate":
        return "Use CRM contacts and leads only. Do not treat catalog items as properties or invent listings."
    return "Do not invent products, services, offers, performance, or external facts."


def _lookback():
    from datetime import timedelta
    return timedelta(days=7)


def _finish(
    run: MarketingAutomationRun, status: str, instant: datetime, failure_code: str | None
) -> None:
    run.status = status
    run.started_at = run.started_at or instant
    run.completed_at = instant
    run.failure_code = failure_code


def _valid_locale(value: str) -> bool:
    import re
    return bool(re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", value))


async def _flush(session: AsyncSession) -> None:
    try:
        await session.flush()
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("marketing_automation_persist_failed") from None
