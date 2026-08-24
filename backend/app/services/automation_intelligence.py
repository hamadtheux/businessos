from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import math
import re
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.automation_intelligence import (
    AutomationIntelligenceNotFoundError,
    AutomationIntelligencePersistenceError,
    AutomationIntelligenceProviderError,
    AutomationIntelligenceValidationError,
)
from app.integrations.automation_contracts import (
    CompetitorCandidateResult,
    CompetitorEvidenceResult,
    CompetitorResearchContext,
    CompetitorResearchProvider,
)
from app.integrations.automation_registry import default_competitor_research_provider
from app.models.automation_intelligence import (
    CompetitorCandidate,
    CompetitorCandidateEvidence,
    CompetitorDiscoveryRun,
    MarketingAutomationRun,
)
from app.models.business import Business
from app.models.appointment_type import AppointmentType
from app.models.business_knowledge_entry import BusinessKnowledgeEntry
from app.models.catalog_item import CatalogItem
from app.models.integration import IntegrationConnection
from app.models.marketing import Competitor
from app.services.background_jobs import enqueue_job
from app.services.billing import BillingEntitlementError, require_feature
from app.services.business_brain_assembly import build_business_brain_manifest
from app.services.operations import record_audit
from app.domain.business_industries import get_business_industry, is_healthcare_business_type


DISCOVERY_LIMIT = 20
AUTOMATION_BUSINESS_BATCH = 100
MANUAL_DISCOVERY_COOLDOWN = timedelta(hours=6)
MAX_EVIDENCE_PER_RESULT = 20
MAX_METADATA_BYTES = 8_192
MAX_METADATA_DEPTH = 4
MAX_METADATA_KEYS = 100
MAX_METADATA_STRING = 500
MAX_METADATA_LIST = 50
_PROVIDER_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def discovery_window_key(instant: datetime) -> str:
    current = instant.astimezone(UTC)
    year, week, _ = current.isocalendar()
    return f"{year}-w{week:02d}"


def manual_discovery_window_key(instant: datetime) -> str:
    current = instant.astimezone(UTC)
    bucket = int(current.timestamp()) // int(MANUAL_DISCOVERY_COOLDOWN.total_seconds())
    return f"cooldown-{bucket}"


def content_window(instant: datetime) -> tuple[date, date, str]:
    current = instant.astimezone(UTC).date()
    start = current - timedelta(days=current.weekday())
    end = start + timedelta(days=6)
    year, week, _ = start.isocalendar()
    return start, end, f"{year}-w{week:02d}"


def opportunity_window(instant: datetime) -> tuple[date, date, str]:
    current = instant.astimezone(UTC).date()
    return current, current, current.isoformat()


