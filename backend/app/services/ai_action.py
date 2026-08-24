from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.ai_action import (
    AIActionConflictError,
    AIActionError,
    AIActionNotFoundError,
    AIActionPersistenceError,
    AIActionStateError,
    AIActionValidationError,
)
from app.models.ai_action import AIAction
from app.models.ai_agent_execution import AIAgentExecution
from app.schemas.ai_agent import (
    AIAgentProposedAction,
    MAX_AGENT_ACTIONS,
)
from app.services.action_registry import ACTION_REGISTRY


_MATERIALIZABLE_EXECUTION_STATUSES = {
    "completed",
    "needs_approval",
    "blocked",
}


async def materialize_ai_actions(
    session: AsyncSession,
    *,
    business_id: UUID,
    execution_id: UUID,
) -> list[AIAction]:
    """
    Materialize one AI execution's validated proposed actions into durable
    tenant-scoped AIAction rows.

    This operation is idempotent:

    - first call creates exactly one AIAction per proposal
    - repeated calls return the already materialized rows when they still
      match the immutable proposal identity
    - partial or conflicting materialization fails closed

    This function performs NO commit and executes NO business action.

    It never:
    - sends messages
    - publishes posts
    - creates or launches ad campaigns
    - changes budgets
    - calls integrations
    - writes credentials
    - performs external side effects
    """
    execution = await _get_materializable_execution(
        session,
        business_id=business_id,
        execution_id=execution_id,
    )

    proposals = _validate_persisted_proposals(
        execution.proposed_actions
    )

    existing_actions = await _get_existing_actions(
        session,
        business_id=business_id,
        execution_id=execution_id,
    )

    if existing_actions:
        _validate_existing_materialization(
            existing_actions,
            proposals,
        )

        return existing_actions

    if not proposals:
        return []

    actions = [
        AIAction(
            business_id=business_id,
            execution_id=execution_id,
            proposal_index=index,
            action_type=proposal.action_type,
            description=proposal.description,
            risk_level=proposal.risk_level,
            proposed_requires_approval=(
                proposal.requires_approval
            ),
            status="proposed",
            action_payload=_normalized_action_payload(
                proposal
            ),
            policy_decision=None,
            policy_reason_code=None,
            policy_evaluated_at=None,
            execution_started_at=None,
            execution_completed_at=None,
            result_summary=None,
            failure_code=None,
            external_reference_id=None,
        )
        for index, proposal in enumerate(
            proposals
        )
    ]

    try:
        session.add_all(
            actions
        )

        await session.flush()

    except SQLAlchemyError:
        raise AIActionPersistenceError(
            "Unable to materialize AI actions"
        ) from None

    return actions


async def get_ai_action(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_id: UUID,
) -> AIAction:
    """
    Return one AI action owned by the authorized business.

    Cross-business access intentionally behaves the same as a missing action.
    """
    statement = (
        select(AIAction)
        .where(
            AIAction.id == action_id,
            AIAction.business_id == business_id,
        )
    )

    try:
        action = await session.scalar(
            statement
        )

    except SQLAlchemyError:
        raise AIActionPersistenceError(
            "Unable to read AI action"
        ) from None

    if action is None:
        raise AIActionNotFoundError(
            "AI action not found"
        )

    if not isinstance(
        action,
        AIAction,
    ):
        raise AIActionPersistenceError(
            "Unable to read AI action"
        )

    if action.business_id != business_id:
        raise AIActionNotFoundError(
            "AI action not found"
        )

    return action


async def list_execution_ai_actions(
    session: AsyncSession,
    *,
    business_id: UUID,
    execution_id: UUID,
) -> list[AIAction]:
    """
    Return all materialized actions for one execution in stable proposal order.
    """
    statement = (
        select(AIAction)
        .where(
            AIAction.business_id == business_id,
            AIAction.execution_id == execution_id,
        )
        .order_by(
            AIAction.proposal_index.asc(),
            AIAction.id.asc(),
        )
    )

    try:
        result = await session.scalars(
            statement
        )

        actions = list(
            result.all()
        )

    except SQLAlchemyError:
        raise AIActionPersistenceError(
            "Unable to list AI actions"
        ) from None

    for action in actions:
        if action.business_id != business_id:
            raise AIActionPersistenceError(
                "Unable to list AI actions"
            )

        if action.execution_id != execution_id:
            raise AIActionPersistenceError(
                "Unable to list AI actions"
            )

    return actions


