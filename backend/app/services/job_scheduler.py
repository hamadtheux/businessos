from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_execution_attempt import ActionExecutionAttempt
from app.models.automation import AutomationEvent, AutomationNodeRun, AutomationWorkflow, AutomationWorkflowRun
from app.models.background_job import BackgroundJob
from app.models.integration import IntegrationWebhookEvent
from app.models.marketing import SocialSchedule
from app.models.billing import BusinessSubscription
from app.models.notification import Notification
from app.schemas.automation import ScheduleDefinition
from app.services.automation import _next_schedule
from app.services.background_jobs import dead_letter_exhausted_leases, enqueue_job
from app.services.automation_intelligence import enqueue_due_intelligence_automation


async def enqueue_due_work(
    session: AsyncSession,
    *,
    batch_size: int,
    now: datetime | None = None,
) -> dict[str, int]:
    """Discover bounded due work; row locks coordinate multiple schedulers."""
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    counts: dict[str, int] = {}
    counts["expired_job_leases"] = await dead_letter_exhausted_leases(
        session, now=instant, limit=batch_size,
    )
    counts["scheduled_workflows"] = await _scheduled_workflows(
        session, instant=instant, limit=batch_size,
    )
    counts["automation_events"] = await _automation_events(
        session, instant=instant, limit=batch_size,
    )
    counts["workflow_runs"] = await _workflow_runs(
        session, instant=instant, limit=batch_size,
    )
    counts["delayed_nodes"] = await _delayed_nodes(
        session, instant=instant, limit=batch_size,
    )
    counts["integration_events"] = await _integration_events(
        session, instant=instant, limit=batch_size,
    )
    counts["action_attempts"] = await _queued_action_attempts(
        session, instant=instant, limit=batch_size,
    )
    counts["uncertain_attempts"] = await _uncertain_attempts(
        session, instant=instant, limit=batch_size,
    )
    counts["social_schedules"] = await _social_schedules(
        session, instant=instant, limit=batch_size,
    )
    counts["subscriptions"] = await _subscriptions(
        session, instant=instant, limit=batch_size,
    )
    counts["intelligence_automation"] = await enqueue_due_intelligence_automation(
        session, now=instant, limit=min(batch_size, 100),
    )
    return counts


async def _scheduled_workflows(session: AsyncSession, *, instant: datetime, limit: int) -> int:
    workflows = list((await session.scalars(select(AutomationWorkflow).where(
        AutomationWorkflow.status == "active",
        AutomationWorkflow.enabled.is_(True),
        AutomationWorkflow.trigger_type == "scheduled_time",
        AutomationWorkflow.next_run_at.is_not(None),
        AutomationWorkflow.next_run_at <= instant,
    ).order_by(
        AutomationWorkflow.next_run_at, AutomationWorkflow.id,
    ).limit(limit).with_for_update(skip_locked=True))).all())
    count = 0
    for workflow in workflows:
        occurrence = workflow.next_run_at
        if occurrence is None:
            continue
        try:
            schedule = ScheduleDefinition.model_validate(workflow.schedule_definition)
            next_run_at = _next_schedule(schedule, workflow.timezone, instant)
        except (ValidationError, ValueError):
            # Invalid durable schedule state is skipped and disabled from
            # repeated hot-loop scanning. Operators can correct/reactivate it.
            workflow.next_run_at = None
            session.add(Notification(
                business_id=workflow.business_id,
                recipient_user_id=None,
                category="processing_failure",
                title="Workflow schedule needs attention",
                message="An active workflow schedule was invalid and has been removed from due processing.",
                priority="high",
                read=False,
                related_entity_type="automation_workflow",
                related_entity_id=workflow.id,
            ))
            continue
        await enqueue_job(
            session,
            business_id=workflow.business_id,
            job_type="process_scheduled_workflow",
            idempotency_key=f"schedule:{workflow.id}:{occurrence.isoformat()}",
            workflow_id=workflow.id,
            scheduled_occurrence_at=occurrence,
        )
        # Bounded missed-run policy: enqueue at most the persisted due
        # occurrence, then jump directly to the next future occurrence.
        workflow.next_run_at = next_run_at
        count += 1
    return count


async def _automation_events(session: AsyncSession, *, instant: datetime, limit: int) -> int:
    events = list((await session.scalars(select(AutomationEvent).where(
        AutomationEvent.status == "pending",
    ).order_by(AutomationEvent.occurred_at, AutomationEvent.id).limit(limit)
        .with_for_update(skip_locked=True))).all())
    for event in events:
        await enqueue_job(
            session,
            business_id=event.business_id,
            job_type="process_automation_event",
            idempotency_key=f"automation-event:{event.id}",
            automation_event_id=event.id,
        )
    return len(events)


async def _workflow_runs(session: AsyncSession, *, instant: datetime, limit: int) -> int:
    runs = list((await session.scalars(select(AutomationWorkflowRun).where(
        AutomationWorkflowRun.status.in_(("queued", "running")),
    ).order_by(AutomationWorkflowRun.created_at, AutomationWorkflowRun.id).limit(limit)
        .with_for_update(skip_locked=True))).all())
    for run in runs:
        await enqueue_job(
            session,
            business_id=run.business_id,
            job_type="resume_workflow_run",
            idempotency_key=f"workflow-start:{run.id}",
            workflow_run_id=run.id,
        )
    return len(runs)