async def schedule_competitor_discovery(
    session: AsyncSession,
    *,
    business_id: UUID,
    trigger_type: str,
    now: datetime | None = None,
    brain_revision: str | None = None,
) -> tuple[CompetitorDiscoveryRun, bool]:
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    if trigger_type not in {"onboarding", "brain_change", "scheduled", "manual_refresh"}:
        raise AutomationIntelligenceValidationError("trigger_type_invalid")
    if trigger_type == "manual_refresh":
        try:
            locked_business_id = await session.scalar(
                select(Business.id).where(Business.id == business_id).with_for_update()
            )
            if locked_business_id is None:
                raise AutomationIntelligenceNotFoundError("business_not_found")
            recent = await session.scalar(
                select(CompetitorDiscoveryRun)
                .where(
                    CompetitorDiscoveryRun.business_id == business_id,
                    CompetitorDiscoveryRun.trigger_type == "manual_refresh",
                    CompetitorDiscoveryRun.created_at
                    > instant - MANUAL_DISCOVERY_COOLDOWN,
                )
                .order_by(
                    CompetitorDiscoveryRun.created_at.desc(),
                    CompetitorDiscoveryRun.id.desc(),
                )
                .limit(1)
            )
            if recent is not None:
                return recent, False
        except AutomationIntelligenceNotFoundError:
            raise
        except SQLAlchemyError:
            raise AutomationIntelligencePersistenceError(
                "discovery_schedule_failed"
            ) from None
    revision = brain_revision
    if revision is None:
        try:
            revision = (await build_business_brain_manifest(session, business_id)).revision
        except Exception:
            raise AutomationIntelligencePersistenceError("brain_manifest_unavailable") from None
    if trigger_type in {"onboarding", "brain_change"}:
        suffix = revision
    elif trigger_type == "manual_refresh":
        suffix = manual_discovery_window_key(instant)
    else:
        suffix = discovery_window_key(instant)
    key = f"competitor-discovery:{trigger_type}:{suffix}"
    values = {
        "id": uuid4(),
        "business_id": business_id,
        "trigger_type": trigger_type,
        "provider_key": None,
        "brain_revision": revision,
        "idempotency_key": key,
        "status": "queued",
        "candidate_count": 0,
        "results_processed": 0,
        "new_candidates": 0,
        "refreshed_candidates": 0,
        "evidence_added": 0,
        "failure_code": None,
        "started_at": None,
        "completed_at": None,
        "created_at": instant,
        "updated_at": instant,
    }
    try:
        inserted_id = await session.scalar(
            pg_insert(CompetitorDiscoveryRun)
            .values(**values)
            .on_conflict_do_nothing(
                constraint="uq_competitor_discovery_runs_business_idempotency"
            )
            .returning(CompetitorDiscoveryRun.id)
        )
        run = await session.scalar(
            select(CompetitorDiscoveryRun).where(
                CompetitorDiscoveryRun.business_id == business_id,
                CompetitorDiscoveryRun.id == (inserted_id or values["id"]),
            )
        ) if inserted_id else await session.scalar(
            select(CompetitorDiscoveryRun).where(
                CompetitorDiscoveryRun.business_id == business_id,
                CompetitorDiscoveryRun.idempotency_key == key,
            )
        )
        if run is None:
            raise AutomationIntelligencePersistenceError("discovery_schedule_failed")
        created = inserted_id is not None
        if created:
            await enqueue_job(
                session,
                business_id=business_id,
                job_type="discover_competitors",
                idempotency_key=f"competitor-discovery-run:{run.id}",
                competitor_discovery_run_id=run.id,
            )
        return run, created
    except AutomationIntelligencePersistenceError:
        raise
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("discovery_schedule_failed") from None


async def schedule_marketing_automation(
    session: AsyncSession,
    *,
    business_id: UUID,
    run_type: str,
    now: datetime | None = None,
) -> tuple[MarketingAutomationRun, bool]:
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    if run_type == "content_plan":
        start, end, suffix = content_window(instant)
        job_type = "generate_content_plan"
    elif run_type in {"campaign_opportunities", "business_growth"}:
        start, end, suffix = opportunity_window(instant)
        job_type = "analyze_campaign_opportunities"
    else:
        raise AutomationIntelligenceValidationError("run_type_invalid")
    key = f"marketing-automation:{run_type}:{suffix}"
    values = {
        "id": uuid4(),
        "business_id": business_id,
        "run_type": run_type,
        "idempotency_key": key,
        "window_start": start,
        "window_end": end,
        "status": "queued",
        "proposal_count": 0,
        "failure_code": None,
        "started_at": None,
        "completed_at": None,
        "created_at": instant,
        "updated_at": instant,
    }
    try:
        inserted_id = await session.scalar(
            pg_insert(MarketingAutomationRun)
            .values(**values)
            .on_conflict_do_nothing(
                constraint="uq_marketing_automation_runs_business_idempotency"
            )
            .returning(MarketingAutomationRun.id)
        )
        run = await session.scalar(
            select(MarketingAutomationRun).where(
                MarketingAutomationRun.business_id == business_id,
                MarketingAutomationRun.id == (inserted_id or values["id"]),
            )
        ) if inserted_id else await session.scalar(
            select(MarketingAutomationRun).where(
                MarketingAutomationRun.business_id == business_id,
                MarketingAutomationRun.idempotency_key == key,
            )
        )
        if run is None:
            raise AutomationIntelligencePersistenceError("marketing_schedule_failed")
        created = inserted_id is not None
        if created:
            await enqueue_job(
                session,
                business_id=business_id,
                job_type=job_type,
                idempotency_key=f"marketing-automation-run:{run.id}",
                marketing_automation_run_id=run.id,
            )
        return run, created
    except AutomationIntelligencePersistenceError:
        raise
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("marketing_schedule_failed") from None


