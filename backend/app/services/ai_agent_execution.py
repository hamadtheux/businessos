from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.ai_agent_execution import (
    AIAgentExecutionNotFoundError,
    AIAgentExecutionPersistenceError,
    AIAgentExecutionStateError,
    AIAgentExecutionValidationError,
)
from app.models.ai_agent_execution import AIAgentExecution
from app.models.opportunity import Opportunity
from app.schemas.ai_agent import (
    AIAgentExecutionResult,
    AIAgentRole,
)
from app.services.automation_events import record_automation_event


AIAgentExecutionTrigger = Literal[
    "api",
    "automation",
    "command",
    "website_widget",
    "system",
]

AIAgentTerminalStatus = Literal[
    "completed",
    "needs_approval",
    "blocked",
]


async def create_running_ai_agent_execution(
    session: AsyncSession,
    *,
    business_id: UUID,
    requested_by_user_id: UUID | None,
    role: AIAgentRole,
    task: str,
    provider_name: str,
    model_name: str,
    trigger_type: AIAgentExecutionTrigger = "api",
    command_id: UUID | None = None,
    opportunity_id: UUID | None = None,
    parent_execution_id: UUID | None = None,
    delegation_role: AIAgentRole | None = None,
    delegation_sequence: int = 0,
    delegation_depth: int = 0,
) -> AIAgentExecution:
    """
    Create one running execution-ledger record.

    Context metadata is intentionally empty at this stage because trusted
    context may not have been assembled yet.

    This function performs no commit.
    """
    normalized_task = _normalize_required_text(
        task,
        field_name="task",
        max_length=4_000,
    )

    normalized_provider = _normalize_required_text(
        provider_name,
        field_name="provider_name",
        max_length=64,
    )

    normalized_model = _normalize_required_text(
        model_name,
        field_name="model_name",
        max_length=128,
    )

    if not 0 <= delegation_sequence <= 3 or not 0 <= delegation_depth <= 1:
        raise AIAgentExecutionValidationError("Invalid AI delegation metadata")
    if delegation_depth == 0 and parent_execution_id is not None:
        raise AIAgentExecutionValidationError("Root execution cannot have a parent")
    if delegation_depth > 0 and (parent_execution_id is None or delegation_role is None):
        raise AIAgentExecutionValidationError("Delegated execution requires linkage")

    if opportunity_id is not None:
        try:
            owned_opportunity_id = await session.scalar(
                select(Opportunity.id).where(
                    Opportunity.id == opportunity_id,
                    Opportunity.business_id == business_id,
                )
            )
        except SQLAlchemyError:
            raise AIAgentExecutionPersistenceError(
                "Unable to validate AI execution opportunity"
            ) from None

        if owned_opportunity_id is None:
            raise AIAgentExecutionValidationError(
                "AI execution opportunity is invalid"
            )

    execution = AIAgentExecution(
        business_id=business_id,
        requested_by_user_id=requested_by_user_id,
        command_id=command_id,
        opportunity_id=opportunity_id,
        parent_execution_id=parent_execution_id,
        delegation_role=delegation_role,
        delegation_sequence=delegation_sequence,
        delegation_depth=delegation_depth,
        role=role,
        trigger_type=trigger_type,
        status="running",
        task=normalized_task,
        provider_name=normalized_provider,
        model_name=normalized_model,
        context_revision=None,
        context_source_count=0,
        business_brain_source_count=0,
        memory_source_count=0,
        output_summary=None,
        recommendations=[],
        proposed_actions=[],
        failure_code=None,
        provider_request_id=None,
        duration_ms=None,
        input_tokens=None,
        output_tokens=None,
        estimated_cost_usd=None,
        completed_at=None,
    )

    try:
        session.add(
            execution
        )

        await session.flush()

    except SQLAlchemyError:
        raise AIAgentExecutionPersistenceError(
            "Unable to create AI agent execution record"
        ) from None

    return execution


async def get_ai_agent_execution(
    session: AsyncSession,
    *,
    business_id: UUID,
    execution_id: UUID,
) -> AIAgentExecution:
    """
    Return one execution owned by the authorized business.
    """
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
        raise AIAgentExecutionPersistenceError(
            "Unable to read AI agent execution record"
        ) from None

    if execution is None:
        raise AIAgentExecutionNotFoundError(
            "AI agent execution not found"
        )

    if not isinstance(
        execution,
        AIAgentExecution,
    ):
        raise AIAgentExecutionPersistenceError(
            "Unable to read AI agent execution record"
        )

    if execution.business_id != business_id:
        raise AIAgentExecutionNotFoundError(
            "AI agent execution not found"
        )

    return execution


