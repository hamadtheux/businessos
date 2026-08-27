from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from hashlib import sha256
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import select

from app.core.config import Settings, settings
from app.db.session import AsyncSessionFactory
from app.domain.integrations import ExternalConnectorWritesDisabledError
from app.exceptions.action_execution_attempt import ActionExecutionAttemptError
from app.exceptions.integration import (
    IntegrationCredentialUnavailableError,
    IntegrationError,
)
from app.integrations.action_adapters import (
    ConnectorActionAdapterRegistry,
    ConnectorRejectedError,
    ConnectorRequestNotSentError,
    connector_action_adapters,
)
from app.integrations.action_boundary import (
    ConnectorDispatchContext,
    prepare_connector_dispatch_context,
)
from app.integrations.credentials import (
    IntegrationCredentialStore,
    credential_store,
)
from app.models.action_execution_attempt import ActionExecutionAttempt
from app.models.background_job import BackgroundJob
from app.models.conversation import Conversation, ConversationMessage, CustomerAgentResponse
from app.models.notification import Notification
from app.models.automation_intelligence import MarketingActionProposal
from app.models.integration import IntegrationEntityLink
from app.models.marketing import Campaign, ExternalCampaignDeployment
from app.services.action_execution_attempt import (
    claim_action_execution_attempt,
    record_action_execution_failure,
    record_action_execution_success,
    record_action_execution_uncertain,
)
from app.services.operations import record_audit
from app.services.automation_events import record_automation_event


logger = logging.getLogger("aibos.action_dispatcher")


@dataclass(frozen=True, slots=True)
class DispatchJobOutcome:
    succeeded: bool
    failure_code: str | None = None
    retryable: bool = False


class DispatchMeasurementHook(Protocol):
    async def record(
        self,
        *,
        business_id: UUID,
        action_type: str,
        connector_type: str | None,
        outcome: str,
    ) -> None: ...


class NullDispatchMeasurementHook:
    async def record(self, **_: object) -> None:
        return None