async def enqueue_due_intelligence_automation(
    session: AsyncSession, *, now: datetime, limit: int = AUTOMATION_BUSINESS_BATCH
) -> int:
    statement = due_intelligence_businesses_statement(now=now, limit=limit)
    try:
        businesses = list((await session.scalars(
            statement
        )).all())
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("business_scan_failed") from None
    created = 0
    for business in businesses:
        _, added = await schedule_competitor_discovery(
            session, business_id=business.id, trigger_type="scheduled", now=now
        )
        created += int(added)
        for run_type in ("content_plan", "campaign_opportunities"):
            _, added = await schedule_marketing_automation(
                session, business_id=business.id, run_type=run_type, now=now
            )
            created += int(added)
    return created


def due_intelligence_businesses_statement(*, now: datetime, limit: int):
    """Select a fair batch that is still missing work in the current windows."""
    instant = now.astimezone(UTC)
    discovery_key = (
        f"competitor-discovery:scheduled:{discovery_window_key(instant)}"
    )
    _content_start, _content_end, content_suffix = content_window(instant)
    _opportunity_start, _opportunity_end, opportunity_suffix = opportunity_window(
        instant
    )
    discovery_scheduled = exists(select(CompetitorDiscoveryRun.id).where(
        CompetitorDiscoveryRun.business_id == Business.id,
        CompetitorDiscoveryRun.idempotency_key == discovery_key,
    ))
    content_scheduled = exists(select(MarketingAutomationRun.id).where(
        MarketingAutomationRun.business_id == Business.id,
        MarketingAutomationRun.idempotency_key
        == f"marketing-automation:content_plan:{content_suffix}",
    ))
    opportunities_scheduled = exists(select(MarketingAutomationRun.id).where(
        MarketingAutomationRun.business_id == Business.id,
        MarketingAutomationRun.idempotency_key
        == f"marketing-automation:campaign_opportunities:{opportunity_suffix}",
    ))
    return (
        select(Business)
        .where(
            Business.status == "active",
            or_(
                ~discovery_scheduled,
                ~content_scheduled,
                ~opportunities_scheduled,
            ),
        )
        .order_by(Business.id)
        .limit(limit)
    )


async def run_competitor_discovery(
    session: AsyncSession,
    *,
    business_id: UUID,
    run_id: UUID,
    provider: CompetitorResearchProvider | None = None,
    now: datetime | None = None,
) -> CompetitorDiscoveryRun:
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    run = await _get_discovery_run(session, business_id=business_id, run_id=run_id, lock=True)
    if run.status in {"completed", "provider_unavailable", "blocked_entitlement"}:
        return run
    try:
        await require_feature(session, business_id=business_id, key="competitor_intelligence")
    except BillingEntitlementError:
        _finish_discovery(run, "blocked_entitlement", instant, "feature_not_entitled")
        await _flush(session)
        return run
    active_provider = provider or default_competitor_research_provider()
    if active_provider is None:
        _finish_discovery(run, "provider_unavailable", instant, "provider_unavailable")
        await _flush(session)
        return run
    run.status = "running"
    run.started_at = run.started_at or instant
    provider_key = active_provider.provider_key.strip()
    if not _PROVIDER_KEY_PATTERN.fullmatch(provider_key):
        raise AutomationIntelligenceProviderError("competitor_provider_result_invalid")
    run.provider_key = provider_key
    run.failure_code = None
    await _flush(session)
    context = await _build_research_context(
        session, business_id=business_id, brain_revision=run.brain_revision or "0" * 64
    )
    try:
        results = tuple(await active_provider.discover(context, limit=DISCOVERY_LIMIT))
    except Exception:
        raise AutomationIntelligenceProviderError("competitor_provider_unavailable") from None
    if len(results) > DISCOVERY_LIMIT or any(
        not isinstance(result, CompetitorCandidateResult) for result in results
    ):
        raise AutomationIntelligenceProviderError("competitor_provider_result_invalid")
    new_candidates = 0
    refreshed_candidates = 0
    evidence_added = 0
    for result in results:
        created, added = await _persist_candidate_result(
            session,
            business_id=business_id,
            run=run,
            result=result,
            now=instant,
        )
        if created:
            new_candidates += 1
        else:
            refreshed_candidates += 1
        evidence_added += added
    run.candidate_count = len(results)
    run.results_processed = len(results)
    run.new_candidates = new_candidates
    run.refreshed_candidates = refreshed_candidates
    run.evidence_added = evidence_added
    _finish_discovery(run, "completed", instant, None)
    await _flush(session)
    return run


