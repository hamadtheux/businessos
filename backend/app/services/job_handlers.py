from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Awaitable, Callable, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.openai_provider import create_openai_provider
from app.core.config import settings
from app.exceptions.automation import (
    AutomationConflictError,
    AutomationNotFoundError,
    AutomationPersistenceError,
    AutomationStateError,
    AutomationValidationError,
)
from app.exceptions.integration import (
    IntegrationNotFoundError,
    IntegrationPersistenceError,
    IntegrationStateError,
    IntegrationValidationError,
)
from app.exceptions.marketing import (
    MarketingNotFoundError,
    MarketingPersistenceError,
    MarketingStateError,
)
from app.models.action_execution_attempt import ActionExecutionAttempt
from app.models.automation import AutomationWorkflow
from app.models.background_job import BackgroundJob
from app.models.integration import IntegrationWebhookEvent
from app.models.notification import Notification
from app.services.action_execution_attempt import (
    mark_stale_action_execution_attempt_uncertain,
)
from app.services.automation import (
    advance_workflow_run,
    create_workflow_run,
    process_automation_event,
)
from app.services.background_jobs import enqueue_job
from app.services.integrations import process_integration_webhook_event
from app.services.marketing import mark_social_schedule_ready
from app.services.billing_maintenance import maintain_subscription
from app.services.automation_intelligence import run_competitor_discovery
from app.services.marketing_automation import (
    analyze_bounded_campaign_opportunities,
    generate_bounded_content_plan,
)
from app.exceptions.automation_intelligence import (
    AutomationIntelligenceNotFoundError,
    AutomationIntelligencePersistenceError,
    AutomationIntelligenceProviderError,
)


@dataclass(frozen=True, slots=True)
class HandlerOutcome:
    succeeded: bool
    failure_code: str | None = None
    retryable: bool = False


JobHandler = Callable[[AsyncSession, BackgroundJob], Awaitable[HandlerOutcome]]
SUCCESS: Final = HandlerOutcome(True)


