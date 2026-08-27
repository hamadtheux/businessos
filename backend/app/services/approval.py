from __future__ import annotations

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.approval import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalPersistenceError,
    ApprovalStateError,
    ApprovalValidationError,
)
from app.exceptions.action_execution_attempt import (
    ActionExecutionAttemptConflictError,
    ActionExecutionAttemptStateError,
    ActionExecutionAttemptValidationError,
)
from app.models.ai_action import AIAction
from app.models.approval_request import ApprovalRequest
from app.models.automation import AutomationNodeRun
from app.models.notification import Notification
from app.schemas.approval import ApprovalStatus, ensure_aware_datetime
from app.services.automation_events import record_automation_event
from app.services.background_jobs import enqueue_job
from app.services.operations import record_audit
from app.services.action_policy import (
    canonical_action_payload_hash,
    evaluate_action_policy,
)


DEFAULT_APPROVAL_PAGE_SIZE: Final = 50
MAX_APPROVAL_PAGE_SIZE: Final = 200
_PERSISTENCE_MESSAGE: Final = "Unable to persist approval request"


async def create_approval_request(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_id: UUID,
    reason_code: str,
    requested_by_user_id: UUID | None = None,
    expires_at: datetime | None = None,
) -> ApprovalRequest:
    """Create one pending request after locking its tenant-owned action."""
    normalized_reason = _normalize_reason_code(reason_code)
    now = datetime.now(UTC)
    _validate_expiration(expires_at, now=now)

    action = await _get_action_for_update(
        session,
        business_id=business_id,
        action_id=action_id,
    )

    if (
        action.status != "pending_approval"
        or action.policy_decision != "require_approval"
        or action.policy_reason_code != normalized_reason
    ):
        raise ApprovalStateError("Action is not pending approval")
    action_type_snapshot, payload_hash_snapshot = _action_authorization_snapshot(
        action, business_id=business_id
    )

    existing = await _get_pending_for_action(
        session,
        business_id=business_id,
        action_id=action_id,
    )
    if existing is not None:
        if (
            existing.reason_code != normalized_reason
            or existing.action_type_snapshot != action_type_snapshot
            or existing.authorized_payload_hash_snapshot != payload_hash_snapshot
        ):
            raise ApprovalConflictError("Pending approval request conflicts")
        return existing

    approval = ApprovalRequest(
        business_id=business_id,
        action_id=action_id,
        workflow_node_run_id=None,
        requested_by_user_id=requested_by_user_id,
        status="pending",
        reason_code=normalized_reason,
        action_type_snapshot=action_type_snapshot,
        authorized_payload_hash_snapshot=payload_hash_snapshot,
        requested_at=now,
        expires_at=expires_at,
        decided_at=None,
        decided_by_user_id=None,
        decision_actor_id=None,
        decision_note=None,
    )
    session.add(approval)
    await _flush(session, approval=approval, refresh_created=True)
    record_automation_event(
        session, business_id=business_id, event_type="ai_action_requires_approval",
        entity_type="ai_action", entity_id=action_id,
        payload={"status": "pending_approval"},
    )
    return approval


async def create_workflow_approval_request(
    session: AsyncSession,
    *,
    business_id: UUID,
    workflow_node_run_id: UUID,
    reason_code: str,
    requested_by_user_id: UUID | None = None,
    expires_at: datetime | None = None,
) -> ApprovalRequest:
    """Create a pure internal workflow review in the existing queue."""
    normalized_reason = _normalize_reason_code(reason_code)
    now = datetime.now(UTC)
    _validate_expiration(expires_at, now=now)
    node_run = await _get_node_run_for_update(
        session, business_id=business_id, workflow_node_run_id=workflow_node_run_id
    )
    if node_run.status != "waiting":
        raise ApprovalStateError("Workflow node is not waiting for approval")
    existing = await session.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.business_id == business_id,
            ApprovalRequest.workflow_node_run_id == workflow_node_run_id,
            ApprovalRequest.status == "pending",
        ).with_for_update()
    )
    if existing is not None:
        if existing.reason_code != normalized_reason:
            raise ApprovalConflictError("Pending approval request conflicts")
        return existing
    approval = ApprovalRequest(
        business_id=business_id,
        action_id=None,
        workflow_node_run_id=workflow_node_run_id,
        requested_by_user_id=requested_by_user_id,
        status="pending",
        reason_code=normalized_reason,
        action_type_snapshot=None,
        authorized_payload_hash_snapshot=None,
        requested_at=now,
        expires_at=expires_at,
        decided_at=None,
        decided_by_user_id=None,
        decision_actor_id=None,
        decision_note=None,
    )
    session.add(approval)
    await _flush(session, approval=approval, refresh_created=True)
    return approval