async def latest_discovery_run(
    session: AsyncSession, *, business_id: UUID
) -> CompetitorDiscoveryRun | None:
    try:
        return await session.scalar(
            select(CompetitorDiscoveryRun)
            .where(CompetitorDiscoveryRun.business_id == business_id)
            .order_by(CompetitorDiscoveryRun.created_at.desc(), CompetitorDiscoveryRun.id.desc())
            .limit(1)
        )
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("discovery_read_failed") from None


async def manual_refresh_available_at(
    session: AsyncSession, *, business_id: UUID
) -> datetime | None:
    try:
        created_at = await session.scalar(
            select(CompetitorDiscoveryRun.created_at)
            .where(
                CompetitorDiscoveryRun.business_id == business_id,
                CompetitorDiscoveryRun.trigger_type == "manual_refresh",
            )
            .order_by(
                CompetitorDiscoveryRun.created_at.desc(),
                CompetitorDiscoveryRun.id.desc(),
            )
            .limit(1)
        )
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("discovery_read_failed") from None
    return (
        created_at.astimezone(UTC) + MANUAL_DISCOVERY_COOLDOWN
        if created_at is not None
        else None
    )


async def list_candidates(
    session: AsyncSession,
    *,
    business_id: UUID,
    status: str | None = None,
    limit: int = 50,
) -> list[CompetitorCandidate]:
    if not 1 <= limit <= 100:
        raise AutomationIntelligenceValidationError("pagination_invalid")
    statement = select(CompetitorCandidate).where(
        CompetitorCandidate.business_id == business_id
    )
    if status is not None:
        if status not in {"suggested", "confirmed", "dismissed", "monitoring"}:
            raise AutomationIntelligenceValidationError("candidate_status_invalid")
        statement = statement.where(CompetitorCandidate.status == status)
    try:
        return list((await session.scalars(
            statement.order_by(
                CompetitorCandidate.confidence.desc(),
                CompetitorCandidate.last_seen_at.desc(),
                CompetitorCandidate.id,
            ).limit(limit)
        )).all())
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("candidate_read_failed") from None


async def candidate_evidence(
    session: AsyncSession, *, business_id: UUID, candidate_id: UUID
) -> list[CompetitorCandidateEvidence]:
    await _get_candidate(session, business_id=business_id, candidate_id=candidate_id)
    try:
        return list((await session.scalars(
            select(CompetitorCandidateEvidence)
            .where(
                CompetitorCandidateEvidence.business_id == business_id,
                CompetitorCandidateEvidence.candidate_id == candidate_id,
            )
            .order_by(
                CompetitorCandidateEvidence.observed_at.desc(),
                CompetitorCandidateEvidence.id,
            )
            .limit(100)
        )).all())
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("evidence_read_failed") from None


