from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.action_execution import (
    ACTION_EXECUTION_DEFINITE_FAILURE_CODES,
    ACTION_EXECUTION_UNCERTAIN_FAILURE_CODES,
    ACTIVE_ACTION_EXECUTION_ATTEMPT_STATUSES,
)
from app.exceptions.action_execution_attempt import (
    ActionExecutionAttemptConflictError,
    ActionExecutionAttemptNotFoundError,
    ActionExecutionAttemptPersistenceError,
    ActionExecutionAttemptStateError,
    ActionExecutionAttemptValidationError,
    ActionExecutionOutcomeUncertainError,
)
from app.models.action_execution_attempt import ActionExecutionAttempt
from app.models.ai_action import AIAction
from app.models.ai_agent_execution import AIAgentExecution
from app.models.ai_workforce import AIAgentConfig
from app.models.approval_request import ApprovalRequest
from app.models.business import Business
from app.schemas.ai_action_payload import ActionPayload
from app.services.action_policy import (
    canonical_action_payload_hash,
    evaluate_action_policy,
)
from app.services.advertising_spend_policy import (
    require_advertising_spend_authorized,
)
from app.services.action_registry import ACTION_REGISTRY, ActionDefinition
from app.services.ai_capabilities import (
    ACTION_CAPABILITY,
    ROLE_CAPABILITIES,
    validate_role_capabilities,
)
from app.services.operations import record_audit


DEFAULT_ATTEMPT_PAGE_SIZE: Final = 50
MAX_ATTEMPT_PAGE_SIZE: Final = 200
MIN_LEASE_SECONDS: Final = 5
MAX_LEASE_SECONDS: Final = 900
MAX_ATTEMPT_NUMBER: Final = 1_000_000

_PERSISTENCE_MESSAGE: Final = "Unable to persist action execution attempt"
_FAILURE_CODE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EXTERNAL_REFERENCE_PATTERN: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/+=-]{0,254}$"
)


async def prepare_action_execution_attempt(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_id: UUID,
) -> ActionExecutionAttempt:
    """
    Persist dispatch intent without performing any external side effect.

    The caller MUST commit the returned queued attempt before it can be
    claimed. A future connector invocation must occur only after a separate
    claim transaction has also committed.

    This function flushes only. It never commits and never invokes a handler.
    """
    action = await _get_action_for_update(
        session,
        business_id=business_id,
        action_id=action_id,
    )
    if action.status == "uncertain":
        raise ActionExecutionOutcomeUncertainError(
            "Uncertain action outcome prohibits automatic retry"
        )
    if action.status != "ready":
        raise ActionExecutionAttemptStateError(
            "AI action is not ready for durable execution"
        )

    definition, _payload = await _revalidate_action_authorization(
        session,
        action=action,
        business_id=business_id,
    )
    # A registry action is not executable merely because it validates. Only
    # action types with an explicit production connector boundary may create a
    # durable external-write attempt.
    from app.integrations.action_boundary import CONNECTOR_ACTION_TYPES

    if definition.action_type not in CONNECTOR_ACTION_TYPES:
        raise ActionExecutionAttemptValidationError(
            "AI action execution is not supported"
        )
    await _require_no_active_attempt(
        session,
        business_id=business_id,
        action_id=action_id,
    )
    attempt_number = await _next_attempt_number(
        session,
        business_id=business_id,
        action_id=action_id,
    )
    idempotency_key = build_action_attempt_idempotency_key(
        action_id=action_id,
        attempt_number=attempt_number,
    )
    queued_at = datetime.now(UTC)

    attempt = ActionExecutionAttempt(
        business_id=business_id,
        action_id=action_id,
        attempt_number=attempt_number,
        idempotency_key=idempotency_key,
        action_type=definition.action_type,
        capability=definition.capability,
        status="queued",
        queued_at=queued_at,
        dispatch_started_at=None,
        completed_at=None,
        lease_acquired_at=None,
        lease_expires_at=None,
        external_reference_id=None,
        failure_code=None,
    )
    session.add(attempt)

    action.status = "queued"
    action.execution_started_at = None
    action.execution_completed_at = None
    action.result_summary = None
    action.failure_code = None
    action.external_reference_id = None

    await _flush_attempt(
        session,
        attempt=attempt,
        refresh_created=True,
    )
    if isinstance(session, AsyncSession):
        record_audit(
            session,
            business_id=business_id,
            actor_user_id=None,
            event_type="action_execution.prepared",
            entity_type="action_execution_attempt",
            entity_id=attempt.id,
            summary=(
                f"Prepared governed {attempt.action_type} dispatch intent; "
                "no connector was invoked."
            ),
        )
        # Durable intent and its worker job commit atomically. The job cannot
        # be claimed before the caller commits this transaction.
        from app.services.background_jobs import enqueue_job

        await enqueue_job(
            session,
            business_id=business_id,
            job_type="dispatch_action_execution",
            idempotency_key=f"dispatch-action:{attempt.id}",
            action_execution_attempt_id=attempt.id,
        )
    return attempt