async def get_approval_request(
    session: AsyncSession,
    *,
    business_id: UUID,
    approval_id: UUID,
) -> ApprovalRequest:
    statement = select(ApprovalRequest).where(
        ApprovalRequest.id == approval_id,
        ApprovalRequest.business_id == business_id,
    )
    try:
        approval = await session.scalar(statement)
    except SQLAlchemyError:
        raise ApprovalPersistenceError(_PERSISTENCE_MESSAGE) from None

    if approval is None:
        raise ApprovalNotFoundError("Approval request not found")
    if not isinstance(approval, ApprovalRequest) or approval.business_id != business_id:
        raise ApprovalNotFoundError("Approval request not found")
    return approval


async def list_approval_requests(
    session: AsyncSession,
    *,
    business_id: UUID,
    approval_status: ApprovalStatus | None = "pending",
    limit: int = DEFAULT_APPROVAL_PAGE_SIZE,
) -> list[ApprovalRequest]:
    if isinstance(limit, bool) or not 1 <= limit <= MAX_APPROVAL_PAGE_SIZE:
        raise ApprovalValidationError("Invalid approval page size")

    statement = select(ApprovalRequest).where(
        ApprovalRequest.business_id == business_id
    )
    if approval_status is not None:
        statement = statement.where(ApprovalRequest.status == approval_status)
    statement = statement.order_by(
        ApprovalRequest.created_at.desc(),
        ApprovalRequest.id.desc(),
    ).limit(limit)

    try:
        result = await session.scalars(statement)
        approvals = list(result.all())
    except SQLAlchemyError:
        raise ApprovalPersistenceError(_PERSISTENCE_MESSAGE) from None

    if any(
        not isinstance(item, ApprovalRequest) or item.business_id != business_id
        for item in approvals
    ):
        raise ApprovalPersistenceError(_PERSISTENCE_MESSAGE)
    return approvals


async def approve_approval_request(
    session: AsyncSession,
    *,
    business_id: UUID,
    approval_id: UUID,
    decided_by_user_id: UUID,
    decision_note: str | None = None,
) -> ApprovalRequest:
    return await _decide_approval_request(
        session,
        business_id=business_id,
        approval_id=approval_id,
        target_status="approved",
        action_status="ready",
        decided_by_user_id=decided_by_user_id,
        decision_note=decision_note,
    )


async def reject_approval_request(
    session: AsyncSession,
    *,
    business_id: UUID,
    approval_id: UUID,
    decided_by_user_id: UUID,
    decision_note: str | None = None,
) -> ApprovalRequest:
    return await _decide_approval_request(
        session,
        business_id=business_id,
        approval_id=approval_id,
        target_status="rejected",
        action_status="rejected",
        decided_by_user_id=decided_by_user_id,
        decision_note=decision_note,
    )


