from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.ai_action import AIActionConflictError
from app.models.ai_action import AIAction
from app.models.approval_request import ApprovalRequest
from app.services.action_policy import evaluate_ai_action_policy
from app.services.approval import create_approval_request


@dataclass(frozen=True, slots=True)
class GovernedAction:
    action: AIAction
    approval: ApprovalRequest | None


async def govern_ai_action(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_id: UUID,
    requested_by_user_id: UUID | None = None,
) -> GovernedAction:
    """Evaluate policy and materialize any required approval atomically."""
    action = await evaluate_ai_action_policy(
        session,
        business_id=business_id,
        action_id=action_id,
    )
    approval = None
    if action.policy_decision == "require_approval":
        if action.policy_reason_code is None:
            raise AIActionConflictError("AI action policy is incomplete")
        approval = await create_approval_request(
            session,
            business_id=business_id,
            action_id=action.id,
            reason_code=action.policy_reason_code,
            requested_by_user_id=requested_by_user_id,
        )
    return GovernedAction(action=action, approval=approval)


async def govern_materialized_ai_actions(
    session: AsyncSession,
    *,
    business_id: UUID,
    actions: list[AIAction],
    requested_by_user_id: UUID | None = None,
) -> list[GovernedAction]:
    """Govern a materialized batch without committing or executing actions."""
    governed: list[GovernedAction] = []
    for action in sorted(actions, key=lambda item: (item.proposal_index, str(item.id))):
        if action.business_id != business_id or action.id is None:
            raise AIActionConflictError("AI action ownership conflicts")
        governed.append(
            await govern_ai_action(
                session,
                business_id=business_id,
                action_id=action.id,
                requested_by_user_id=requested_by_user_id,
            )
        )
    return governed