async def revalidate_action_execution_attempt_for_dispatch(
    session: AsyncSession,
    *,
    business_id: UUID,
    attempt_id: UUID,
) -> tuple[ActionExecutionAttempt, AIAction, ActionDefinition, ActionPayload]:
    """Revalidate immutable policy, approval, payload, currency and spend."""
    attempt, action = await _lock_attempt_and_action(
        session,
        business_id=business_id,
        attempt_id=attempt_id,
    )
    _validate_attempt_identity(attempt, action)
    _require_dispatching_state(attempt, action)
    definition, payload = await _revalidate_action_authorization(
        session, action=action, business_id=business_id
    )
    return attempt, action, definition, payload


async def get_action_execution_attempt(
    session: AsyncSession,
    *,
    business_id: UUID,
    attempt_id: UUID,
) -> ActionExecutionAttempt:
    statement = select(ActionExecutionAttempt).where(
        ActionExecutionAttempt.id == attempt_id,
        ActionExecutionAttempt.business_id == business_id,
    )
    try:
        attempt = await session.scalar(statement)
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    if attempt is None or not isinstance(attempt, ActionExecutionAttempt):
        raise ActionExecutionAttemptNotFoundError("Action execution attempt not found")
    if attempt.business_id != business_id:
        raise ActionExecutionAttemptNotFoundError("Action execution attempt not found")
    return attempt


async def claim_action_execution_attempt(
    session: AsyncSession,
    *,
    business_id: UUID,
    attempt_id: UUID,
    lease_seconds: int,
) -> ActionExecutionAttempt:
    """
    Claim durable queued intent without invoking a connector.

    The caller MUST commit this dispatching state before any future connector
    call. An expired dispatching lease is ambiguous and is never auto-retried.
    """
    normalized_lease_seconds = _validate_lease_seconds(lease_seconds)
    attempt, action = await _lock_attempt_and_action(
        session,
        business_id=business_id,
        attempt_id=attempt_id,
    )
    if attempt.status != "queued" or action.status != "queued":
        raise ActionExecutionAttemptStateError(
            "Action execution attempt cannot be claimed"
        )
    _validate_attempt_identity(attempt, action)
    await _revalidate_action_authorization(
        session,
        action=action,
        business_id=business_id,
    )

    now = datetime.now(UTC)
    attempt.status = "dispatching"
    attempt.dispatch_started_at = now
    attempt.lease_acquired_at = now
    attempt.lease_expires_at = now + timedelta(seconds=normalized_lease_seconds)
    action.status = "executing"
    action.execution_started_at = now

    if isinstance(session, AsyncSession):
        record_audit(
            session,
            business_id=business_id,
            actor_user_id=None,
            event_type="action_execution.dispatching",
            entity_type="action_execution_attempt",
            entity_id=attempt.id,
            summary=(
                f"Claimed governed {attempt.action_type} dispatch intent; "
                "connector preflight remains required."
            ),
        )

    await _flush_attempt(session, attempt=attempt)
    return attempt