async def expire_approval_request(
    session: AsyncSession,
    *,
    business_id: UUID,
    approval_id: UUID,
    now: datetime | None = None,
) -> ApprovalRequest:
    decision_time = now or datetime.now(UTC)
    ensure_aware_datetime(decision_time, field_name="now")

    approval, action, node_run = await _lock_approval_and_target(
        session,
        business_id=business_id,
        approval_id=approval_id,
    )
    if approval.status == "expired":
        return approval
    if approval.status != "pending" or not _target_is_pending(action, node_run):
        raise ApprovalStateError("Approval request cannot expire")
    if approval.expires_at is not None and decision_time < approval.expires_at:
        raise ApprovalStateError("Approval request has not expired")

    _apply_decision(
        approval,
        action,
        target_status="expired",
        action_status="expired",
        decision_time=decision_time,
        actor_id=None,
        decision_note=None,
    )
    await _flush(session, approval=approval)
    await _enqueue_workflow_resume(session, approval=approval, node_run=node_run)
    return approval


async def cancel_approval_request(
    session: AsyncSession,
    *,
    business_id: UUID,
    approval_id: UUID,
    canceled_by_user_id: UUID | None = None,
) -> ApprovalRequest:
    approval, action, node_run = await _lock_approval_and_target(
        session,
        business_id=business_id,
        approval_id=approval_id,
    )
    if approval.status == "canceled":
        return approval
    if approval.status != "pending" or not _target_is_pending(action, node_run):
        raise ApprovalStateError("Approval request cannot be canceled")

    _apply_decision(
        approval,
        action,
        target_status="canceled",
        action_status="canceled",
        decision_time=datetime.now(UTC),
        actor_id=canceled_by_user_id,
        decision_note=None,
    )
    await _flush(session, approval=approval)
    await _enqueue_workflow_resume(session, approval=approval, node_run=node_run)
    return approval


async def _decide_approval_request(
    session: AsyncSession,
    *,
    business_id: UUID,
    approval_id: UUID,
    target_status: str,
    action_status: str,
    decided_by_user_id: UUID,
    decision_note: str | None,
) -> ApprovalRequest:
    note = _normalize_decision_note(decision_note)
    approval, action, node_run = await _lock_approval_and_target(
        session,
        business_id=business_id,
        approval_id=approval_id,
    )

    if approval.status == target_status:
        if action is not None and action.status != action_status:
            raise ApprovalStateError("Approval request cannot be decided")
        await _enqueue_workflow_resume(session, approval=approval, node_run=node_run)
        if target_status == "approved" and action is not None:
            await _queue_approved_action_if_ready(
                session, approval=approval, action=action
            )
        return approval
    if approval.status != "pending" or not _target_is_pending(action, node_run):
        raise ApprovalStateError("Approval request cannot be decided")
    if action is not None and (
        action.policy_decision != "require_approval"
        or action.policy_reason_code != approval.reason_code
    ):
        raise ApprovalConflictError("Approval request conflicts with action policy")
    if action is not None:
        action_type_snapshot, payload_hash_snapshot = _action_authorization_snapshot(
            action, business_id=business_id
        )
        if (
            approval.action_type_snapshot != action_type_snapshot
            or approval.authorized_payload_hash_snapshot != payload_hash_snapshot
        ):
            raise ApprovalConflictError(
                "Approval request conflicts with action authorization"
            )
    if approval.expires_at is not None and datetime.now(UTC) >= approval.expires_at:
        raise ApprovalStateError("Approval request has expired")

    _apply_decision(
        approval,
        action,
        target_status=target_status,
        action_status=action_status,
        decision_time=datetime.now(UTC),
        actor_id=decided_by_user_id,
        decision_note=note,
    )
    if isinstance(session, AsyncSession):
        record_audit(
            session,
            business_id=business_id,
            actor_user_id=decided_by_user_id,
            event_type=f"approval.{target_status}",
            entity_type="approval_request",
            entity_id=approval.id,
            summary=(
                f"Approval request was {target_status}; any approved action "
                "must pass server revalidation before durable dispatch."
            ),
        )
    await _flush(session, approval=approval)
    await _enqueue_workflow_resume(session, approval=approval, node_run=node_run)
    if target_status == "approved" and action is not None:
        await _queue_approved_action_if_ready(
            session, approval=approval, action=action
        )
    return approval