async def change_candidate_status(
    session: AsyncSession,
    *,
    business_id: UUID,
    candidate_id: UUID,
    actor_user_id: UUID,
    status: str,
) -> CompetitorCandidate:
    if status not in {"confirmed", "dismissed", "monitoring"}:
        raise AutomationIntelligenceValidationError("candidate_status_invalid")
    candidate = await _get_candidate(
        session, business_id=business_id, candidate_id=candidate_id, lock=True
    )
    if candidate.status == status:
        return candidate
    if candidate.status == "dismissed" and status != "dismissed":
        raise AutomationIntelligenceValidationError("dismissed_candidate_is_terminal")
    if status in {"confirmed", "monitoring"} and candidate.competitor_id is None:
        competitor = None
        if candidate.website_domain:
            competitor = await session.scalar(
                select(Competitor).where(
                    Competitor.business_id == business_id,
                    Competitor.website_domain == candidate.website_domain,
                )
            )
        if competitor is None:
            competitor = Competitor(
                business_id=business_id,
                name=candidate.name,
                website_domain=candidate.website_domain,
                description=candidate.discovery_reason[:3000],
                active=True,
                notes="Confirmed from an AI-discovered suggestion with retained source evidence.",
                source_candidate_id=candidate.id,
                confirmation_source="ai_suggestion",
            )
            session.add(competitor)
            await _flush(session)
        if competitor.business_id != business_id:
            raise AutomationIntelligenceNotFoundError("candidate_not_found")
        candidate.competitor_id = competitor.id
    candidate.status = status
    await _flush(session)
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        event_type=f"marketing.competitor_candidate_{status}",
        entity_type="competitor_candidate",
        entity_id=candidate.id,
        summary=f"Marked discovered competitor suggestion {candidate.name} as {status}.",
    )
    return candidate


async def latest_marketing_run(
    session: AsyncSession, *, business_id: UUID, run_type: str
) -> MarketingAutomationRun | None:
    if run_type not in {"content_plan", "campaign_opportunities", "business_growth"}:
        raise AutomationIntelligenceValidationError("run_type_invalid")
    try:
        return await session.scalar(
            select(MarketingAutomationRun)
            .where(
                MarketingAutomationRun.business_id == business_id,
                MarketingAutomationRun.run_type == run_type,
            )
            .order_by(MarketingAutomationRun.created_at.desc(), MarketingAutomationRun.id.desc())
            .limit(1)
        )
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("marketing_run_read_failed") from None


async def get_marketing_run(
    session: AsyncSession, *, business_id: UUID, run_id: UUID, lock: bool = False
) -> MarketingAutomationRun:
    statement = select(MarketingAutomationRun).where(
        MarketingAutomationRun.business_id == business_id,
        MarketingAutomationRun.id == run_id,
    )
    if lock:
        statement = statement.with_for_update()
    try:
        run = await session.scalar(statement)
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("marketing_run_read_failed") from None
    if run is None or run.business_id != business_id:
        raise AutomationIntelligenceNotFoundError("marketing_run_not_found")
    return run


async def _build_research_context(
    session: AsyncSession, *, business_id: UUID, brain_revision: str
) -> CompetitorResearchContext:
    try:
        business = await session.scalar(select(Business).where(Business.id == business_id))
        if business is None or business.id != business_id:
            raise AutomationIntelligenceNotFoundError("business_not_found")
        industry = get_business_industry(business.business_type)
        public_service_industry = (
            is_healthcare_business_type(business.business_type)
            or (industry is not None and industry.group == "professional_services")
        )
        if public_service_industry:
            products_and_services = list((await session.scalars(
                select(AppointmentType.name).where(
                    AppointmentType.business_id == business_id,
                    AppointmentType.active.is_(True),
                ).order_by(AppointmentType.id).limit(50)
            )).all())
            knowledge: list[BusinessKnowledgeEntry] = []
        else:
            products_and_services = (
                []
                if industry is not None and industry.group == "real_estate"
                else list((await session.scalars(
                    select(CatalogItem.name).where(
                        CatalogItem.business_id == business_id,
                        CatalogItem.status == "active",
                    ).order_by(CatalogItem.id).limit(50)
                )).all())
            )
            knowledge = list((await session.scalars(
                select(BusinessKnowledgeEntry).where(
                    BusinessKnowledgeEntry.business_id == business_id,
                    BusinessKnowledgeEntry.status == "active",
                    BusinessKnowledgeEntry.category.in_(("general", "marketing", "brand")),
                ).order_by(BusinessKnowledgeEntry.id).limit(20)
            )).all())
        connections = list((await session.scalars(
            select(IntegrationConnection.connector_type).where(
                IntegrationConnection.business_id == business_id,
                IntegrationConnection.status == "connected",
            ).order_by(IntegrationConnection.connector_type).limit(20)
        )).all())
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("research_context_failed") from None
    description = next((item.content[:3000] for item in knowledge if item.category == "general"), None)
    website = _knowledge_value(knowledge, ("website", "domain"))
    location = _knowledge_value(knowledge, ("location", "service area", "area served"))
    return CompetitorResearchContext(
        business_name=business.name,
        industry=business.business_type,
        business_description=description,
        products_and_services=tuple(str(item)[:200] for item in products_and_services),
        location_or_service_area=location,
        website_domain=_domain(website),
        connected_metadata=tuple(str(item) for item in connections),
        brain_revision=brain_revision,
    )


