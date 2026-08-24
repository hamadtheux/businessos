from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.background_jobs import (
    JOB_FAILURE_CODES,
    JOB_POLICIES,
    JobType,
    require_job_policy,
)
from app.exceptions.background_jobs import (
    BackgroundJobNotFoundError,
    BackgroundJobPersistenceError,
    BackgroundJobStateError,
    BackgroundJobValidationError,
)
from app.models.action_execution_attempt import ActionExecutionAttempt
from app.models.automation_intelligence import CompetitorDiscoveryRun, MarketingAutomationRun
from app.models.automation import (
    AutomationEvent,
    AutomationNodeRun,
    AutomationWorkflow,
    AutomationWorkflowRun,
)
from app.models.background_job import BackgroundJob, WorkerInstance
from app.models.billing import BusinessSubscription
from app.models.integration import IntegrationWebhookEvent
from app.models.marketing import SocialSchedule
from app.models.notification import Notification
from app.services.operations import record_audit


MAX_JOB_PAGE_SIZE: Final = 100
_REFERENCE_MODELS = {
    "automation_event_id": AutomationEvent,
    "workflow_id": AutomationWorkflow,
    "workflow_run_id": AutomationWorkflowRun,
    "node_run_id": AutomationNodeRun,
    "integration_event_id": IntegrationWebhookEvent,
    "action_execution_attempt_id": ActionExecutionAttempt,
    "social_schedule_id": SocialSchedule,
    "subscription_id": BusinessSubscription,
    "competitor_discovery_run_id": CompetitorDiscoveryRun,
    "marketing_automation_run_id": MarketingAutomationRun,
}


async def enqueue_job(
    session: AsyncSession,
    *,
    business_id: UUID,
    job_type: JobType,
    idempotency_key: str,
    available_at: datetime | None = None,
    automation_event_id: UUID | None = None,
    workflow_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    node_run_id: UUID | None = None,
    integration_event_id: UUID | None = None,
    action_execution_attempt_id: UUID | None = None,
    social_schedule_id: UUID | None = None,
    subscription_id: UUID | None = None,
    competitor_discovery_run_id: UUID | None = None,
    marketing_automation_run_id: UUID | None = None,
    scheduled_occurrence_at: datetime | None = None,
) -> BackgroundJob:
    """Enqueue one server-defined job without accepting arbitrary payloads."""
    policy = require_job_policy(job_type)
    normalized_key = idempotency_key.strip()
    if not normalized_key or len(normalized_key) > 200:
        raise BackgroundJobValidationError("idempotency_key_invalid")
    references = {
        "automation_event_id": automation_event_id,
        "workflow_id": workflow_id,
        "workflow_run_id": workflow_run_id,
        "node_run_id": node_run_id,
        "integration_event_id": integration_event_id,
        "action_execution_attempt_id": action_execution_attempt_id,
        "social_schedule_id": social_schedule_id,
        "subscription_id": subscription_id,
        "competitor_discovery_run_id": competitor_discovery_run_id,
        "marketing_automation_run_id": marketing_automation_run_id,
    }
    if references[policy.reference_field] is None:
        raise BackgroundJobValidationError("job_reference_required")
    permitted = {policy.reference_field}
    if job_type == "resume_workflow_run":
        permitted.add("node_run_id")
    if any(value is not None and field not in permitted for field, value in references.items()):
        raise BackgroundJobValidationError("job_reference_invalid")
    if (job_type == "process_scheduled_workflow") != (scheduled_occurrence_at is not None):
        raise BackgroundJobValidationError("scheduled_occurrence_invalid")
    for field, reference_id in references.items():
        if reference_id is not None:
            await _require_tenant_reference(
                session, field=field, reference_id=reference_id, business_id=business_id,
            )

    now = datetime.now(UTC)
    values = {
        "id": uuid4(),
        "business_id": business_id,
        "job_type": policy.job_type,
        "status": "queued",
        "priority": policy.priority,
        "idempotency_key": normalized_key,
        "attempt_count": 0,
        "max_attempts": policy.max_attempts,
        "available_at": (available_at or now).astimezone(UTC),
        "claimed_at": None,
        "lease_expires_at": None,
        "worker_id": None,
        "completed_at": None,
        "failure_code": None,
        "scheduled_occurrence_at": (
            scheduled_occurrence_at.astimezone(UTC) if scheduled_occurrence_at else None
        ),
        "created_at": now,
        "updated_at": now,
        **references,
    }
    try:
        inserted_id = await session.scalar(
            pg_insert(BackgroundJob)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_background_jobs_idempotency_key")
            .returning(BackgroundJob.id)
        )
        job = await session.scalar(select(BackgroundJob).where(
            BackgroundJob.id == (inserted_id or values["id"])
        )) if inserted_id else await session.scalar(select(BackgroundJob).where(
            BackgroundJob.idempotency_key == normalized_key
        ))
    except SQLAlchemyError:
        raise BackgroundJobPersistenceError("job_enqueue_failed") from None
    if job is None or job.business_id != business_id or job.job_type != job_type:
        raise BackgroundJobValidationError("idempotency_key_conflict")
    return job