async def dispatch_action_execution_job(
    job: BackgroundJob,
    *,
    adapters: ConnectorActionAdapterRegistry = connector_action_adapters,
    credentials: IntegrationCredentialStore = credential_store,
    configuration: Settings = settings,
    measurement: DispatchMeasurementHook | None = None,
) -> DispatchJobOutcome:
    """Run the durable claim/read/invoke/record protocol.

    Every database transaction is committed and closed before the provider
    adapter is entered. Unknown post-invocation failures are classified as
    uncertain and are never automatically retried.
    """
    attempt_id = job.action_execution_attempt_id
    if attempt_id is None:
        return DispatchJobOutcome(False, "invalid_job_state")
    hook = measurement or NullDispatchMeasurementHook()

    try:
        async with AsyncSessionFactory() as session:
            attempt = await session.scalar(
                select(ActionExecutionAttempt).where(
                    ActionExecutionAttempt.id == attempt_id,
                    ActionExecutionAttempt.business_id == job.business_id,
                )
            )
            if attempt is None:
                return DispatchJobOutcome(False, "resource_not_found")
            if attempt.status in {"succeeded", "failed", "uncertain", "canceled"}:
                return DispatchJobOutcome(True)
            if attempt.status == "dispatching":
                # A prior worker committed the claim. Re-entry cannot know
                # whether that worker reached the provider, so it must stop.
                await record_action_execution_uncertain(
                    session,
                    business_id=job.business_id,
                    attempt_id=attempt_id,
                )
                _add_outcome_records(
                    session,
                    business_id=job.business_id,
                    attempt_id=attempt_id,
                    outcome="uncertain",
                    connector_type=None,
                    action_type=attempt.action_type,
                )
                await session.commit()
                await _measure_safely(
                    hook,
                    business_id=job.business_id,
                    action_type=attempt.action_type,
                    connector_type=None,
                    outcome="uncertain",
                )
                return DispatchJobOutcome(True)
            await claim_action_execution_attempt(
                session,
                business_id=job.business_id,
                attempt_id=attempt_id,
                lease_seconds=configuration.job_lease_seconds,
            )
            await session.commit()
    except ActionExecutionAttemptError:
        return DispatchJobOutcome(False, "invalid_job_state")
    except Exception:
        logger.exception(
            json.dumps(
                {
                    "event": "action_dispatch_claim_failed",
                    "business_id": str(job.business_id),
                    "attempt_id": str(attempt_id),
                }
            )
        )
        return DispatchJobOutcome(False, "dependency_unavailable")

    context: ConnectorDispatchContext | None = None
    try:
        async with AsyncSessionFactory() as session:
            context = await prepare_connector_dispatch_context(
                session,
                business_id=job.business_id,
                attempt_id=attempt_id,
                adapters=adapters,
                configuration=configuration,
            )
            # Close the read/revalidation transaction before secrets are read
            # or the provider is called.
            await session.commit()
    except (ExternalConnectorWritesDisabledError, IntegrationError):
        await _record_definite_failure(
            job.business_id,
            attempt_id,
            action_type=getattr(attempt, "action_type", "unknown"),
            connector_type=None,
            hook=hook,
            code="request_not_sent",
        )
        return DispatchJobOutcome(True)
    except ActionExecutionAttemptError:
        await _record_definite_failure(
            job.business_id,
            attempt_id,
            action_type=getattr(attempt, "action_type", "unknown"),
            connector_type=None,
            hook=hook,
            code="connector_validation_failed",
        )
        return DispatchJobOutcome(True)
    except Exception:
        logger.exception(
            json.dumps(
                {
                    "event": "action_dispatch_preflight_failed",
                    "business_id": str(job.business_id),
                    "attempt_id": str(attempt_id),
                }
            )
        )
        await _record_definite_failure(
            job.business_id,
            attempt_id,
            action_type=getattr(attempt, "action_type", "unknown"),
            connector_type=None,
            hook=hook,
            code="request_not_sent",
        )
        return DispatchJobOutcome(True)

    adapter = adapters.get(context.connector_type, context.action_type)
    if adapter is None:
        await _record_definite_failure(
            context.business_id,
            context.attempt_id,
            action_type=context.action_type,
            connector_type=context.connector_type,
            hook=hook,
            code="request_not_sent",
        )
        return DispatchJobOutcome(True)
    try:
        material = await credentials.retrieve(
            context.credential_reference,
            business_id=context.business_id,
            connector_type=context.connector_type,
            purpose="oauth_credentials",
        )
    except IntegrationCredentialUnavailableError:
        await _record_definite_failure(
            context.business_id,
            context.attempt_id,
            action_type=context.action_type,
            connector_type=context.connector_type,
            hook=hook,
            code="request_not_sent",
        )
        return DispatchJobOutcome(True)

    try:
        async with asyncio.timeout(configuration.connector_dispatch_timeout_seconds):
            result = await adapter.execute(
                credentials=material,
                action_type=context.action_type,
                payload=context.payload,
                selected_resources=context.selected_resources,
                delivery_target=context.delivery_target,
                idempotency_key=context.idempotency_key,
            )
    except ConnectorRequestNotSentError:
        await _record_definite_failure(
            context.business_id,
            context.attempt_id,
            action_type=context.action_type,
            connector_type=context.connector_type,
            hook=hook,
            code="request_not_sent",
        )
        return DispatchJobOutcome(True)
    except ConnectorRejectedError:
        await _record_definite_failure(
            context.business_id,
            context.attempt_id,
            action_type=context.action_type,
            connector_type=context.connector_type,
            hook=hook,
            code="connector_rejected",
        )
        return DispatchJobOutcome(True)
    except Exception:
        await _record_uncertain(context, hook=hook)
        return DispatchJobOutcome(True)

    if not result.succeeded:
        await _record_definite_failure(
            context.business_id,
            context.attempt_id,
            action_type=context.action_type,
            connector_type=context.connector_type,
            hook=hook,
            code="connector_rejected",
        )
        return DispatchJobOutcome(True)

    try:
        async with AsyncSessionFactory() as session:
            await record_action_execution_success(
                session,
                business_id=context.business_id,
                attempt_id=context.attempt_id,
                external_reference_id=result.external_reference_id,
            )
            await _persist_campaign_provider_result(
                session, context=context, result=result,
            )
            await _persist_customer_message_result(
                session, context=context, result=result,
            )
            _add_outcome_records(
                session,
                business_id=context.business_id,
                attempt_id=context.attempt_id,
                outcome="succeeded",
                connector_type=context.connector_type,
                action_type=context.action_type,
            )
            await session.commit()
    except Exception:
        # A successful provider response followed by a persistence failure is
        # externally ambiguous. Never re-invoke the provider.
        await _record_uncertain(context, hook=hook)
        return DispatchJobOutcome(True)
    await _measure_safely(
        hook,
        business_id=context.business_id,
        action_type=context.action_type,
        connector_type=context.connector_type,
        outcome="succeeded",
    )
    _log_outcome(context, "succeeded")
    return DispatchJobOutcome(True)