async def _queue_approved_action_if_ready(
    session: AsyncSession,
    *,
    approval: ApprovalRequest,
    action: AIAction,
) -> None:
    if not isinstance(session, AsyncSession) or action.status != "ready":
        return
    from app.integrations.action_boundary import CONNECTOR_ACTION_TYPES
    from app.services.action_execution_attempt import (
        prepare_action_execution_attempt,
    )

    if action.action_type not in CONNECTOR_ACTION_TYPES:
        return

    try:
        await prepare_action_execution_attempt(
            session,
            business_id=approval.business_id,
            action_id=action.id,
        )
    except (
        ActionExecutionAttemptConflictError,
        ActionExecutionAttemptStateError,
        ActionExecutionAttemptValidationError,
    ):
        session.add(
            Notification(
                business_id=approval.business_id,
                recipient_user_id=None,
                category="action_configuration",
                title="Approved action needs configuration",
                message=(
                    "The decision was recorded, but spend, connector, or "
                    "action preconditions must be completed before dispatch."
                ),
                priority="medium",
                read=False,
                related_entity_type="ai_action",
                related_entity_id=action.id,
            )
        )


async def _enqueue_workflow_resume(
    session: AsyncSession,
    *,
    approval: ApprovalRequest,
    node_run: AutomationNodeRun | None,
) -> None:
    if node_run is None:
        return
    await enqueue_job(
        session,
        business_id=approval.business_id,
        job_type="resume_workflow_run",
        idempotency_key=(
            f"workflow-approval:{node_run.workflow_run_id}:{node_run.id}:"
            f"{approval.id}:{approval.status}"
        ),
        workflow_run_id=node_run.workflow_run_id,
        node_run_id=node_run.id,
    )


async def _lock_approval_and_target(
    session: AsyncSession,
    *,
    business_id: UUID,
    approval_id: UUID,
) -> tuple[ApprovalRequest, AIAction | None, AutomationNodeRun | None]:
    # Resolve the immutable action reference first without locking, then use
    # the same action -> approval lock order as create_approval_request().
    # A final locked reread prevents acting on a changed or missing row.
    lookup = select(ApprovalRequest).where(
        ApprovalRequest.id == approval_id,
        ApprovalRequest.business_id == business_id,
    )
    try:
        reference = await session.scalar(lookup)
    except SQLAlchemyError:
        raise ApprovalPersistenceError(_PERSISTENCE_MESSAGE) from None
    if reference is None or not isinstance(reference, ApprovalRequest):
        raise ApprovalNotFoundError("Approval request not found")
    if reference.business_id != business_id:
        raise ApprovalNotFoundError("Approval request not found")

    action = None
    node_run = None
    if reference.action_id is not None:
        action = await _get_action_for_update(
            session, business_id=business_id, action_id=reference.action_id
        )
    elif reference.workflow_node_run_id is not None:
        node_run = await _get_node_run_for_update(
            session,
            business_id=business_id,
            workflow_node_run_id=reference.workflow_node_run_id,
        )
    else:
        raise ApprovalConflictError("Approval request has no target")

    statement = lookup.with_for_update()
    try:
        approval = await session.scalar(statement)
    except SQLAlchemyError:
        raise ApprovalPersistenceError(_PERSISTENCE_MESSAGE) from None
    if approval is None or not isinstance(approval, ApprovalRequest):
        raise ApprovalNotFoundError("Approval request not found")
    target = action or node_run
    if target is None or target.business_id != approval.business_id:
        raise ApprovalConflictError("Approval request ownership conflicts")
    if approval.action_id != reference.action_id or approval.workflow_node_run_id != reference.workflow_node_run_id:
        raise ApprovalConflictError("Approval request ownership conflicts")
    return approval, action, node_run