async def record_action_execution_success(
    session: AsyncSession,
    *,
    business_id: UUID,
    attempt_id: UUID,
    external_reference_id: str | None = None,
) -> ActionExecutionAttempt:
    reference = _normalize_optional_external_reference(external_reference_id)
    attempt, action = await _lock_attempt_and_action(
        session,
        business_id=business_id,
        attempt_id=attempt_id,
    )
    _validate_attempt_identity(attempt, action)
    if attempt.status == "succeeded":
        if (
            action.status == "succeeded"
            and attempt.external_reference_id == reference
            and action.external_reference_id == reference
            and attempt.failure_code is None
            and action.failure_code is None
        ):
            return attempt
        raise ActionExecutionAttemptConflictError(
            "Action execution outcome conflicts with persisted success"
        )
    _require_dispatching_state(attempt, action)
    now = datetime.now(UTC)
    attempt.status = "succeeded"
    attempt.completed_at = now
    attempt.external_reference_id = reference
    attempt.failure_code = None
    action.status = "succeeded"
    action.execution_completed_at = now
    action.external_reference_id = reference
    action.failure_code = None
    action.result_summary = "Provider accepted the governed action."
    await _flush_attempt(session, attempt=attempt)
    await _enqueue_workflow_resume_for_action(session, action=action)
    return attempt


async def record_action_execution_failure(
    session: AsyncSession,
    *,
    business_id: UUID,
    attempt_id: UUID,
    failure_code: str,
) -> ActionExecutionAttempt:
    normalized_code = _normalize_failure_code(failure_code)
    if normalized_code not in ACTION_EXECUTION_DEFINITE_FAILURE_CODES:
        raise ActionExecutionAttemptValidationError(
            "Invalid definite execution failure code"
        )
    attempt, action = await _lock_attempt_and_action(
        session,
        business_id=business_id,
        attempt_id=attempt_id,
    )
    _validate_attempt_identity(attempt, action)
    if attempt.status == "failed":
        if (
            action.status == "failed"
            and attempt.failure_code == normalized_code
            and action.failure_code == normalized_code
        ):
            return attempt
        raise ActionExecutionAttemptConflictError(
            "Action execution outcome conflicts with persisted failure"
        )
    _require_dispatching_state(attempt, action)
    now = datetime.now(UTC)
    attempt.status = "failed"
    attempt.completed_at = now
    attempt.external_reference_id = None
    attempt.failure_code = normalized_code
    action.status = "failed"
    action.execution_completed_at = now
    action.external_reference_id = None
    action.failure_code = normalized_code
    action.result_summary = "The connector did not complete the governed action."
    await _flush_attempt(session, attempt=attempt)
    await _enqueue_workflow_resume_for_action(session, action=action)
    return attempt


async def record_action_execution_uncertain(
    session: AsyncSession,
    *,
    business_id: UUID,
    attempt_id: UUID,
    failure_code: str = "external_outcome_uncertain",
) -> ActionExecutionAttempt:
    """
    Record an ambiguous external outcome and permanently stop blind retries.

    No exception object, provider body, headers, or raw connector response is
    accepted by this interface, so such data cannot be persisted here.
    """
    normalized_code = _normalize_uncertain_failure_code(failure_code)
    attempt, action = await _lock_attempt_and_action(
        session,
        business_id=business_id,
        attempt_id=attempt_id,
    )
    _validate_attempt_identity(attempt, action)
    if attempt.status == "uncertain":
        if (
            action.status == "uncertain"
            and attempt.failure_code == normalized_code
            and action.failure_code == normalized_code
        ):
            return attempt
        raise ActionExecutionAttemptConflictError(
            "Action execution outcome conflicts with persisted uncertainty"
        )
    _require_dispatching_state(attempt, action)
    await _apply_uncertain_outcome(
        session,
        attempt=attempt,
        action=action,
        failure_code=normalized_code,
        completed_at=datetime.now(UTC),
    )
    return attempt


async def cancel_action_execution_attempt(
    session: AsyncSession,
    *,
    business_id: UUID,
    attempt_id: UUID,
) -> ActionExecutionAttempt:
    """Cancel only an intent that has never entered dispatch."""
    attempt, action = await _lock_attempt_and_action(
        session,
        business_id=business_id,
        attempt_id=attempt_id,
    )
    _validate_attempt_identity(attempt, action)
    if attempt.status == "canceled":
        if action.status != "canceled":
            raise ActionExecutionAttemptConflictError(
                "Action execution state conflicts with its attempt"
            )
        return attempt
    if attempt.status != "queued" or action.status != "queued":
        raise ActionExecutionAttemptStateError(
            "Dispatched action execution attempts cannot be canceled"
        )

    now = datetime.now(UTC)
    attempt.status = "canceled"
    attempt.completed_at = now
    action.status = "canceled"
    action.execution_completed_at = now
    action.result_summary = (
        "Governed dispatch was canceled before provider invocation."
    )
    await _flush_attempt(session, attempt=attempt)
    await _enqueue_workflow_resume_for_action(session, action=action)
    return attempt