async def _record_definite_failure(
    business_id: UUID,
    attempt_id: UUID,
    *,
    action_type: str,
    connector_type: str | None,
    hook: DispatchMeasurementHook,
    code: str,
) -> None:
    async with AsyncSessionFactory() as session:
        await record_action_execution_failure(
            session,
            business_id=business_id,
            attempt_id=attempt_id,
            failure_code=code,
        )
        if action_type in {"create_google_ads_campaign", "create_meta_campaign"}:
            attempt = await session.scalar(select(ActionExecutionAttempt).where(
                ActionExecutionAttempt.business_id == business_id,
                ActionExecutionAttempt.id == attempt_id,
            ))
            if attempt is not None:
                proposal = await session.scalar(select(MarketingActionProposal).where(
                    MarketingActionProposal.business_id == business_id,
                    MarketingActionProposal.ai_action_id == attempt.action_id,
                    MarketingActionProposal.entity_type == "campaign",
                ))
                if proposal is not None:
                    campaign = await session.scalar(select(Campaign).where(
                        Campaign.business_id == business_id,
                        Campaign.id == proposal.entity_id,
                    ).with_for_update())
                    if campaign is not None:
                        campaign.status = "failed"
        _add_outcome_records(
            session,
            business_id=business_id,
            attempt_id=attempt_id,
            outcome="failed",
            connector_type=connector_type,
            action_type=action_type,
        )
        await session.commit()
    await _measure_safely(
        hook,
        business_id=business_id,
        action_type=action_type,
        connector_type=connector_type,
        outcome="failed",
    )
    logger.info(
        json.dumps(
            {
                "event": "action_dispatch_completed",
                "business_id": str(business_id),
                "attempt_id": str(attempt_id),
                "action_type": action_type,
                "connector_type": connector_type,
                "outcome": "failed",
                "failure_code": code,
            }
        )
    )


async def _record_uncertain(
    context: ConnectorDispatchContext,
    *,
    hook: DispatchMeasurementHook,
) -> None:
    async with AsyncSessionFactory() as session:
        await record_action_execution_uncertain(
            session,
            business_id=context.business_id,
            attempt_id=context.attempt_id,
        )
        await _mark_campaign_provider_state(
            session, context=context, status="unknown_external_state",
            failure_code="external_state_uncertain",
        )
        _add_outcome_records(
            session,
            business_id=context.business_id,
            attempt_id=context.attempt_id,
            outcome="uncertain",
            connector_type=context.connector_type,
            action_type=context.action_type,
        )
        await session.commit()
    await _measure_safely(
        hook,
        business_id=context.business_id,
        action_type=context.action_type,
        connector_type=context.connector_type,
        outcome="uncertain",
    )
    _log_outcome(context, "uncertain")


