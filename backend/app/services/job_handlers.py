from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Awaitable, Callable, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.openai_provider import create_openai_provider
from app.core.config import settings
from app.domain.background_jobs import initial_opportunity_analysis_request_key
from app.exceptions.automation import (
    AutomationConflictError,
    AutomationNotFoundError,
    AutomationPersistenceError,
    AutomationStateError,
    AutomationValidationError,
)
from app.exceptions.integration import (
    IntegrationCredentialUnavailableError,
    IntegrationNotFoundError,
    IntegrationPersistenceError,
    IntegrationStateError,
    IntegrationValidationError,
    IntegrationProviderUnavailableError,
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
from app.models.integration import IntegrationConnection, IntegrationEntityLink
from app.models.catalog_item import CatalogItem
from app.models.commerce import CommerceFeedDestination, CommerceFeedProductStatus
from app.models.marketing import Campaign, CampaignProductSelection, ExternalCampaignDeployment, ProductCampaignPerformance
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
from app.services.integrations import ingest_ad_performance
from app.integrations.adapters import connector_adapters
from app.integrations.contracts import NormalizedAdPerformance
from app.integrations.credentials import credential_store
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
from app.exceptions.commerce import (
    CommerceConfigurationRequiredError,
    CommerceNotFoundError,
    CommercePersistenceError,
    CommerceProviderError,
    CommerceValidationError,
)
from app.exceptions.ai_agent import AIAgentProviderError
from app.exceptions.ai_workforce import (
    AIWorkforceConflictError,
    AIWorkforceNotFoundError,
    AIWorkforcePersistenceError,
    AIWorkforceValidationError,
)
from app.exceptions.customer_agent import (
    CustomerAgentNotFoundError,
    CustomerAgentPersistenceError,
    CustomerAgentValidationError,
)
from app.services.commerce import process_sync_run_page, reconcile_webhook
from app.services.ad_commerce import synchronize_destination
from app.services.customer_agent import process_customer_agent_response
from app.services.automation_copilot import analyze_business_opportunity
from app.services.billing import BillingEntitlementError


@dataclass(frozen=True, slots=True)
class HandlerOutcome:
    succeeded: bool
    failure_code: str | None = None
    retryable: bool = False
    retry_after_seconds: int | None = None


JobHandler = Callable[[AsyncSession, BackgroundJob], Awaitable[HandlerOutcome]]
SUCCESS: Final = HandlerOutcome(True)


async def handle_customer_agent_response(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.automation_event_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    provider = None
    if settings.openai_api_key_value:
        try:
            provider = create_openai_provider(settings)
        except AIAgentProviderError:
            provider = None
    try:
        result = await process_customer_agent_response(
            session,
            business_id=job.business_id,
            automation_event_id=job.automation_event_id,
            provider=provider,
            final_attempt=job.attempt_count >= job.max_attempts,
        )
    except CustomerAgentNotFoundError:
        return HandlerOutcome(False, "resource_not_found")
    except CustomerAgentValidationError:
        return HandlerOutcome(False, "invalid_job_state")
    except CustomerAgentPersistenceError:
        return HandlerOutcome(False, "dependency_unavailable", True)
    if result.retryable:
        return HandlerOutcome(
            False,
            result.failure_code or "dependency_unavailable",
            True,
        )
    if result.failure_code == "feature_not_entitled":
        return HandlerOutcome(False, "feature_not_entitled")
    return SUCCESS


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


async def handle_analyze_business_opportunity(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    """Delegate one tenant-owned Opportunity to the existing analysis service."""
    if job.opportunity_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    try:
        provider = create_openai_provider(settings)
    except AIAgentProviderError:
        # No execution ledger exists yet, so a bounded queue retry can recover
        # after server-side provider configuration becomes available.
        return HandlerOutcome(False, "provider_unavailable", True)

    try:
        outcome = await analyze_business_opportunity(
            session,
            business_id=job.business_id,
            opportunity_id=job.opportunity_id,
            provider=provider,
            analysis_request_key=initial_opportunity_analysis_request_key(
                job.opportunity_id
            ),
            requested_by_user_id=None,
            trigger_type="automation",
        )
    except AIWorkforceNotFoundError:
        return HandlerOutcome(False, "resource_not_found")
    except BillingEntitlementError:
        return HandlerOutcome(False, "feature_not_entitled")
    except (AIWorkforceConflictError, AIWorkforceValidationError):
        return HandlerOutcome(False, "invalid_job_state")
    except AIWorkforcePersistenceError:
        return HandlerOutcome(False, "dependency_unavailable", True)

    if outcome.execution.status == "running":
        # A previous worker durably started this same request but has not yet
        # recorded its terminal ledger outcome. Requeue with the existing
        # bounded policy rather than claiming analysis completed.
        return HandlerOutcome(False, "dependency_unavailable", True)
    # The service persists both successful and safely failed terminal model
    # executions. Either makes the job complete; replaying a failed execution
    # must not create an infinite provider retry loop.
    return SUCCESS


async def handle_commerce_sync(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.commerce_sync_run_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    try:
        await process_sync_run_page(
            session,
            business_id=job.business_id,
            sync_run_id=job.commerce_sync_run_id,
            execution_id=job.id,
        )
    except CommerceConfigurationRequiredError:
        return HandlerOutcome(False, "invalid_job_state", False)
    except CommerceProviderError as error:
        return HandlerOutcome(
            False, "provider_unavailable", error.retryable,
            error.retry_after_seconds,
        )
    except CommerceValidationError:
        return HandlerOutcome(False, "invalid_job_state", False)
    except CommercePersistenceError:
        return HandlerOutcome(False, "dependency_unavailable", True)
    return SUCCESS


async def handle_commerce_webhook_reconcile(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.commerce_webhook_receipt_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    try:
        await reconcile_webhook(
            session, business_id=job.business_id,
            receipt_id=job.commerce_webhook_receipt_id,
        )
    except CommerceConfigurationRequiredError:
        return HandlerOutcome(False, "invalid_job_state")
    except CommerceNotFoundError:
        return HandlerOutcome(False, "resource_not_found")
    except CommercePersistenceError:
        return HandlerOutcome(False, "dependency_unavailable", True)
    return SUCCESS


async def handle_destination_status_sync(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.commerce_feed_destination_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    destination = await session.scalar(select(CommerceFeedDestination).where(
        CommerceFeedDestination.id == job.commerce_feed_destination_id,
        CommerceFeedDestination.business_id == job.business_id,
    ))
    if destination is None:
        return HandlerOutcome(False, "resource_not_found")
    expected = "google_merchant_center" if job.job_type == "google_merchant_status_sync" else "meta_product_catalog"
    if destination.provider != expected:
        return HandlerOutcome(False, "invalid_job_state")
    connection = await session.scalar(select(IntegrationConnection).where(
        IntegrationConnection.id == destination.integration_connection_id,
        IntegrationConnection.business_id == job.business_id,
    ))
    try:
        await synchronize_destination(
            session, business_id=job.business_id, destination_id=destination.id,
            actor_user_id=connection.connected_by_user_id if connection else None,
            idempotency_key=f"status-sync:{job.id}", reconcile_only=True,
        )
    except CommerceConfigurationRequiredError:
        return HandlerOutcome(False, "invalid_job_state")
    except CommerceProviderError as error:
        return HandlerOutcome(False, "provider_unavailable", error.retryable, error.retry_after_seconds)
    except CommercePersistenceError:
        return HandlerOutcome(False, "dependency_unavailable", True)
    return SUCCESS


async def handle_ads_performance_sync(
    session: AsyncSession, job: BackgroundJob,
) -> HandlerOutcome:
    if job.marketing_campaign_id is None:
        return HandlerOutcome(False, "invalid_job_state")
    connector_type = "google_ads" if job.job_type == "google_ads_performance_sync" else "meta_ads"
    channel = "google_ads" if connector_type == "google_ads" else "meta"
    campaign = await session.scalar(select(Campaign).where(
        Campaign.id == job.marketing_campaign_id,
        Campaign.business_id == job.business_id,
    ))
    if campaign is None:
        return HandlerOutcome(False, "resource_not_found")
    link = await session.scalar(select(IntegrationEntityLink).join(
        IntegrationConnection,
        (IntegrationConnection.id == IntegrationEntityLink.integration_connection_id)
        & (IntegrationConnection.business_id == IntegrationEntityLink.business_id),
    ).where(
        IntegrationEntityLink.business_id == job.business_id,
        IntegrationEntityLink.internal_entity_type == "campaign",
        IntegrationEntityLink.internal_entity_id == campaign.id,
        IntegrationConnection.connector_type == connector_type,
        IntegrationConnection.status == "connected",
        IntegrationConnection.authentication_state == "authorized",
    ))
    if link is None:
        return HandlerOutcome(False, "resource_not_found")
    connection = await session.scalar(select(IntegrationConnection).where(
        IntegrationConnection.id == link.integration_connection_id,
        IntegrationConnection.business_id == job.business_id,
    ))
    if connection is None or not connection.credential_reference:
        return HandlerOutcome(False, "invalid_job_state")
    account_type = "google_ads_customer" if connector_type == "google_ads" else "ad_account"
    account = next((str(item.get("external_reference")) for item in connection.selected_resources if item.get("resource_type") == account_type), None)
    if not account:
        return HandlerOutcome(False, "invalid_job_state")
    try:
        material = await credential_store.retrieve(
            connection.credential_reference, business_id=job.business_id,
            connector_type=connector_type, purpose="oauth_credentials",
        )
        values = await connector_adapters.get(connector_type).read_campaign_performance(
            material, account_reference=account,
            period_start=max(campaign.start_date or date.today() - timedelta(days=30), date.today() - timedelta(days=90)),
            period_end=min(campaign.end_date or date.today(), date.today()),
        )
        provider_status = await connector_adapters.get(connector_type).read_campaign_status(
            material, account_reference=account,
            campaign_reference=link.external_entity_id,
        )
    except (IntegrationCredentialUnavailableError, IntegrationProviderUnavailableError):
        return HandlerOutcome(False, "provider_unavailable", True)
    relevant = [item for item in values if item.external_campaign_reference == link.external_entity_id]
    deployment = await session.scalar(select(ExternalCampaignDeployment).where(
        ExternalCampaignDeployment.business_id == job.business_id,
        ExternalCampaignDeployment.campaign_id == campaign.id,
        ExternalCampaignDeployment.integration_connection_id == connection.id,
    ).with_for_update())
    if deployment is not None:
        deployment.status = provider_status.status
        deployment.provider_status = provider_status.provider_status
        deployment.failure_code = "provider_issue" if provider_status.issues else None
        deployment.last_reconciled_at = datetime.now(UTC)
    campaign.status = provider_status.status
    aggregates: dict[tuple[date, date], NormalizedAdPerformance] = {}
    for item in relevant:
        key = (item.period_start, item.period_end)
        current = aggregates.get(key)
        aggregates[key] = NormalizedAdPerformance(
            external_campaign_reference=item.external_campaign_reference,
            period_start=item.period_start, period_end=item.period_end,
            spend=(current.spend if current else Decimal("0")) + item.spend,
            impressions=(current.impressions if current else 0) + item.impressions,
            clicks=(current.clicks if current else 0) + item.clicks,
            conversions=(current.conversions if current else 0) + item.conversions,
            revenue=(current.revenue if current else Decimal("0")) + item.revenue,
            reach=(current.reach if current else 0) + item.reach,
            leads=(current.leads if current else 0) + item.leads,
        )
        if item.external_product_reference:
            await _upsert_product_performance(
                session, campaign=campaign, provider="google" if connector_type == "google_ads" else "meta",
                normalized=item,
            )
    for normalized in aggregates.values():
        await ingest_ad_performance(
            session, business_id=job.business_id, connection_id=connection.id,
            campaign_id=campaign.id, actor_user_id=connection.connected_by_user_id,
            channel=channel, normalized=normalized,
        )
    connection.last_successful_sync_at = datetime.now(UTC)
    return SUCCESS


async def _upsert_product_performance(session, *, campaign, provider, normalized) -> None:
    selected_ids = select(CampaignProductSelection.catalog_item_id).where(
        CampaignProductSelection.business_id == campaign.business_id,
        CampaignProductSelection.campaign_id == campaign.id,
    )
    candidates = list((await session.scalars(select(CatalogItem).where(
        CatalogItem.business_id == campaign.business_id,
        CatalogItem.id.in_(selected_ids),
    ))).all())
    reference = normalized.external_product_reference
    item = next((candidate for candidate in candidates if (
        candidate.sku == reference or str(candidate.id) == reference
        or (reference and reference.endswith(f"~{candidate.sku or candidate.id}"))
    )), None)
    if item is None:
        status = await session.scalar(select(CommerceFeedProductStatus).where(
            CommerceFeedProductStatus.business_id == campaign.business_id,
            CommerceFeedProductStatus.catalog_item_id.in_(selected_ids),
            CommerceFeedProductStatus.external_product_id == normalized.external_product_reference,
        ))
        if status is not None:
            item = await session.scalar(select(CatalogItem).where(
                CatalogItem.business_id == campaign.business_id,
                CatalogItem.id == status.catalog_item_id,
            ))
    if item is None:
        return
    value = await session.scalar(select(ProductCampaignPerformance).where(
        ProductCampaignPerformance.business_id == campaign.business_id,
        ProductCampaignPerformance.campaign_id == campaign.id,
        ProductCampaignPerformance.catalog_item_id == item.id,
        ProductCampaignPerformance.provider == provider,
        ProductCampaignPerformance.period_start == normalized.period_start,
        ProductCampaignPerformance.period_end == normalized.period_end,
        ProductCampaignPerformance.attribution_class == "provider_attributed",
    ).with_for_update())
    fields = {
        "external_campaign_reference": normalized.external_campaign_reference,
        "external_product_reference": normalized.external_product_reference,
        "spend": normalized.spend, "impressions": normalized.impressions,
        "clicks": normalized.clicks, "conversions": Decimal(normalized.conversions),
        "conversion_value": normalized.revenue,
    }
    if value is None:
        session.add(ProductCampaignPerformance(
            business_id=campaign.business_id, campaign_id=campaign.id,
            catalog_item_id=item.id, provider=provider,
            period_start=normalized.period_start, period_end=normalized.period_end,
            attribution_class="provider_attributed", **fields,
        ))
    else:
        for key, item_value in fields.items():
            setattr(value, key, item_value)


JOB_HANDLERS: Final = MappingProxyType({
    "process_automation_event": handle_process_automation_event,
    "resume_workflow_run": handle_resume_workflow_run,
    "process_scheduled_workflow": handle_process_scheduled_workflow,
    "process_integration_event": handle_process_integration_event,
    "customer_agent_response": handle_customer_agent_response,
    "dispatch_action_execution": handle_dispatch_action_execution,
    "reconcile_uncertain_attempt": handle_reconcile_uncertain_attempt,
    "mark_social_schedule_ready": handle_mark_social_schedule_ready,
    "maintain_subscription": handle_maintain_subscription,
    "discover_competitors": handle_discover_competitors,
    "generate_content_plan": handle_generate_content_plan,
    "analyze_campaign_opportunities": handle_analyze_campaign_opportunities,
    "analyze_business_opportunity": handle_analyze_business_opportunity,
    "commerce_initial_sync": handle_commerce_sync,
    "commerce_incremental_sync": handle_commerce_sync,
    "commerce_webhook_reconcile": handle_commerce_webhook_reconcile,
    "google_merchant_status_sync": handle_destination_status_sync,
    "meta_catalog_status_sync": handle_destination_status_sync,
    "google_ads_performance_sync": handle_ads_performance_sync,
    "meta_ads_performance_sync": handle_ads_performance_sync,
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