def _knowledge_value(
    entries: list[BusinessKnowledgeEntry], terms: tuple[str, ...]
) -> str | None:
    for entry in entries:
        haystack = f"{entry.title} {entry.content}".casefold()
        if any(term in haystack for term in terms):
            return entry.content[:500]
    return None


def _domain(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip().split()[0]
    parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
    host = parsed.hostname
    return host.casefold() if host else None


def _provider_reference(value: str) -> str:
    reference = value.strip()
    if (
        not reference
        or len(reference) > 2048
        or any(ord(character) < 32 for character in reference)
    ):
        raise AutomationIntelligenceProviderError("competitor_evidence_invalid")
    parsed = urlsplit(reference)
    scheme = parsed.scheme.casefold()
    if scheme in {"http", "https"} and not parsed.hostname:
        raise AutomationIntelligenceProviderError("competitor_evidence_invalid")
    if scheme in {"javascript", "data", "vbscript", "file", "ftp"}:
        raise AutomationIntelligenceProviderError("competitor_evidence_invalid")
    return reference


def _canonical_url(value: str) -> str:
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > 2048
        or "\\" in candidate
        or any(character.isspace() or ord(character) < 32 for character in candidate)
    ):
        raise AutomationIntelligenceProviderError("competitor_provider_result_invalid")
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise AutomationIntelligenceProviderError("competitor_provider_result_invalid")
    try:
        parsed.port
    except ValueError:
        raise AutomationIntelligenceProviderError(
            "competitor_provider_result_invalid"
        ) from None
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path or "",
            parsed.query,
            parsed.fragment,
        )
    )


def _candidate_domain(value: str | None, canonical_url: str | None) -> str | None:
    if value:
        if "://" in value:
            return urlsplit(_canonical_url(value)).hostname.casefold()
        candidate = value.strip().casefold().rstrip(".")
        if (
            not candidate
            or len(candidate) > 253
            or any(character in candidate for character in "/:@?#\\")
            or ".." in candidate
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or not all(char.isalnum() or char == "-" for char in label)
                for label in candidate.split(".")
            )
        ):
            raise AutomationIntelligenceProviderError(
                "competitor_provider_result_invalid"
            )
        return candidate
    return urlsplit(canonical_url).hostname.casefold() if canonical_url else None