async def _persist_campaign_provider_result(session, *, context, result) -> None:
    if context.action_type not in {"create_google_ads_campaign", "create_meta_campaign"}:
        return
    proposal = await session.scalar(select(MarketingActionProposal).where(
        MarketingActionProposal.business_id == context.business_id,
        MarketingActionProposal.ai_action_id == context.action_id,
        MarketingActionProposal.entity_type == "campaign",
    ))
    if proposal is None:
        raise RuntimeError("campaign_proposal_link_missing")
    campaign = await session.scalar(select(Campaign).where(
        Campaign.business_id == context.business_id,
        Campaign.id == proposal.entity_id,
    ).with_for_update())
    if campaign is None:
        raise RuntimeError("campaign_missing")
    provider = "google" if context.connector_type == "google_ads" else "meta"
    fingerprint = sha256(context.payload.model_dump_json().encode("utf-8")).hexdigest()
    deployment = await session.scalar(select(ExternalCampaignDeployment).where(
        ExternalCampaignDeployment.business_id == context.business_id,
        ExternalCampaignDeployment.campaign_id == campaign.id,
        ExternalCampaignDeployment.provider == provider,
    ).with_for_update())
    if deployment is None:
        deployment = ExternalCampaignDeployment(
            business_id=context.business_id, campaign_id=campaign.id,
            integration_connection_id=context.connection_id, provider=provider,
            safe_payload_fingerprint=fingerprint,
        )
        session.add(deployment)
    elif deployment.safe_payload_fingerprint != fingerprint:
        raise RuntimeError("campaign_payload_conflict")
    deployment.external_campaign_reference = result.external_reference_id
    deployment.child_references = {
        key: value for key, value in result.safe_metadata.items()
        if key.endswith("_reference") and isinstance(value, str) and len(value) <= 255
    }
    deployment.status = "provider_pending"
    deployment.provider_status = str(result.safe_metadata.get("status") or "provider_pending")[:64]
    deployment.failure_code = None
    campaign.status = "provider_pending"
    existing_link = await session.scalar(select(IntegrationEntityLink).where(
        IntegrationEntityLink.business_id == context.business_id,
        IntegrationEntityLink.integration_connection_id == context.connection_id,
        IntegrationEntityLink.internal_entity_type == "campaign",
        IntegrationEntityLink.internal_entity_id == campaign.id,
    ))
    if existing_link is None:
        session.add(IntegrationEntityLink(
            business_id=context.business_id,
            integration_connection_id=context.connection_id,
            internal_entity_type="campaign", internal_entity_id=campaign.id,
            external_resource_reference="campaign",
            external_entity_id=result.external_reference_id,
            sync_state="linked",
        ))


async def _persist_customer_message_result(session, *, context, result) -> None:
    if context.action_type not in {
        "send_email", "send_whatsapp_message", "send_customer_message",
    }:
        return
    raw_conversation = getattr(context.payload, "conversation_ref", None)
    if raw_conversation is None:
        return
    try:
        conversation_id = UUID(raw_conversation)
    except (TypeError, ValueError):
        raise RuntimeError("conversation_reference_invalid") from None
    conversation = await session.scalar(select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.business_id == context.business_id,
        Conversation.integration_connection_id == context.connection_id,
    ).with_for_update())
    if conversation is None:
        raise RuntimeError("conversation_missing")
    raw_customer = getattr(context.payload, "recipient_ref", None) or getattr(
        context.payload, "customer_ref", None
    )
    if conversation.customer_id is None or raw_customer != str(conversation.customer_id):
        raise RuntimeError("conversation_customer_conflict")
    content = getattr(context.payload, "body", None) or getattr(
        context.payload, "message", None
    )
    reference = result.external_reference_id
    if not isinstance(content, str) or not content.strip() or len(content) > 10_000:
        raise RuntimeError("outbound_message_invalid")
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 255:
        raise RuntimeError("outbound_reference_invalid")
    existing = await session.scalar(select(ConversationMessage).where(
        ConversationMessage.business_id == context.business_id,
        ConversationMessage.action_execution_attempt_id == context.attempt_id,
    ).with_for_update())
    if existing is not None:
        if (
            existing.business_id != context.business_id
            or existing.action_execution_attempt_id != context.attempt_id
            or existing.conversation_id != conversation.id
            or existing.external_reference != reference
            or existing.content != content.strip()
        ):
            raise RuntimeError("outbound_message_conflict")
        return
    instant = datetime.now(UTC)
    message = ConversationMessage(
        business_id=context.business_id,
        conversation_id=conversation.id,
        action_execution_attempt_id=context.attempt_id,
        direction="outbound",
        sender_type="ai",
        sender_user_id=None,
        content=content.strip(),
        sent_at=instant,
        external_reference=reference.strip(),
        # A successful send API response proves provider acceptance, not
        # device delivery or that the recipient read the message.
        delivery_status="submitted",
    )
    session.add(message)
    conversation.last_activity_at = instant
    response = await session.scalar(select(CustomerAgentResponse).where(
        CustomerAgentResponse.business_id == context.business_id,
        CustomerAgentResponse.ai_action_id == context.action_id,
    ).with_for_update())
    if response is not None:
        response.status = "reply_submitted"
        response.failure_code = None
    await session.flush()
    record_automation_event(
        session,
        business_id=context.business_id,
        event_type="outbound_message_recorded",
        entity_type="conversation_message",
        entity_id=message.id,
        payload={"channel": conversation.channel, "delivery_status": "submitted"},
    )
    record_audit(
        session,
        business_id=context.business_id,
        actor_user_id=None,
        event_type="customer_agent.outbound_message_recorded",
        entity_type="conversation_message",
        entity_id=message.id,
        summary="Recorded a provider-accepted Customer Agent reply as submitted.",
    )