async def _delayed_nodes(session: AsyncSession, *, instant: datetime, limit: int) -> int:
    rows = (await session.execute(select(AutomationNodeRun, AutomationWorkflowRun).join(
        AutomationWorkflowRun,
        (AutomationWorkflowRun.id == AutomationNodeRun.workflow_run_id)
        & (AutomationWorkflowRun.business_id == AutomationNodeRun.business_id),
    ).where(
        AutomationNodeRun.status == "waiting",
        AutomationNodeRun.resume_at.is_not(None),
        AutomationNodeRun.resume_at <= instant,
        AutomationWorkflowRun.status == "waiting",
    ).order_by(AutomationNodeRun.resume_at, AutomationNodeRun.id).limit(limit)
        .with_for_update(skip_locked=True, of=AutomationNodeRun))).all()
    for node, run in rows:
        await enqueue_job(
            session,
            business_id=node.business_id,
            job_type="resume_workflow_run",
            idempotency_key=f"workflow-resume:{run.id}:{node.id}:{node.resume_at.isoformat()}",
            workflow_run_id=run.id,
            node_run_id=node.id,
        )
    return len(rows)


async def _integration_events(session: AsyncSession, *, instant: datetime, limit: int) -> int:
    events = list((await session.scalars(select(IntegrationWebhookEvent).where(
        IntegrationWebhookEvent.status == "received",
    ).order_by(IntegrationWebhookEvent.received_at, IntegrationWebhookEvent.id).limit(limit)
        .with_for_update(skip_locked=True))).all())
    for event in events:
        await enqueue_job(
            session,
            business_id=event.business_id,
            job_type="process_integration_event",
            idempotency_key=f"integration-event:{event.id}",
            integration_event_id=event.id,
        )
    return len(events)


async def _uncertain_attempts(session: AsyncSession, *, instant: datetime, limit: int) -> int:
    attempts = list((await session.scalars(select(ActionExecutionAttempt).where(
        ActionExecutionAttempt.status == "dispatching",
        ActionExecutionAttempt.lease_expires_at <= instant,
    ).order_by(ActionExecutionAttempt.lease_expires_at, ActionExecutionAttempt.id).limit(limit)
        .with_for_update(skip_locked=True))).all())
    for attempt in attempts:
        await enqueue_job(
            session,
            business_id=attempt.business_id,
            job_type="reconcile_uncertain_attempt",
            idempotency_key=f"uncertain-attempt:{attempt.id}:{attempt.lease_expires_at.isoformat()}",
            action_execution_attempt_id=attempt.id,
        )
    return len(attempts)


async def _queued_action_attempts(
    session: AsyncSession, *, instant: datetime, limit: int
) -> int:
    _ = instant
    attempts = list((await session.scalars(
        select(ActionExecutionAttempt).where(
            ActionExecutionAttempt.status == "queued",
        ).order_by(
            ActionExecutionAttempt.queued_at,
            ActionExecutionAttempt.id,
        ).limit(limit).with_for_update(skip_locked=True)
    )).all())
    for attempt in attempts:
        await enqueue_job(
            session,
            business_id=attempt.business_id,
            job_type="dispatch_action_execution",
            idempotency_key=f"dispatch-action:{attempt.id}",
            action_execution_attempt_id=attempt.id,
        )
    return len(attempts)


async def _social_schedules(session: AsyncSession, *, instant: datetime, limit: int) -> int:
    schedules = list((await session.scalars(select(SocialSchedule).where(
        SocialSchedule.status == "scheduled",
        SocialSchedule.scheduled_for <= instant,
    ).order_by(SocialSchedule.scheduled_for, SocialSchedule.id).limit(limit)
        .with_for_update(skip_locked=True))).all())
    for schedule in schedules:
        await enqueue_job(
            session,
            business_id=schedule.business_id,
            job_type="mark_social_schedule_ready",
            idempotency_key=f"social-schedule-ready:{schedule.id}:{schedule.scheduled_for.isoformat()}",
            social_schedule_id=schedule.id,
        )
    return len(schedules)


async def _subscriptions(session: AsyncSession, *, instant: datetime, limit: int) -> int:
    maintenance_already_queued = exists(select(BackgroundJob.id).where(
        BackgroundJob.subscription_id == BusinessSubscription.id,
        BackgroundJob.job_type == "maintain_subscription",
        BackgroundJob.idempotency_key.like(
            f"billing-maintenance:%:{instant.date().isoformat()}",
        ),
    ))
    subscriptions = list((await session.scalars(select(BusinessSubscription).where(
        BusinessSubscription.status.in_(("active", "trialing")),
        ~maintenance_already_queued,
    ).order_by(BusinessSubscription.current_period_end, BusinessSubscription.id).limit(limit)
        .with_for_update(skip_locked=True))).all())
    for subscription in subscriptions:
        await enqueue_job(
            session,
            business_id=subscription.business_id,
            job_type="maintain_subscription",
            idempotency_key=f"billing-maintenance:{subscription.id}:{instant.date().isoformat()}",
            subscription_id=subscription.id,
        )
    return len(subscriptions)
