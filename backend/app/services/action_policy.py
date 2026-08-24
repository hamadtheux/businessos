from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.ai_action import (
    AIActionConflictError,
    AIActionNotFoundError,
    AIActionPersistenceError,
    AIActionStateError,
    AIActionValidationError,
    UnsupportedAIActionError,
)
from app.models.ai_action import AIAction
from app.schemas.ai_action_payload import ActionPayloadType
from app.services.action_registry import ACTION_REGISTRY, ActionDefinition


PolicyDecision = Literal["allow", "require_approval", "block"]


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    decision: PolicyDecision
    reason_code: str
    validated_payload: ActionPayloadType | None


def evaluate_action_policy(
    action: AIAction,
    *,
    business_id: UUID,
) -> PolicyEvaluation:
    """Pure deterministic policy evaluation with fixed reason precedence."""
    if action.business_id != business_id:
        raise AIActionNotFoundError("AI action not found")

    definition = ACTION_REGISTRY.get(action.action_type)
    if definition is None:
        return PolicyEvaluation(
            decision="block",
            reason_code="unsupported_action",
            validated_payload=None,
        )

    try:
        payload = ACTION_REGISTRY.validate_payload(
            action.action_type,
            action.action_payload,
        )
    except (AIActionValidationError, UnsupportedAIActionError):
        return PolicyEvaluation(
            decision="block",
            reason_code="invalid_action_payload",
            validated_payload=None,
        )

    reason = _mandatory_approval_reason(action, definition)
    if reason is not None:
        return PolicyEvaluation(
            decision="require_approval",
            reason_code=reason,
            validated_payload=payload,
        )

    return PolicyEvaluation(
        decision="allow",
        reason_code="policy_allow",
        validated_payload=payload,
    )


async def evaluate_ai_action_policy(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_id: UUID,
) -> AIAction:
    """Lock and transition one proposed action without committing."""
    action = await _get_action_for_policy(
        session,
        business_id=business_id,
        action_id=action_id,
    )
    evaluation = evaluate_action_policy(action, business_id=business_id)

    if action.status != "proposed":
        _validate_repeated_evaluation(action, evaluation)
        return action

    action.action_payload = (
        evaluation.validated_payload.model_dump(mode="json")
        if evaluation.validated_payload is not None
        else {}
    )
    action.authorized_payload_hash = (
        canonical_action_payload_hash(evaluation.validated_payload)
        if evaluation.validated_payload is not None
        else None
    )
    action.policy_decision = evaluation.decision
    action.policy_reason_code = evaluation.reason_code
    action.policy_evaluated_at = datetime.now(UTC)
    action.status = {
        "allow": "ready",
        "require_approval": "pending_approval",
        "block": "blocked",
    }[evaluation.decision]

    try:
        await session.flush()
    except SQLAlchemyError:
        raise AIActionPersistenceError("Unable to evaluate AI action policy") from None
    return action


def _mandatory_approval_reason(
    action: AIAction,
    definition: ActionDefinition,
) -> str | None:
    if definition.destructive:
        return "destructive_action"
    if definition.external_communication:
        return "external_communication"
    if definition.external_publication:
        return "external_publication"
    if definition.campaign_launch:
        return "campaign_launch"
    if action.action_type == "change_ad_budget":
        return "ad_spend_change"
    if action.risk_level == "critical" or definition.default_risk_level == "critical":
        return "critical_action"
    if definition.spend_related:
        return "human_approval_required"
    if definition.always_requires_approval or action.proposed_requires_approval:
        return "human_approval_required"
    return None


def canonical_action_payload_hash(payload: ActionPayloadType) -> str:
    encoded = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


async def _get_action_for_policy(
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
        raise AIActionPersistenceError("Unable to evaluate AI action policy") from None
    if action is None or not isinstance(action, AIAction):
        raise AIActionNotFoundError("AI action not found")
    if action.business_id != business_id:
        raise AIActionNotFoundError("AI action not found")
    return action


def _validate_repeated_evaluation(
    action: AIAction,
    evaluation: PolicyEvaluation,
) -> None:
    if action.status in {"executing", "succeeded", "failed", "canceled", "rejected", "expired"}:
        raise AIActionStateError("AI action policy cannot be reevaluated")
    hash_matches = (
        action.authorized_payload_hash is None
        if evaluation.validated_payload is None
        else action.authorized_payload_hash
        == canonical_action_payload_hash(evaluation.validated_payload)
    )
    if (
        action.policy_decision != evaluation.decision
        or action.policy_reason_code != evaluation.reason_code
        or action.policy_evaluated_at is None
        or not hash_matches
    ):
        raise AIActionConflictError("AI action policy reevaluation conflicts")
    expected_statuses = {
        "allow": {"ready"},
        "require_approval": {"pending_approval", "ready"},
        "block": {"blocked"},
    }[evaluation.decision]
    if action.status not in expected_statuses:
        raise AIActionConflictError("AI action policy reevaluation conflicts")
