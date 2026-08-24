from __future__ import annotations

import asyncio
import json
import logging
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
from app.models.notification import Notification
from app.services.action_execution_attempt import (
    claim_action_execution_attempt,
    record_action_execution_failure,
    record_action_execution_success,
    record_action_execution_uncertain,
)
from app.services.operations import record_audit


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