async def list_queued_action_execution_attempts(
    session: AsyncSession,
    *,
    business_id: UUID,
    limit: int = DEFAULT_ATTEMPT_PAGE_SIZE,
) -> list[ActionExecutionAttempt]:
    normalized_limit = _validate_page_limit(limit)
    statement = (
        select(ActionExecutionAttempt)
        .where(
            ActionExecutionAttempt.business_id == business_id,
            ActionExecutionAttempt.status == "queued",
        )
        .order_by(
            ActionExecutionAttempt.queued_at.asc(),
            ActionExecutionAttempt.id.asc(),
        )
        .limit(normalized_limit)
    )
    return await _list_attempts(
        session,
        statement=statement,
        business_id=business_id,
        required_status="queued",
    )


async def list_stale_dispatching_action_execution_attempts(
    session: AsyncSession,
    *,
    business_id: UUID,
    now: datetime | None = None,
    limit: int = DEFAULT_ATTEMPT_PAGE_SIZE,
) -> list[ActionExecutionAttempt]:
    normalized_limit = _validate_page_limit(limit)
    evaluated_at = _normalize_aware_now(now)
    statement = (
        select(ActionExecutionAttempt)
        .where(
            ActionExecutionAttempt.business_id == business_id,
            ActionExecutionAttempt.status == "dispatching",
            ActionExecutionAttempt.lease_expires_at <= evaluated_at,
        )
        .order_by(
            ActionExecutionAttempt.lease_expires_at.asc(),
            ActionExecutionAttempt.id.asc(),
        )
        .limit(normalized_limit)
    )
    return await _list_attempts(
        session,
        statement=statement,
        business_id=business_id,
        required_status="dispatching",
    )


async def mark_stale_action_execution_attempt_uncertain(
    session: AsyncSession,
    *,
    business_id: UUID,
    attempt_id: UUID,
    now: datetime | None = None,
) -> ActionExecutionAttempt:
    """Fail closed after a dispatch lease expires; never requeue the attempt."""
    evaluated_at = _normalize_aware_now(now)
    attempt, action = await _lock_attempt_and_action(
        session,
        business_id=business_id,
        attempt_id=attempt_id,
    )
    _validate_attempt_identity(attempt, action)
    if attempt.status == "uncertain":
        if (
            action.status == "uncertain"
            and attempt.failure_code == "dispatch_lease_expired"
            and action.failure_code == "dispatch_lease_expired"
        ):
            return attempt
        raise ActionExecutionAttemptConflictError(
            "Action execution outcome conflicts with persisted uncertainty"
        )
    _require_dispatching_state(attempt, action)
    if attempt.lease_expires_at is None or attempt.lease_expires_at > evaluated_at:
        raise ActionExecutionAttemptStateError(
            "Action execution attempt lease has not expired"
        )
    await _apply_uncertain_outcome(
        session,
        attempt=attempt,
        action=action,
        failure_code="dispatch_lease_expired",
        completed_at=evaluated_at,
    )
    return attempt


def build_action_attempt_idempotency_key(
    *,
    action_id: UUID,
    attempt_number: int,
) -> str:
    if not isinstance(action_id, UUID):
        raise ActionExecutionAttemptValidationError("Invalid action identifier")
    if (
        isinstance(attempt_number, bool)
        or not 1 <= attempt_number <= MAX_ATTEMPT_NUMBER
    ):
        raise ActionExecutionAttemptValidationError("Invalid attempt number")
    value = f"ai-action:{action_id}:attempt:{attempt_number}"
    if len(value) > 200:
        raise ActionExecutionAttemptValidationError("Invalid idempotency key")
    return value