async def _mark_campaign_provider_state(session, *, context, status, failure_code) -> None:
    if context.action_type not in {"create_google_ads_campaign", "create_meta_campaign"}:
        return
    proposal = await session.scalar(select(MarketingActionProposal).where(
        MarketingActionProposal.business_id == context.business_id,
        MarketingActionProposal.ai_action_id == context.action_id,
        MarketingActionProposal.entity_type == "campaign",
    ))
    if proposal is None:
        return
    campaign = await session.scalar(select(Campaign).where(
        Campaign.business_id == context.business_id,
        Campaign.id == proposal.entity_id,
    ).with_for_update())
    if campaign is not None:
        campaign.status = status
    deployment = await session.scalar(select(ExternalCampaignDeployment).where(
        ExternalCampaignDeployment.business_id == context.business_id,
        ExternalCampaignDeployment.campaign_id == proposal.entity_id,
        ExternalCampaignDeployment.provider == ("google" if context.connector_type == "google_ads" else "meta"),
    ).with_for_update())
    if deployment is not None:
        deployment.status = status
        deployment.failure_code = failure_code


def _add_outcome_records(
    session,
    *,
    business_id: UUID,
    attempt_id: UUID,
    outcome: str,
    connector_type: str | None,
    action_type: str,
) -> None:
    title = {
        "succeeded": "External action completed",
        "failed": "External action was not completed",
        "uncertain": "External action needs reconciliation",
    }[outcome]
    message = {
        "succeeded": "The governed connector action completed and its external reference was recorded.",
        "failed": "The governed connector action failed closed. Review the connection and action before trying again.",
        "uncertain": "The provider outcome could not be proven. AI Business OS will not retry this action automatically.",
    }[outcome]
    session.add(
        Notification(
            business_id=business_id,
            recipient_user_id=None,
            category="external_action",
            title=title,
            message=message,
            priority="high" if outcome == "uncertain" else "medium",
            read=False,
            related_entity_type="action_execution_attempt",
            related_entity_id=attempt_id,
        )
    )
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=None,
        event_type=f"action_execution.{outcome}",
        entity_type="action_execution_attempt",
        entity_id=attempt_id,
        summary=(
            f"Governed {action_type} dispatch {outcome}"
            + (f" through {connector_type}." if connector_type else ".")
        ),
    )


async def _measure_safely(hook: DispatchMeasurementHook, **values: object) -> None:
    try:
        await hook.record(**values)
    except Exception:
        logger.warning(
            json.dumps({"event": "action_dispatch_measurement_hook_failed"})
        )


def _log_outcome(context: ConnectorDispatchContext, outcome: str) -> None:
    logger.info(
        json.dumps(
            {
                "event": "action_dispatch_completed",
                "business_id": str(context.business_id),
                "attempt_id": str(context.attempt_id),
                "action_type": context.action_type,
                "connector_type": context.connector_type,
                "outcome": outcome,
            }
        )
    )