async def claim_jobs(
    session: AsyncSession,
    *,
    worker_id: str,
    batch_size: int,
    lease_seconds: int,
    now: datetime | None = None,
) -> list[BackgroundJob]:
    if not worker_id.strip() or len(worker_id) > 96:
        raise BackgroundJobValidationError("worker_id_invalid")
    if not 1 <= batch_size <= 100 or not 10 <= lease_seconds <= 900:
        raise BackgroundJobValidationError("claim_parameters_invalid")
    claimed_at = (now or datetime.now(UTC)).astimezone(UTC)
    recoverable_job_types = tuple(
        job_type for job_type, policy in JOB_POLICIES.items() if policy.lease_recoverable
    )
    eligible = or_(
        (
            (BackgroundJob.status == "queued")
            & (BackgroundJob.available_at <= claimed_at)
            & (BackgroundJob.attempt_count < BackgroundJob.max_attempts)
        ),
        (
            (BackgroundJob.status == "processing")
            & (BackgroundJob.lease_expires_at <= claimed_at)
            & (BackgroundJob.attempt_count < BackgroundJob.max_attempts)
            & (BackgroundJob.job_type.in_(recoverable_job_types))
        ),
    )
    tenant_rank = func.row_number().over(
        partition_by=BackgroundJob.business_id,
        order_by=(
            BackgroundJob.priority.desc(),
            BackgroundJob.available_at.asc(),
            BackgroundJob.id.asc(),
        ),
    ).label("tenant_rank")
    ranked = (
        select(BackgroundJob.id.label("job_id"), tenant_rank)
        .where(eligible)
        .subquery("fair_job_candidates")
    )
    try:
        jobs = list((await session.scalars(
            select(BackgroundJob)
            .join(ranked, ranked.c.job_id == BackgroundJob.id)
            .order_by(
                ranked.c.tenant_rank.asc(),
                BackgroundJob.priority.desc(),
                BackgroundJob.available_at.asc(),
                BackgroundJob.id.asc(),
            )
            .limit(batch_size)
            .with_for_update(of=BackgroundJob, skip_locked=True)
        )).all())
        for job in jobs:
            job.status = "processing"
            job.attempt_count += 1
            job.claimed_at = claimed_at
            job.lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
            job.worker_id = worker_id
            job.completed_at = None
            job.failure_code = None
        await session.flush()
    except SQLAlchemyError:
        raise BackgroundJobPersistenceError("job_claim_failed") from None
    return jobs


async def record_job_success(
    session: AsyncSession, *, job_id: UUID, worker_id: str,
) -> BackgroundJob:
    job = await _lock_job(session, job_id=job_id)
    _require_claim_owner(job, worker_id)
    job.status = "succeeded"
    job.completed_at = datetime.now(UTC)
    job.failure_code = None
    await _synchronize_linked_run(
        session,
        job=job,
        status="completed",
        instant=job.completed_at,
        failure_code=None,
    )
    await _flush(session)
    return job