async def finalize_successful_ai_agent_execution(
    session: AsyncSession,
    *,
    business_id: UUID,
    execution_id: UUID,
    result: AIAgentExecutionResult,
    duration_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    estimated_cost_usd: Decimal | None = None,
    provider_request_id: str | None = None,
) -> AIAgentExecution:
    """
    Finalize a running execution using one validated agent runtime result.

    Proposed actions are stored only as structured proposals. This function
    never executes them.
    """
    execution = await get_ai_agent_execution(
        session,
        business_id=business_id,
        execution_id=execution_id,
    )

    _require_running(
        execution
    )

    if result.business_id != business_id:
        raise AIAgentExecutionValidationError(
            "AI agent result belongs to a different business"
        )

    if result.role != execution.role:
        raise AIAgentExecutionValidationError(
            "AI agent result role does not match execution"
        )

    terminal_status = result.output.status

    if terminal_status not in {
        "completed",
        "needs_approval",
        "blocked",
    }:
        raise AIAgentExecutionValidationError(
            "AI agent result has an unsupported terminal status"
        )

    normalized_duration = _validate_optional_non_negative_int(
        duration_ms,
        field_name="duration_ms",
    )

    normalized_input_tokens = _validate_optional_non_negative_int(
        input_tokens,
        field_name="input_tokens",
    )

    normalized_output_tokens = _validate_optional_non_negative_int(
        output_tokens,
        field_name="output_tokens",
    )

    normalized_cost = _validate_optional_non_negative_decimal(
        estimated_cost_usd,
        field_name="estimated_cost_usd",
    )

    normalized_request_id = _normalize_optional_text(
        provider_request_id,
        field_name="provider_request_id",
        max_length=255,
    )

    execution.status = terminal_status
    execution.context_revision = result.context_revision
    execution.context_source_count = result.context_source_count
    execution.business_brain_source_count = (
        result.business_brain_source_count
    )
    execution.memory_source_count = result.memory_source_count

    execution.output_summary = result.output.summary

    execution.recommendations = list(
        result.output.recommendations
    )

    execution.proposed_actions = [
        action.model_dump(
            mode="json"
        )
        for action in result.output.proposed_actions
    ]

    execution.failure_code = None
    execution.provider_request_id = normalized_request_id
    execution.duration_ms = normalized_duration
    execution.input_tokens = normalized_input_tokens
    execution.output_tokens = normalized_output_tokens
    execution.estimated_cost_usd = normalized_cost
    execution.completed_at = datetime.now(
        UTC
    )

    try:
        await session.flush()

    except SQLAlchemyError:
        raise AIAgentExecutionPersistenceError(
            "Unable to finalize AI agent execution record"
        ) from None

    record_automation_event(
        session, business_id=business_id, event_type="ai_execution_completed",
        entity_type="ai_execution", entity_id=execution.id,
        payload={"status": execution.status},
    )
    return execution


async def fail_ai_agent_execution(
    session: AsyncSession,
    *,
    business_id: UUID,
    execution_id: UUID,
    failure_code: str,
    duration_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    estimated_cost_usd: Decimal | None = None,
    provider_request_id: str | None = None,
) -> AIAgentExecution:
    """
    Mark one running execution as failed using safe internal metadata only.

    Raw provider/database exception messages must never be passed as
    failure_code.
    """
    execution = await get_ai_agent_execution(
        session,
        business_id=business_id,
        execution_id=execution_id,
    )

    _require_running(
        execution
    )

    normalized_failure_code = _normalize_required_text(
        failure_code,
        field_name="failure_code",
        max_length=64,
    )

    normalized_duration = _validate_optional_non_negative_int(
        duration_ms,
        field_name="duration_ms",
    )

    normalized_input_tokens = _validate_optional_non_negative_int(
        input_tokens,
        field_name="input_tokens",
    )

    normalized_output_tokens = _validate_optional_non_negative_int(
        output_tokens,
        field_name="output_tokens",
    )

    normalized_cost = _validate_optional_non_negative_decimal(
        estimated_cost_usd,
        field_name="estimated_cost_usd",
    )

    normalized_request_id = _normalize_optional_text(
        provider_request_id,
        field_name="provider_request_id",
        max_length=255,
    )

    execution.status = "failed"

    execution.output_summary = None
    execution.recommendations = []
    execution.proposed_actions = []

    execution.failure_code = normalized_failure_code
    execution.provider_request_id = normalized_request_id
    execution.duration_ms = normalized_duration
    execution.input_tokens = normalized_input_tokens
    execution.output_tokens = normalized_output_tokens
    execution.estimated_cost_usd = normalized_cost
    execution.completed_at = datetime.now(
        UTC
    )

    try:
        await session.flush()

    except SQLAlchemyError:
        raise AIAgentExecutionPersistenceError(
            "Unable to fail AI agent execution record"
        ) from None

    return execution


def _require_running(
    execution: AIAgentExecution,
) -> None:
    if execution.status != "running":
        raise AIAgentExecutionStateError(
            "AI agent execution is already terminal"
        )

    if execution.completed_at is not None:
        raise AIAgentExecutionStateError(
            "Running AI agent execution cannot already be completed"
        )


def _normalize_required_text(
    value: str,
    *,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise AIAgentExecutionValidationError(
            f"{field_name} must be a string"
        )

    normalized = value.strip()

    if not normalized:
        raise AIAgentExecutionValidationError(
            f"{field_name} cannot be blank"
        )

    if len(normalized) > max_length:
        raise AIAgentExecutionValidationError(
            f"{field_name} exceeds maximum length"
        )

    return normalized


def _normalize_optional_text(
    value: str | None,
    *,
    field_name: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None

    return _normalize_required_text(
        value,
        field_name=field_name,
        max_length=max_length,
    )


def _validate_optional_non_negative_int(
    value: int | None,
    *,
    field_name: str,
) -> int | None:
    if value is None:
        return None

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise AIAgentExecutionValidationError(
            f"{field_name} must be a non-negative integer"
        )

    return value


def _validate_optional_non_negative_decimal(
    value: Decimal | None,
    *,
    field_name: str,
) -> Decimal | None:
    if value is None:
        return None

    if not isinstance(
        value,
        Decimal,
    ):
        raise AIAgentExecutionValidationError(
            f"{field_name} must be a Decimal"
        )

    if not value.is_finite():
        raise AIAgentExecutionValidationError(
            f"{field_name} must be finite"
        )

    if value < 0:
        raise AIAgentExecutionValidationError(
            f"{field_name} cannot be negative"
        )

    return value