async def _revalidate_action_authorization(
    session: AsyncSession,
    *,
    action: AIAction,
    business_id: UUID,
) -> tuple[ActionDefinition, ActionPayload]:
    if (
        action.policy_decision not in {"allow", "require_approval"}
        or action.policy_evaluated_at is None
        or action.policy_reason_code is None
    ):
        raise ActionExecutionAttemptStateError(
            "AI action does not have a valid policy decision"
        )

    evaluation = evaluate_action_policy(action, business_id=business_id)
    if (
        evaluation.decision != action.policy_decision
        or evaluation.reason_code != action.policy_reason_code
        or evaluation.validated_payload is None
        or action.authorized_payload_hash is None
        or action.authorized_payload_hash
        != canonical_action_payload_hash(evaluation.validated_payload)
    ):
        raise ActionExecutionAttemptConflictError(
            "AI action no longer matches its policy evaluation"
        )
    await _require_current_agent_capability(
        session,
        business_id=business_id,
        action=action,
    )
    if action.policy_decision == "require_approval":
        await _require_matching_approval(
            session,
            business_id=business_id,
            action=action,
        )

    payload = evaluation.validated_payload
    await _require_trusted_business_currency(
        session,
        business_id=business_id,
        payload=payload,
    )
    definition = ACTION_REGISTRY.get(action.action_type)
    if definition is None:
        raise ActionExecutionAttemptValidationError("Unsupported AI action")
    await require_advertising_spend_authorized(
        session,
        business_id=business_id,
        definition=definition,
        payload=payload,
    )
    return definition, payload


async def _require_current_agent_capability(
    session: AsyncSession,
    *,
    business_id: UUID,
    action: AIAction,
) -> None:
    """Recheck the server-owned agent configuration at execution time."""
    try:
        execution = await session.scalar(
            select(AIAgentExecution).where(
                AIAgentExecution.id == action.execution_id,
                AIAgentExecution.business_id == business_id,
            )
        )
        if execution is None or not isinstance(execution, AIAgentExecution):
            raise ActionExecutionAttemptStateError(
                "AI action execution authorization is unavailable"
            )
        config = await session.scalar(
            select(AIAgentConfig).where(
                AIAgentConfig.business_id == business_id,
                AIAgentConfig.role == execution.role,
            )
        )
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None

    if (
        config is None
        or not isinstance(config, AIAgentConfig)
        or config.business_id != business_id
        or config.role != execution.role
        or not config.enabled
        or config.autonomy_mode not in {"manual", "supervised", "autonomous"}
        or execution.business_id != business_id
    ):
        raise ActionExecutionAttemptStateError(
            "AI action execution authorization is unavailable"
        )
    role_capabilities = ROLE_CAPABILITIES.get(execution.role)
    required_capability = ACTION_CAPABILITY.get(action.action_type)
    if role_capabilities is None or required_capability is None:
        raise ActionExecutionAttemptValidationError(
            "AI action capability is not supported"
        )
    try:
        configured = validate_role_capabilities(
            execution.role,
            list(config.capability_config or []),
        )
    except (TypeError, ValueError):
        raise ActionExecutionAttemptValidationError(
            "AI action capability configuration is invalid"
        ) from None
    if (
        required_capability not in role_capabilities
        or required_capability not in configured
    ):
        raise ActionExecutionAttemptStateError(
            "AI action capability is no longer authorized"
        )
    if (
        config.autonomy_mode == "manual"
        and action.policy_decision != "require_approval"
    ):
        raise ActionExecutionAttemptStateError(
            "Manual AI actions require approval"
        )


async def _require_matching_approval(
    session: AsyncSession,
    *,
    business_id: UUID,
    action: AIAction,
) -> None:
    statement = select(ApprovalRequest).where(
        ApprovalRequest.business_id == business_id,
        ApprovalRequest.action_id == action.id,
        ApprovalRequest.status == "approved",
        ApprovalRequest.reason_code == action.policy_reason_code,
    ).with_for_update()
    try:
        approval = await session.scalar(statement)
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    if approval is None or not isinstance(approval, ApprovalRequest):
        raise ActionExecutionAttemptStateError("AI action approval is missing")
    if (
        approval.business_id != business_id
        or approval.action_type_snapshot != action.action_type
        or approval.authorized_payload_hash_snapshot
        != action.authorized_payload_hash
    ):
        raise ActionExecutionAttemptConflictError(
            "AI action no longer matches its approval"
        )


async def _require_trusted_business_currency(
    session: AsyncSession,
    *,
    business_id: UUID,
    payload: ActionPayload,
) -> None:
    proposed_currency = getattr(payload, "currency", None)
    if proposed_currency is None:
        return
    statement = select(Business.currency).where(Business.id == business_id)
    try:
        business_currency = await session.scalar(statement)
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    if (
        not isinstance(business_currency, str)
        or business_currency.upper() != proposed_currency
    ):
        raise ActionExecutionAttemptValidationError(
            "AI action currency is not authorized"
        )