async def record_job_failure(
    session: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    failure_code: str,
    retryable: bool,
) -> BackgroundJob:
    if failure_code not in JOB_FAILURE_CODES:
        raise BackgroundJobValidationError("failure_code_invalid")
    job = await _lock_job(session, job_id=job_id)
    _require_claim_owner(job, worker_id)
    policy = require_job_policy(job.job_type)
    now = datetime.now(UTC)
    if retryable and policy.retryable and job.attempt_count < job.max_attempts:
        job.status = "queued"
        job.available_at = now + _retry_delay(job.id, job.attempt_count)
        job.claimed_at = None
        job.lease_expires_at = None
        job.worker_id = None
        job.completed_at = None
        job.failure_code = None
    else:
        exhausted = retryable and policy.retryable and job.attempt_count >= job.max_attempts
        job.status = "dead_letter" if exhausted else "failed"
        job.completed_at = now
        job.failure_code = "retry_exhausted" if exhausted else failure_code
        if exhausted:
            _add_failure_notification(session, job)
        await _synchronize_linked_run(
            session,
            job=job,
            status="failed",
            instant=now,
            failure_code=job.failure_code,
        )
    await _flush(session)
    return job


async def dead_letter_exhausted_leases(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 100,
) -> int:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    jobs = list((await session.scalars(
        select(BackgroundJob)
        .where(
            BackgroundJob.status == "processing",
            BackgroundJob.lease_expires_at <= timestamp,
            BackgroundJob.attempt_count >= BackgroundJob.max_attempts,
        )
        .order_by(BackgroundJob.lease_expires_at, BackgroundJob.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )).all())
    for job in jobs:
        job.status = "dead_letter"
        job.failure_code = "retry_exhausted"
        job.completed_at = timestamp
        _add_failure_notification(session, job)
        await _synchronize_linked_run(
            session,
            job=job,
            status="failed",
            instant=timestamp,
            failure_code="retry_exhausted",
        )
    await _flush(session)
    return len(jobs)


async def list_jobs(
    session: AsyncSession,
    *,
    business_id: UUID,
    status: str | None,
    job_type: str | None,
    page: int,
    page_size: int,
) -> tuple[list[BackgroundJob], int]:
    if page < 1 or not 1 <= page_size <= MAX_JOB_PAGE_SIZE:
        raise BackgroundJobValidationError("pagination_invalid")
    conditions = [BackgroundJob.business_id == business_id]
    if status:
        conditions.append(BackgroundJob.status == status)
    if job_type:
        require_job_policy(job_type)
        conditions.append(BackgroundJob.job_type == job_type)
    total = int(await session.scalar(
        select(func.count()).select_from(BackgroundJob).where(*conditions)
    ) or 0)
    jobs = list((await session.scalars(
        select(BackgroundJob)
        .where(*conditions)
        .order_by(BackgroundJob.created_at.desc(), BackgroundJob.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).all())
    return jobs, total


async def get_job(
    session: AsyncSession, *, business_id: UUID, job_id: UUID,
) -> BackgroundJob:
    job = await session.scalar(select(BackgroundJob).where(
        BackgroundJob.id == job_id, BackgroundJob.business_id == business_id,
    ))
    if job is None:
        raise BackgroundJobNotFoundError("job_not_found")
    return job


async def retry_job(
    session: AsyncSession,
    *,
    business_id: UUID,
    job_id: UUID,
    actor_user_id: UUID,
) -> BackgroundJob:
    job = await _lock_tenant_job(session, business_id=business_id, job_id=job_id)
    policy = require_job_policy(job.job_type)
    if job.status not in {"failed", "dead_letter"} or not policy.manually_retryable:
        raise BackgroundJobStateError("job_not_retryable")
    before = job.status
    job.status = "queued"
    job.attempt_count = 0
    job.available_at = datetime.now(UTC)
    job.claimed_at = None
    job.lease_expires_at = None
    job.worker_id = None
    job.completed_at = None
    job.failure_code = None
    await _synchronize_linked_run(
        session,
        job=job,
        status="queued",
        instant=job.available_at,
        failure_code=None,
    )
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        event_type="processing.job_retried",
        entity_type="background_job",
        entity_id=job.id,
        summary="Manually retried a safe internal background job.",
        before_value=before,
        after_value="queued",
    )
    await _flush(session)
    return job


async def cancel_job(
    session: AsyncSession,
    *,
    business_id: UUID,
    job_id: UUID,
    actor_user_id: UUID,
) -> BackgroundJob:
    job = await _lock_tenant_job(session, business_id=business_id, job_id=job_id)
    if job.status != "queued":
        raise BackgroundJobStateError("only_queued_jobs_cancelable")
    job.status = "canceled"
    job.completed_at = datetime.now(UTC)
    await _synchronize_linked_run(
        session,
        job=job,
        status="failed",
        instant=job.completed_at,
        failure_code="job_canceled",
    )
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        event_type="processing.job_canceled",
        entity_type="background_job",
        entity_id=job.id,
        summary="Canceled queued internal work; already committed effects were not undone.",
        before_value="queued",
        after_value="canceled",
    )
    await _flush(session)
    return job


async def upsert_worker_heartbeat(
    session: AsyncSession,
    *,
    worker_id: str,
    role: str,
    version: str,
    status: str = "running",
    now: datetime | None = None,
) -> None:
    if role not in {"worker", "scheduler"} or status not in {"running", "stopping", "stopped"}:
        raise BackgroundJobValidationError("worker_state_invalid")
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    values = {
        "worker_id": worker_id,
        "role": role,
        "version": version[:64],
        "status": status,
        "started_at": timestamp,
        "last_heartbeat_at": timestamp,
        "stopped_at": timestamp if status == "stopped" else None,
    }
    await session.execute(
        pg_insert(WorkerInstance).values(**values).on_conflict_do_update(
            index_elements=[WorkerInstance.worker_id],
            set_={
                "role": role,
                "version": version[:64],
                "status": status,
                "last_heartbeat_at": timestamp,
                "stopped_at": timestamp if status == "stopped" else None,
            },
        )
    )
    await _flush(session)


async def processing_health(
    session: AsyncSession, *, business_id: UUID,
) -> dict[str, object]:
    now = datetime.now(UTC)
    counts = {
        status: int(value)
        for status, value in (await session.execute(
            select(BackgroundJob.status, func.count())
            .where(BackgroundJob.business_id == business_id)
            .group_by(BackgroundJob.status)
        )).all()
    }
    oldest = await session.scalar(select(func.min(BackgroundJob.created_at)).where(
        BackgroundJob.business_id == business_id,
        BackgroundJob.status == "queued",
    ))
    average_latency = await session.scalar(select(func.avg(
        func.extract("epoch", BackgroundJob.completed_at - BackgroundJob.created_at)
    )).where(
        BackgroundJob.business_id == business_id,
        BackgroundJob.status == "succeeded",
        BackgroundJob.completed_at.is_not(None),
    ))
    event_backlog = int(await session.scalar(
        select(func.count()).select_from(AutomationEvent).where(
            AutomationEvent.business_id == business_id,
            AutomationEvent.status.in_(("pending", "processing", "failed")),
        )
    ) or 0)
    heartbeat_rows = (await session.execute(
        select(
            WorkerInstance.role,
            func.max(WorkerInstance.last_heartbeat_at),
        ).where(WorkerInstance.status == "running").group_by(WorkerInstance.role)
    )).all()
    heartbeats = {role: value for role, value in heartbeat_rows}
    return {
        "counts": {key: counts.get(key, 0) for key in (
            "queued", "processing", "succeeded", "failed", "dead_letter", "canceled",
        )},
        "automation_event_backlog": event_backlog,
        "oldest_queued_job_age_seconds": (
            max(0.0, (now - oldest).total_seconds()) if oldest else None
        ),
        "average_processing_latency_seconds": (
            float(average_latency) if average_latency is not None else None
        ),
        "worker_last_heartbeat_at": heartbeats.get("worker"),
        "scheduler_last_heartbeat_at": heartbeats.get("scheduler"),
    }


async def _require_tenant_reference(
    session: AsyncSession, *, field: str, reference_id: UUID, business_id: UUID,
) -> None:
    model = _REFERENCE_MODELS[field]
    found = await session.scalar(select(model.id).where(
        model.id == reference_id, model.business_id == business_id,
    ))
    if found is None:
        raise BackgroundJobValidationError("job_reference_invalid")


async def _lock_job(session: AsyncSession, *, job_id: UUID) -> BackgroundJob:
    job = await session.scalar(
        select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update()
    )
    if job is None:
        raise BackgroundJobNotFoundError("job_not_found")
    return job


async def _lock_tenant_job(
    session: AsyncSession, *, business_id: UUID, job_id: UUID,
) -> BackgroundJob:
    job = await session.scalar(select(BackgroundJob).where(
        BackgroundJob.id == job_id, BackgroundJob.business_id == business_id,
    ).with_for_update())
    if job is None:
        raise BackgroundJobNotFoundError("job_not_found")
    return job


def _require_claim_owner(job: BackgroundJob, worker_id: str) -> None:
    if job.status != "processing" or job.worker_id != worker_id:
        raise BackgroundJobStateError("job_claim_lost")


def _retry_delay(job_id: UUID, attempt_count: int) -> timedelta:
    base = min(300, 2 ** min(attempt_count, 8))
    jitter_milliseconds = job_id.int % 1000
    return timedelta(seconds=base, milliseconds=jitter_milliseconds)


def _add_failure_notification(session: AsyncSession, job: BackgroundJob) -> None:
    session.add(Notification(
        business_id=job.business_id,
        recipient_user_id=None,
        category="processing_failure",
        title="Internal work needs attention",
        message=f"A {job.job_type.replace('_', ' ')} job exhausted its safe retries.",
        priority="high",
        read=False,
        related_entity_type="background_job",
        related_entity_id=job.id,
    ))


async def _synchronize_linked_run(
    session: AsyncSession,
    *,
    job: BackgroundJob,
    status: str,
    instant: datetime,
    failure_code: str | None,
) -> None:
    """Keep a terminal/retried job consistent with its tenant-owned run."""
    if job.competitor_discovery_run_id is not None:
        run = await session.scalar(
            select(CompetitorDiscoveryRun)
            .where(
                CompetitorDiscoveryRun.id == job.competitor_discovery_run_id,
                CompetitorDiscoveryRun.business_id == job.business_id,
            )
            .with_for_update()
        )
    elif job.marketing_automation_run_id is not None:
        run = await session.scalar(
            select(MarketingAutomationRun)
            .where(
                MarketingAutomationRun.id == job.marketing_automation_run_id,
                MarketingAutomationRun.business_id == job.business_id,
            )
            .with_for_update()
        )
    else:
        return
    if run is None:
        raise BackgroundJobValidationError("job_reference_invalid")
    terminal = {
        "completed",
        "provider_unavailable",
        "blocked_entitlement",
        "failed",
    }
    if status == "queued":
        if run.status == "failed":
            run.status = "queued"
            run.failure_code = None
            run.started_at = None
            run.completed_at = None
        return
    if run.status in terminal:
        return
    run.status = status
    run.started_at = run.started_at or instant
    run.completed_at = instant
    run.failure_code = failure_code


async def _flush(session: AsyncSession) -> None:
    try:
        await session.flush()
    except SQLAlchemyError:
        raise BackgroundJobPersistenceError("job_persistence_failed") from None
