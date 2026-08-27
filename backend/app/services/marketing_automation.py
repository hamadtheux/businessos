from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError

from app.agents.provider import AIAgentProvider
from app.domain.business_industries import get_business_industry, is_healthcare_business_type
from app.exceptions.automation_intelligence import (
    AutomationIntelligencePersistenceError,
    AutomationIntelligenceProviderError,
)
from app.exceptions.business_brain import BusinessBrainAssemblyError
from app.exceptions.marketing import MarketingAIError
from app.models.automation_intelligence import MarketingAutomationRun
from app.models.business import Business
from app.models.integration import IntegrationConnection
from app.models.marketing import (
    Competitor,
    CompetitorObservation,
    MarketingContent,
    MarketingPerformance,
    MarketingTrend,
)
from app.models.notification import Notification
from app.models.opportunity import Opportunity
from app.services.automation_intelligence import get_marketing_run
from app.services.billing import BillingEntitlementError, require_feature
from app.services.business_brain_assembly import build_business_brain_manifest
from app.schemas.marketing import ScheduledContentProposal
from app.services.marketing import _execute_cmo
from app.services.operations import record_audit


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


async def _create_opportunity_if_missing(
    session: AsyncSession,
    *,
    business_id: UUID,
    dedupe_key: str,
    title: str,
    description: str,
    category: str,
    source: str,
    source_entity_type: str,
    source_entity_id: UUID,
    reason: str,
    confidence,
    recommendation: str,
    provenance: list[dict[str, object]],
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
                priority="medium",
                estimated_value=None,
                currency=None,
                status="open",
                customer_id=None,
                lead_id=None,
                source_entity_type=source_entity_type,
                source_entity_id=source_entity_id,
                reason=reason,
                confidence=confidence,
                recommendation=recommendation,
                suggested_action="generate_campaign_proposal",
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

    return inserted_id is not None


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