async def _get_node_run_for_update(
    session: AsyncSession,
    *,
    business_id: UUID,
    workflow_node_run_id: UUID,
) -> AutomationNodeRun:
    try:
        value = await session.scalar(
            select(AutomationNodeRun).where(
                AutomationNodeRun.id == workflow_node_run_id,
                AutomationNodeRun.business_id == business_id,
            ).with_for_update()
        )
    except SQLAlchemyError:
        raise ApprovalPersistenceError(_PERSISTENCE_MESSAGE) from None
    if value is None:
        raise ApprovalNotFoundError("Workflow node run not found")
    return value


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
        raise ApprovalPersistenceError(_PERSISTENCE_MESSAGE) from None
    if action is None or not isinstance(action, AIAction):
        raise ApprovalNotFoundError("AI action not found")
    if action.business_id != business_id:
        raise ApprovalNotFoundError("AI action not found")
    return action


async def _get_pending_for_action(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_id: UUID,
) -> ApprovalRequest | None:
    statement = (
        select(ApprovalRequest)
        .where(
            ApprovalRequest.business_id == business_id,
            ApprovalRequest.action_id == action_id,
            ApprovalRequest.status == "pending",
        )
        .with_for_update()
    )
    try:
        approval = await session.scalar(statement)
    except SQLAlchemyError:
        raise ApprovalPersistenceError(_PERSISTENCE_MESSAGE) from None
    if approval is not None and not isinstance(approval, ApprovalRequest):
        raise ApprovalPersistenceError(_PERSISTENCE_MESSAGE)
    return approval


def _apply_decision(
    approval: ApprovalRequest,
    action: AIAction | None,
    *,
    target_status: str,
    action_status: str,
    decision_time: datetime,
    actor_id: UUID | None,
    decision_note: str | None,
) -> None:
    approval.status = target_status
    approval.decided_at = decision_time
    approval.decided_by_user_id = actor_id
    approval.decision_actor_id = actor_id
    approval.decision_note = decision_note
    if action is not None:
        action.status = action_status


def _target_is_pending(action: AIAction | None, node_run: AutomationNodeRun | None) -> bool:
    return (
        action is not None and action.status == "pending_approval"
    ) or (
        node_run is not None and node_run.status == "waiting"
    )


def _action_authorization_snapshot(
    action: AIAction,
    *,
    business_id: UUID,
) -> tuple[str, str]:
    """Return the exact registry/policy identity a human is authorizing."""
    evaluation = evaluate_action_policy(action, business_id=business_id)
    if (
        evaluation.decision != "require_approval"
        or evaluation.reason_code != action.policy_reason_code
        or evaluation.validated_payload is None
        or action.authorized_payload_hash is None
    ):
        raise ApprovalConflictError("Action authorization is incomplete")
    current_hash = canonical_action_payload_hash(evaluation.validated_payload)
    if current_hash != action.authorized_payload_hash:
        raise ApprovalConflictError("Action authorization no longer matches")
    return action.action_type, current_hash


def _normalize_reason_code(value: str) -> str:
    if not isinstance(value, str):
        raise ApprovalValidationError("Invalid approval reason code")
    normalized = value.strip()
    if not normalized or len(normalized) > 64:
        raise ApprovalValidationError("Invalid approval reason code")
    return normalized


def _normalize_decision_note(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApprovalValidationError("Invalid approval decision note")
    normalized = value.strip()
    if not normalized or len(normalized) > 2_000:
        raise ApprovalValidationError("Invalid approval decision note")
    return normalized


def _validate_expiration(value: datetime | None, *, now: datetime) -> None:
    if value is None:
        return
    try:
        ensure_aware_datetime(value, field_name="expires_at")
        if value <= now:
            raise ValueError
    except (TypeError, ValueError):
        raise ApprovalValidationError("Invalid approval expiration") from None


async def _flush(
    session: AsyncSession,
    *,
    approval: ApprovalRequest,
    refresh_created: bool = False,
) -> None:
    try:
        await session.flush()
        attributes = ["updated_at"]
        if refresh_created:
            attributes.append("created_at")
        await session.refresh(approval, attribute_names=attributes)
    except SQLAlchemyError:
        raise ApprovalPersistenceError(_PERSISTENCE_MESSAGE) from None