async def _get_materializable_execution(
    session: AsyncSession,
    *,
    business_id: UUID,
    execution_id: UUID,
) -> AIAgentExecution:
    statement = (
        select(AIAgentExecution)
        .where(
            AIAgentExecution.id == execution_id,
            AIAgentExecution.business_id == business_id,
        )
    )

    try:
        execution = await session.scalar(
            statement
        )

    except SQLAlchemyError:
        raise AIActionPersistenceError(
            "Unable to read AI agent execution"
        ) from None

    if execution is None:
        raise AIActionNotFoundError(
            "AI agent execution not found"
        )

    if not isinstance(
        execution,
        AIAgentExecution,
    ):
        raise AIActionPersistenceError(
            "Unable to read AI agent execution"
        )

    if execution.business_id != business_id:
        raise AIActionNotFoundError(
            "AI agent execution not found"
        )

    if (
        execution.status
        not in _MATERIALIZABLE_EXECUTION_STATUSES
    ):
        raise AIActionStateError(
            "AI agent execution is not ready for action materialization"
        )

    if execution.completed_at is None:
        raise AIActionStateError(
            "AI agent execution must be terminal before action materialization"
        )

    return execution


def _validate_persisted_proposals(
    value: object,
) -> list[AIAgentProposedAction]:
    """
    Revalidate the execution ledger's JSON proposals at the action boundary.

    Although the ledger originally receives validated runtime output, the
    action subsystem independently validates persisted JSON before treating
    it as authoritative action data.
    """
    if not isinstance(
        value,
        list,
    ):
        raise AIActionValidationError(
            "Persisted proposed actions are invalid"
        )

    if len(value) > MAX_AGENT_ACTIONS:
        raise AIActionValidationError(
            "Persisted proposed actions exceed the allowed limit"
        )

    proposals: list[
        AIAgentProposedAction
    ] = []

    for raw_action in value:
        try:
            proposal = (
                AIAgentProposedAction.model_validate(
                    raw_action
                )
            )

        except ValidationError:
            raise AIActionValidationError(
                "Persisted proposed action is invalid"
            ) from None

        except Exception:
            raise AIActionValidationError(
                "Persisted proposed action is invalid"
            ) from None

        proposals.append(
            proposal
        )

    return proposals


async def _get_existing_actions(
    session: AsyncSession,
    *,
    business_id: UUID,
    execution_id: UUID,
) -> list[AIAction]:
    statement = (
        select(AIAction)
        .where(
            AIAction.business_id == business_id,
            AIAction.execution_id == execution_id,
        )
        .order_by(
            AIAction.proposal_index.asc(),
            AIAction.id.asc(),
        )
    )

    try:
        result = await session.scalars(
            statement
        )

        return list(
            result.all()
        )

    except SQLAlchemyError:
        raise AIActionPersistenceError(
            "Unable to inspect existing AI actions"
        ) from None


def _validate_existing_materialization(
    existing_actions: list[AIAction],
    proposals: list[AIAgentProposedAction],
) -> None:
    """
    Verify a repeated materialization request refers to the exact same set of
    immutable AI proposals.

    Mutable future fields such as policy decisions, execution status, payload,
    connector results, or external IDs are intentionally NOT compared.
    """
    if len(existing_actions) != len(
        proposals
    ):
        raise AIActionConflictError(
            "AI action materialization conflicts with existing actions"
        )

    for expected_index, (
        action,
        proposal,
    ) in enumerate(
        zip(
            existing_actions,
            proposals,
            strict=True,
        )
    ):
        if (
            action.proposal_index
            != expected_index
        ):
            raise AIActionConflictError(
                "AI action materialization conflicts with existing actions"
            )

        if (
            action.action_type
            != proposal.action_type
        ):
            raise AIActionConflictError(
                "AI action materialization conflicts with existing actions"
            )

        if (
            action.description
            != proposal.description
        ):
            raise AIActionConflictError(
                "AI action materialization conflicts with existing actions"
            )

        if (
            action.risk_level
            != proposal.risk_level
        ):
            raise AIActionConflictError(
                "AI action materialization conflicts with existing actions"
            )

        if (
            action.proposed_requires_approval
            != proposal.requires_approval
        ):
            raise AIActionConflictError(
                "AI action materialization conflicts with existing actions"
            )

        if action.action_payload != _normalized_action_payload(proposal):
            raise AIActionConflictError(
                "AI action materialization conflicts with existing actions"
            )


def _normalized_action_payload(
    proposal: AIAgentProposedAction,
) -> dict[str, object]:
    """
    Persist only a registry-validated, normalized payload.

    Unsupported or malformed candidate data becomes an empty safe object so
    later policy evaluation can block it without retaining arbitrary fields.
    """
    candidate = proposal.action_payload
    if candidate is not None:
        candidate = candidate.model_dump(mode="json")

    try:
        payload = ACTION_REGISTRY.validate_payload(
            proposal.action_type,
            candidate,
        )
    except AIActionError:
        return {}

    value = payload.model_dump(mode="json")
    return dict(value)