def _bounded_metadata(value: object) -> dict[str, object]:
    key_count = 0

    def visit(item: object, depth: int) -> object:
        nonlocal key_count
        if depth > MAX_METADATA_DEPTH:
            raise AutomationIntelligenceProviderError("competitor_evidence_invalid")
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, str):
            if len(item) > MAX_METADATA_STRING:
                raise AutomationIntelligenceProviderError("competitor_evidence_invalid")
            return item
        if isinstance(item, int):
            if abs(item) > 10**15:
                raise AutomationIntelligenceProviderError("competitor_evidence_invalid")
            return item
        if isinstance(item, float):
            if not math.isfinite(item) or abs(item) > 10**15:
                raise AutomationIntelligenceProviderError("competitor_evidence_invalid")
            return item
        if isinstance(item, (list, tuple)):
            if len(item) > MAX_METADATA_LIST:
                raise AutomationIntelligenceProviderError("competitor_evidence_invalid")
            return [visit(child, depth + 1) for child in item]
        if isinstance(item, dict):
            result: dict[str, object] = {}
            for raw_key, child in item.items():
                if not isinstance(raw_key, str) or not raw_key or len(raw_key) > 64:
                    raise AutomationIntelligenceProviderError(
                        "competitor_evidence_invalid"
                    )
                key_count += 1
                if key_count > MAX_METADATA_KEYS:
                    raise AutomationIntelligenceProviderError(
                        "competitor_evidence_invalid"
                    )
                result[raw_key] = visit(child, depth + 1)
            return result
        raise AutomationIntelligenceProviderError("competitor_evidence_invalid")

    if not isinstance(value, dict):
        try:
            value = dict(value) if hasattr(value, "items") else value
        except (TypeError, ValueError):
            raise AutomationIntelligenceProviderError(
                "competitor_evidence_invalid"
            ) from None
    bounded = visit(value, 0)
    if not isinstance(bounded, dict):
        raise AutomationIntelligenceProviderError("competitor_evidence_invalid")
    encoded = json.dumps(
        bounded, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_METADATA_BYTES:
        raise AutomationIntelligenceProviderError("competitor_evidence_invalid")
    return bounded


async def _persist_candidate_result(
    session: AsyncSession,
    *,
    business_id: UUID,
    run: CompetitorDiscoveryRun,
    result: CompetitorCandidateResult,
    now: datetime,
) -> tuple[bool, int]:
    if (
        not isinstance(result, CompetitorCandidateResult)
        or not isinstance(result.name, str)
        or not isinstance(result.discovery_reason, str)
        or not isinstance(result.confidence, Decimal)
        or (
            result.website_domain is not None
            and not isinstance(result.website_domain, str)
        )
        or (
            result.canonical_url is not None
            and not isinstance(result.canonical_url, str)
        )
        or (
            result.industry_relationship is not None
            and not isinstance(result.industry_relationship, str)
        )
        or (
            result.geographic_relationship is not None
            and not isinstance(result.geographic_relationship, str)
        )
        or not isinstance(result.evidence, tuple)
        or any(
            not isinstance(evidence, CompetitorEvidenceResult)
            for evidence in result.evidence
        )
    ):
        raise AutomationIntelligenceProviderError("competitor_provider_result_invalid")
    name = result.name.strip()
    canonical_url = _canonical_url(result.canonical_url) if result.canonical_url else None
    domain = _candidate_domain(result.website_domain, canonical_url)
    reason = result.discovery_reason.strip()
    if (
        not name or len(name) > 180 or not reason or len(reason) > 3000
        or result.confidence < Decimal("0") or result.confidence > Decimal("1")
        or not result.evidence or len(result.evidence) > MAX_EVIDENCE_PER_RESULT
        or (
            result.industry_relationship is not None
            and len(result.industry_relationship.strip()) > 500
        )
        or (
            result.geographic_relationship is not None
            and len(result.geographic_relationship.strip()) > 500
        )
    ):
        raise AutomationIntelligenceProviderError("competitor_provider_result_invalid")
    fingerprint = _hash(f"{domain or ''}|{name.casefold()}")
    try:
        candidate = await session.scalar(
            select(CompetitorCandidate).where(
                CompetitorCandidate.business_id == business_id,
                CompetitorCandidate.fingerprint == fingerprint,
            ).with_for_update()
        )
        if candidate is None:
            created = True
            candidate = CompetitorCandidate(
                business_id=business_id,
                discovery_run_id=run.id,
                competitor_id=None,
                name=name,
                website_domain=domain,
                canonical_url=canonical_url,
                source=run.provider_key or "research_provider",
                discovery_reason=reason,
                confidence=result.confidence,
                industry_relationship=(result.industry_relationship or None),
                geographic_relationship=(result.geographic_relationship or None),
                status="suggested",
                fingerprint=fingerprint,
                discovered_at=now,
                last_seen_at=now,
            )
            session.add(candidate)
            await session.flush()
        else:
            created = False
            candidate.last_seen_at = now
            candidate.confidence = max(candidate.confidence, result.confidence)
            candidate.discovery_reason = reason
        evidence_added = 0
        for evidence in result.evidence:
            if (
                not isinstance(evidence.source_type, str)
                or not isinstance(evidence.source_reference, str)
                or not isinstance(evidence.title, str)
                or not isinstance(evidence.excerpt, str)
                or not isinstance(evidence.observed_at, datetime)
            ):
                raise AutomationIntelligenceProviderError(
                    "competitor_evidence_invalid"
                )
            reference = _provider_reference(evidence.source_reference)
            title = evidence.title.strip()
            excerpt = evidence.excerpt.strip()
            if (
                evidence.source_type not in {
                    "provider_result", "public_url", "public_metadata", "ai_inference"
                }
                or not reference
                or not title
                or len(title) > 300
                or not excerpt
                or len(excerpt) > 4000
                or evidence.observed_at.tzinfo is None
                or evidence.observed_at.utcoffset() is None
            ):
                raise AutomationIntelligenceProviderError("competitor_evidence_invalid")
            observed_at = evidence.observed_at.astimezone(UTC)
            if (
                observed_at < datetime(1970, 1, 1, tzinfo=UTC)
                or observed_at > now + timedelta(minutes=5)
            ):
                raise AutomationIntelligenceProviderError("competitor_evidence_invalid")
            safe_metadata = _bounded_metadata(evidence.safe_metadata)
            evidence_fingerprint = _hash(
                f"{evidence.source_type}|{reference}|{excerpt}"
            )
            exists = await session.scalar(
                select(func.count(CompetitorCandidateEvidence.id)).where(
                    CompetitorCandidateEvidence.business_id == business_id,
                    CompetitorCandidateEvidence.candidate_id == candidate.id,
                    CompetitorCandidateEvidence.discovery_run_id == run.id,
                    CompetitorCandidateEvidence.fingerprint == evidence_fingerprint,
                )
            )
            if not exists:
                session.add(CompetitorCandidateEvidence(
                    business_id=business_id,
                    candidate_id=candidate.id,
                    discovery_run_id=run.id,
                    source_type=evidence.source_type,
                    source_reference=reference,
                    title=title,
                    excerpt=excerpt,
                    observed_at=observed_at,
                    safe_metadata=safe_metadata,
                    fingerprint=evidence_fingerprint,
                ))
                evidence_added += 1
        await session.flush()
        return created, evidence_added
    except AutomationIntelligenceProviderError:
        raise
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("candidate_persist_failed") from None


async def _get_discovery_run(
    session: AsyncSession, *, business_id: UUID, run_id: UUID, lock: bool
) -> CompetitorDiscoveryRun:
    statement = select(CompetitorDiscoveryRun).where(
        CompetitorDiscoveryRun.business_id == business_id,
        CompetitorDiscoveryRun.id == run_id,
    )
    if lock:
        statement = statement.with_for_update()
    try:
        run = await session.scalar(statement)
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("discovery_read_failed") from None
    if run is None or run.business_id != business_id:
        raise AutomationIntelligenceNotFoundError("discovery_run_not_found")
    return run


async def _get_candidate(
    session: AsyncSession, *, business_id: UUID, candidate_id: UUID, lock: bool = False
) -> CompetitorCandidate:
    statement = select(CompetitorCandidate).where(
        CompetitorCandidate.business_id == business_id,
        CompetitorCandidate.id == candidate_id,
    )
    if lock:
        statement = statement.with_for_update()
    try:
        candidate = await session.scalar(statement)
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("candidate_read_failed") from None
    if candidate is None or candidate.business_id != business_id:
        raise AutomationIntelligenceNotFoundError("candidate_not_found")
    return candidate


def _finish_discovery(
    run: CompetitorDiscoveryRun, status: str, instant: datetime, failure_code: str | None
) -> None:
    run.status = status
    run.started_at = run.started_at or instant
    run.completed_at = instant
    run.failure_code = failure_code


async def _flush(session: AsyncSession) -> None:
    try:
        await session.flush()
    except SQLAlchemyError:
        raise AutomationIntelligencePersistenceError("automation_persist_failed") from None


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