async def handle_dispatch_action_execution(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    # The worker intercepts this type so the provider boundary can close all
    # DB transactions before invocation. Calling it as an ordinary handler is
    # intentionally refused.
    _ = session, job
    return HandlerOutcome(False, "invalid_job_state")


async def handle_process_automation_event(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.automation_event_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    _event, runs = await process_automation_event(
        session, business_id=job.business_id, event_id=job.automation_event_id,
    )
    for run in runs:
        if run.status in {"queued", "running"}:
            await enqueue_job(
                session,
                business_id=job.business_id,
                job_type="resume_workflow_run",
                idempotency_key=f"workflow-start:{run.id}",
                workflow_run_id=run.id,
            )
    return SUCCESS


async def handle_resume_workflow_run(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.workflow_run_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    provider = None
    if settings.openai_api_key_value:
        provider = create_openai_provider(settings)
    run = await advance_workflow_run(
        session,
        business_id=job.business_id,
        run_id=job.workflow_run_id,
        actor_user_id=None,
        provider=provider,
    )
    if run.status == "failed":
        # The workflow engine persisted its own terminal failure and
        # notification. The queue job itself completed deterministically.
        return SUCCESS
    return SUCCESS


async def handle_process_scheduled_workflow(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.workflow_id is None or job.scheduled_occurrence_at is None:
        return HandlerOutcome(False, "invalid_job_state")
    workflow = await session.scalar(select(AutomationWorkflow).where(
        AutomationWorkflow.id == job.workflow_id,
        AutomationWorkflow.business_id == job.business_id,
    ))
    if workflow is None:
        return HandlerOutcome(False, "resource_not_found")
    if workflow.status != "active" or not workflow.enabled or workflow.trigger_type != "scheduled_time":
        return HandlerOutcome(False, "invalid_job_state")
    run = await create_workflow_run(
        session,
        business_id=job.business_id,
        workflow_id=workflow.id,
        trigger_type="schedule",
        context_payload={
            "schedule": {"occurred_at": job.scheduled_occurrence_at.isoformat()},
        },
        requested_by_user_id=None,
        idempotency_key=f"schedule:{workflow.id}:{job.scheduled_occurrence_at.isoformat()}",
    )
    if run.status in {"queued", "running"}:
        await enqueue_job(
            session,
            business_id=job.business_id,
            job_type="resume_workflow_run",
            idempotency_key=f"workflow-start:{run.id}",
            workflow_run_id=run.id,
        )
    return SUCCESS


async def handle_process_integration_event(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.integration_event_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    try:
        await process_integration_webhook_event(
            session,
            business_id=job.business_id,
            event_id=job.integration_event_id,
        )
    except (IntegrationStateError, IntegrationValidationError):
        event = await session.scalar(select(IntegrationWebhookEvent).where(
            IntegrationWebhookEvent.id == job.integration_event_id,
            IntegrationWebhookEvent.business_id == job.business_id,
        ).with_for_update())
        if event is not None and event.status != "processed":
            event.status = "failed"
            event.failure_code = "invalid_job_state"
        return HandlerOutcome(False, "invalid_job_state")
    return SUCCESS


async def handle_reconcile_uncertain_attempt(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.action_execution_attempt_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    attempt = await session.scalar(select(ActionExecutionAttempt).where(
        ActionExecutionAttempt.id == job.action_execution_attempt_id,
        ActionExecutionAttempt.business_id == job.business_id,
    ))
    if attempt is None:
        return HandlerOutcome(False, "resource_not_found")
    was_dispatching = attempt.status == "dispatching"
    await mark_stale_action_execution_attempt_uncertain(
        session,
        business_id=job.business_id,
        attempt_id=attempt.id,
    )
    if was_dispatching:
        session.add(Notification(
            business_id=job.business_id,
            recipient_user_id=None,
            category="action_uncertain",
            title="External action outcome is uncertain",
            message="A dispatch lease expired. The action was not retried and requires reconciliation.",
            priority="high",
            read=False,
            related_entity_type="action_execution_attempt",
            related_entity_id=attempt.id,
        ))
    return SUCCESS


async def handle_mark_social_schedule_ready(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.social_schedule_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    await mark_social_schedule_ready(
        session,
        business_id=job.business_id,
        schedule_id=job.social_schedule_id,
    )
    return SUCCESS


async def handle_maintain_subscription(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.subscription_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    subscription = await maintain_subscription(
        session, subscription_id=job.subscription_id, business_id=job.business_id,
    )
    if subscription is None:
        return HandlerOutcome(False, "resource_not_found")
    return SUCCESS


async def handle_discover_competitors(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.competitor_discovery_run_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    await run_competitor_discovery(
        session,
        business_id=job.business_id,
        run_id=job.competitor_discovery_run_id,
    )
    return SUCCESS


async def handle_generate_content_plan(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.marketing_automation_run_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    provider = create_openai_provider(settings) if settings.openai_api_key_value else None
    await generate_bounded_content_plan(
        session,
        business_id=job.business_id,
        run_id=job.marketing_automation_run_id,
        provider=provider,
    )
    return SUCCESS


async def handle_analyze_campaign_opportunities(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.marketing_automation_run_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    await analyze_bounded_campaign_opportunities(
        session,
        business_id=job.business_id,
        run_id=job.marketing_automation_run_id,
    )
    return SUCCESS


JOB_HANDLERS: Final = MappingProxyType({
    "process_automation_event": handle_process_automation_event,
    "resume_workflow_run": handle_resume_workflow_run,
    "process_scheduled_workflow": handle_process_scheduled_workflow,
    "process_integration_event": handle_process_integration_event,
    "dispatch_action_execution": handle_dispatch_action_execution,
    "reconcile_uncertain_attempt": handle_reconcile_uncertain_attempt,
    "mark_social_schedule_ready": handle_mark_social_schedule_ready,
    "maintain_subscription": handle_maintain_subscription,
    "discover_competitors": handle_discover_competitors,
    "generate_content_plan": handle_generate_content_plan,
    "analyze_campaign_opportunities": handle_analyze_campaign_opportunities,
})


async def dispatch_job_handler(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    handler = JOB_HANDLERS.get(job.job_type)
    if handler is None:
        return HandlerOutcome(False, "invalid_job_state")
    try:
        return await handler(session, job)
    except (AutomationNotFoundError, IntegrationNotFoundError, MarketingNotFoundError):
        return HandlerOutcome(False, "resource_not_found")
    except (AutomationStateError, AutomationValidationError, AutomationConflictError, MarketingStateError):
        return HandlerOutcome(False, "workflow_invalid")
    except (AutomationPersistenceError, IntegrationPersistenceError, MarketingPersistenceError):
        return HandlerOutcome(False, "dependency_unavailable", True)
    except AutomationIntelligenceNotFoundError:
        return HandlerOutcome(False, "resource_not_found")
    except AutomationIntelligenceProviderError:
        return HandlerOutcome(False, "provider_unavailable", True)
    except AutomationIntelligencePersistenceError:
        return HandlerOutcome(False, "dependency_unavailable", True)