async def _require_no_active_attempt(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_id: UUID,
) -> None:
    statement = (
        select(ActionExecutionAttempt.id)
        .where(
            ActionExecutionAttempt.business_id == business_id,
            ActionExecutionAttempt.action_id == action_id,
            ActionExecutionAttempt.status.in_(
                sorted(
                    status.value
                    for status in ACTIVE_ACTION_EXECUTION_ATTEMPT_STATUSES
                )
            ),
        )
        .with_for_update()
    )
    try:
        active_id = await session.scalar(statement)
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    if active_id is not None:
        raise ActionExecutionAttemptConflictError(
            "AI action already has an active execution attempt"
        )


async def _next_attempt_number(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_id: UUID,
) -> int:
    statement = select(func.max(ActionExecutionAttempt.attempt_number)).where(
        ActionExecutionAttempt.business_id == business_id,
        ActionExecutionAttempt.action_id == action_id,
    )
    try:
        previous = await session.scalar(statement)
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    if previous is None:
        return 1
    if isinstance(previous, bool) or not isinstance(previous, int):
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE)
    next_number = previous + 1
    if next_number > MAX_ATTEMPT_NUMBER:
        raise ActionExecutionAttemptConflictError("Maximum action attempts reached")
    return next_number


async def _get_action_for_update(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_id: UUID,
) -> AIAction:
    statement = (
        select(AIAction)
        .where(AIAction.id == action_id, AIAction.business_id == business_id)
        .with_for_update()
    )
    try:
        action = await session.scalar(statement)
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    if action is None or not isinstance(action, AIAction):
        raise ActionExecutionAttemptNotFoundError("AI action not found")
    if action.business_id != business_id:
        raise ActionExecutionAttemptNotFoundError("AI action not found")
    return action


async def _lock_attempt_and_action(
    session: AsyncSession,
    *,
    business_id: UUID,
    attempt_id: UUID,
) -> tuple[ActionExecutionAttempt, AIAction]:
    lookup = select(ActionExecutionAttempt).where(
        ActionExecutionAttempt.id == attempt_id,
        ActionExecutionAttempt.business_id == business_id,
    )
    try:
        reference = await session.scalar(lookup)
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    if reference is None or not isinstance(reference, ActionExecutionAttempt):
        raise ActionExecutionAttemptNotFoundError("Action execution attempt not found")
    if reference.business_id != business_id:
        raise ActionExecutionAttemptNotFoundError("Action execution attempt not found")

    action = await _get_action_for_update(
        session,
        business_id=business_id,
        action_id=reference.action_id,
    )
    try:
        attempt = await session.scalar(lookup.with_for_update())
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    if attempt is None or not isinstance(attempt, ActionExecutionAttempt):
        raise ActionExecutionAttemptNotFoundError("Action execution attempt not found")
    if attempt.action_id != reference.action_id or attempt.business_id != action.business_id:
        raise ActionExecutionAttemptConflictError(
            "Action execution attempt ownership conflicts"
        )
    return attempt, action


def _require_dispatching_state(
    attempt: ActionExecutionAttempt,
    action: AIAction,
) -> None:
    if attempt.status != "dispatching" or action.status != "executing":
        if attempt.status == "uncertain" or action.status == "uncertain":
            raise ActionExecutionOutcomeUncertainError(
                "Uncertain action outcome prohibits automatic retry"
            )
        raise ActionExecutionAttemptStateError(
            "Action execution attempt is not dispatching"
        )
    _validate_attempt_identity(attempt, action)


def _validate_attempt_identity(
    attempt: ActionExecutionAttempt,
    action: AIAction,
) -> None:
    expected_key = build_action_attempt_idempotency_key(
        action_id=action.id,
        attempt_number=attempt.attempt_number,
    )
    definition = ACTION_REGISTRY.get(action.action_type)
    if (
        definition is None
        or attempt.action_id != action.id
        or attempt.business_id != action.business_id
        or attempt.action_type != action.action_type
        or attempt.capability != definition.capability
        or attempt.idempotency_key != expected_key
    ):
        raise ActionExecutionAttemptConflictError(
            "Action execution attempt identity conflicts"
        )


async def _apply_uncertain_outcome(
    session: AsyncSession,
    *,
    attempt: ActionExecutionAttempt,
    action: AIAction,
    failure_code: str,
    completed_at: datetime,
) -> None:
    attempt.status = "uncertain"
    attempt.completed_at = completed_at
    attempt.external_reference_id = None
    attempt.failure_code = failure_code
    action.status = "uncertain"
    action.execution_completed_at = completed_at
    action.external_reference_id = None
    action.failure_code = failure_code
    action.result_summary = (
        "The provider outcome is uncertain; reconciliation is required."
    )
    await _flush_attempt(session, attempt=attempt)
    await _enqueue_workflow_resume_for_action(session, action=action)


async def _enqueue_workflow_resume_for_action(
    session: AsyncSession,
    *,
    action: AIAction,
) -> None:
    """Wake a waiting workflow only after its action has a durable result."""
    if not isinstance(session, AsyncSession):
        return
    from app.models.automation import AutomationNodeRun
    from app.services.background_jobs import enqueue_job

    node_run = await session.scalar(
        select(AutomationNodeRun)
        .where(
            AutomationNodeRun.business_id == action.business_id,
            AutomationNodeRun.action_id == action.id,
            AutomationNodeRun.status == "waiting",
        )
        .order_by(AutomationNodeRun.created_at.desc(), AutomationNodeRun.id.desc())
        .limit(1)
    )
    if node_run is None:
        return
    await enqueue_job(
        session,
        business_id=action.business_id,
        job_type="resume_workflow_run",
        idempotency_key=(
            f"workflow-action:{node_run.workflow_run_id}:{node_run.id}:"
            f"{action.status}"
        ),
        workflow_run_id=node_run.workflow_run_id,
        node_run_id=node_run.id,
    )


async def _list_attempts(
    session: AsyncSession,
    *,
    statement,
    business_id: UUID,
    required_status: str,
) -> list[ActionExecutionAttempt]:
    try:
        result = await session.scalars(statement)
        attempts = list(result.all())
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    if any(
        not isinstance(item, ActionExecutionAttempt)
        or item.business_id != business_id
        or item.status != required_status
        for item in attempts
    ):
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE)
    return attempts


def _validate_lease_seconds(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_LEASE_SECONDS <= value <= MAX_LEASE_SECONDS
    ):
        raise ActionExecutionAttemptValidationError("Invalid dispatch lease")
    return value


def _validate_page_limit(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_ATTEMPT_PAGE_SIZE
    ):
        raise ActionExecutionAttemptValidationError("Invalid attempt page size")
    return value


def _normalize_failure_code(value: str) -> str:
    if not isinstance(value, str):
        raise ActionExecutionAttemptValidationError("Invalid execution failure code")
    normalized = value.strip()
    if _FAILURE_CODE_PATTERN.fullmatch(normalized) is None:
        raise ActionExecutionAttemptValidationError("Invalid execution failure code")
    return normalized


def _normalize_uncertain_failure_code(value: str) -> str:
    normalized = _normalize_failure_code(value)
    if normalized not in ACTION_EXECUTION_UNCERTAIN_FAILURE_CODES:
        raise ActionExecutionAttemptValidationError(
            "Invalid uncertain execution failure code"
        )
    return normalized


def _normalize_optional_external_reference(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ActionExecutionAttemptValidationError("Invalid external reference")
    normalized = value.strip()
    if _EXTERNAL_REFERENCE_PATTERN.fullmatch(normalized) is None:
        raise ActionExecutionAttemptValidationError("Invalid external reference")
    return normalized


def _normalize_aware_now(value: datetime | None) -> datetime:
    normalized = value or datetime.now(UTC)
    if normalized.tzinfo is None or normalized.utcoffset() is None:
        raise ActionExecutionAttemptValidationError("Timestamp must be timezone-aware")
    return normalized


async def _flush_attempt(
    session: AsyncSession,
    *,
    attempt: ActionExecutionAttempt,
    refresh_created: bool = False,
) -> None:
    try:
        await session.flush()
        attributes = ["updated_at"]
        if refresh_created:
            attributes.append("created_at")
        await session.refresh(attempt, attribute_names=attributes)
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
