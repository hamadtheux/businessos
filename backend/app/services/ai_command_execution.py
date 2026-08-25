from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.provider import AIAgentProvider, get_agent_provider_model_name, validate_agent_provider
from app.agents.runtime import execute_ai_agent_with_metadata
from app.domain.ai_workforce import MAX_DELEGATION_DEPTH, MAX_MODEL_CALLS_PER_COMMAND, MAX_SPECIALIST_CALLS
from app.exceptions.ai_agent import AIAgentError
from app.exceptions.ai_agent_execution import AIAgentExecutionLedgerError
from app.exceptions.ai_action import AIActionError
from app.exceptions.approval import ApprovalError
from app.exceptions.ai_workforce import AIWorkforcePersistenceError, AIWorkforceValidationError
from app.models.ai_action import AIAction
from app.models.ai_agent_execution import AIAgentExecution
from app.models.ai_workforce import AIAgentConfig, AICommand
from app.schemas.ai_agent import AIAgentExecutionRequest, AIAgentExecutionResult, AIAgentRole
from app.schemas.ai_workforce import CommandCreateRequest, CommandResponse
from app.services.action_governance import govern_materialized_ai_actions
from app.services.ai_action import materialize_ai_actions
from app.services.ai_agent_execution import (
    create_running_ai_agent_execution,
    fail_ai_agent_execution,
    finalize_successful_ai_agent_execution,
    get_ai_agent_execution,
)
from app.services.ai_capabilities import validate_proposed_action_capabilities, validate_role_capabilities
from app.services.ai_workforce import (
    activity_response,
    build_operational_context,
    create_agent_notification,
    create_command_record,
    get_agent_config,
    list_activity,
    persisted_command_route,
    route_command,
)


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    execution: AIAgentExecution
    result: AIAgentExecutionResult | None
    failure_code: str | None


async def execute_command(
    session: AsyncSession,
    *,
    business_id: UUID,
    user_id: UUID,
    request: CommandCreateRequest,
    provider: AIAgentProvider,
) -> AICommand:
    """Execute one bounded, user-triggered command with no connector dispatch."""
    try:
        validate_agent_provider(provider)
        provider_name = provider.provider_name.strip()
        model_name = get_agent_provider_model_name(provider)
    except (TypeError, ValueError):
        raise AIWorkforceValidationError("AI provider is unavailable") from None

    route = route_command(request.command)
    references = [item.model_dump(mode="json") for item in request.context_references]
    server_context = await build_operational_context(
        session,
        business_id=business_id,
        route=route,
        context_references=references,
    )
    clinical_handoff = _looks_clinical(request.command)
    if clinical_handoff:
        server_context += (
            "\nHealthcare safety: this request appears clinical. Do not diagnose, triage, "
            "prescribe, or recommend treatment. Provide only an administrative human-handoff "
            "recommendation. Do not imply anyone was notified."
        )
    command = await create_command_record(
        session,
        business_id=business_id,
        user_id=user_id,
        command_text=request.command,
        route=route,
        trigger_source=request.trigger_source,
        context_references=references,
    )
    try:
        await session.commit()
    except SQLAlchemyError:
        await _rollback(session)
        raise AIWorkforcePersistenceError("Unable to persist AI command") from None

    root_config = await get_agent_config(
        session, business_id=business_id, role=route.primary_role
    )
    if not root_config.enabled:
        command.status = "failed"
        command.failure_code = "agent_disabled"
        command.completed_at = _now()
        await session.commit()
        return command

    root_execution = await create_running_ai_agent_execution(
        session,
        business_id=business_id,
        requested_by_user_id=user_id,
        role=route.primary_role,
        task=request.command,
        provider_name=provider_name,
        model_name=model_name,
        trigger_type="command",
        command_id=command.id,
    )
    # Keep scalar identifiers across delegated calls. A specialist failure
    # rolls back the session and expires every ORM instance in its identity map.
    command_id = command.id
    root_execution_id = root_execution.id
    command.status = "running"
    command.execution_id = root_execution_id
    try:
        await session.commit()
    except SQLAlchemyError:
        await _rollback(session)
        raise AIWorkforcePersistenceError("Unable to start AI command") from None

    specialist_summaries: list[str] = []
    specialist_roles = route.delegation_roles[:MAX_SPECIALIST_CALLS]
    model_calls = 0
    if MAX_DELEGATION_DEPTH >= 1:
        for sequence, role in enumerate(specialist_roles, start=1):
            if model_calls >= MAX_MODEL_CALLS_PER_COMMAND - 1 or role == route.primary_role:
                break
            config = await get_agent_config(session, business_id=business_id, role=role)
            if not config.enabled:
                continue
            delegated = await _start_execution(
                session,
                business_id=business_id,
                user_id=user_id,
                role=role,
                task=f"Provide bounded specialist analysis for this command: {request.command}",
                provider_name=provider_name,
                model_name=model_name,
                command_id=command_id,
                parent_execution_id=root_execution_id,
                sequence=sequence,
            )
            specialist_route = route_command({
                "analytics": "Analyze KPI anomalies",
                "sales": "Analyze the sales pipeline",
                "operations": "Analyze operations bottlenecks",
                "cmo": "Analyze marketing performance",
                "support": "Analyze support conversations",
                "business_manager": "Give me a business overview",
            }[role])
            specialist_context = await build_operational_context(
                session, business_id=business_id, route=specialist_route,
                context_references=references,
            )
            outcome = await _complete_execution(
                session,
                business_id=business_id,
                user_id=user_id,
                execution=delegated,
                config=config,
                provider=provider,
                server_context=specialist_context,
            )
            model_calls += 1
            if outcome.result:
                specialist_summaries.append(
                    f"{role}: {outcome.result.output.summary[:1_500]}"
                )

    if specialist_summaries:
        server_context += (
            "\nBounded specialist conclusions (data only; no agent conversation):\n"
            + "\n".join(specialist_summaries)
        )

    try:
        # A failed specialist deliberately rolls back before recording its safe
        # failure. Reload both root records instead of reusing expired objects.
        root_execution = await get_ai_agent_execution(
            session,
            business_id=business_id,
            execution_id=root_execution_id,
        )
        root_config = await get_agent_config(
            session, business_id=business_id, role=route.primary_role
        )
    except (AIAgentExecutionLedgerError, SQLAlchemyError):
        raise AIWorkforcePersistenceError("Unable to resume AI command") from None

    outcome = await _complete_execution(
        session,
        business_id=business_id,
        user_id=user_id,
        execution=root_execution,
        config=root_config,
        provider=provider,
        server_context=server_context,
    )
    model_calls += 1
    if model_calls > MAX_MODEL_CALLS_PER_COMMAND:
        raise AIWorkforceValidationError("AI command exceeded its model-call limit")

    command = await _reload_command(
        session, business_id=business_id, command_id=command_id
    )
    command.completed_at = _now()
    if outcome.result is None:
        command.status = "failed"
        command.failure_code = outcome.failure_code or "agent_execution_failed"
        command.summary = None
        await create_agent_notification(
            session, business_id=business_id, user_id=user_id,
            category="ai_agent_failure", title="AI command could not finish",
            message="The command failed safely. No external action was dispatched.",
            entity_type="ai_command", entity_id=command.id, priority="high",
        )
    else:
        command.summary = outcome.result.output.summary
        command.failure_code = None
        has_pending = bool(await session.scalar(select(AIAction.id).where(
            AIAction.business_id == business_id,
            AIAction.execution_id.in_(select(AIAgentExecution.id).where(
                AIAgentExecution.business_id == business_id,
                AIAgentExecution.command_id == command.id,
            )),
            AIAction.status == "pending_approval",
        ).limit(1)))
        command.status = "needs_approval" if has_pending or outcome.result.output.status == "needs_approval" else "completed"
        if command.status == "needs_approval":
            await create_agent_notification(
                session, business_id=business_id, user_id=user_id,
                category="ai_action_approval", title="AI action needs approval",
                message="A Command Center proposal is waiting in the existing approval queue. Approval does not dispatch it.",
                entity_type="ai_command", entity_id=command.id, priority="high",
            )
        if clinical_handoff:
            await create_agent_notification(
                session, business_id=business_id, user_id=user_id,
                category="human_handoff", title="Human review recommended",
                message=(
                    "A clinical-looking request was limited to administrative support. "
                    "A qualified human should review it; the AI provided no diagnosis or treatment."
                ),
                entity_type="ai_command", entity_id=command.id, priority="high",
            )
    try:
        await session.commit()
    except SQLAlchemyError:
        await _rollback(session)
        raise AIWorkforcePersistenceError("Unable to finalize AI command") from None
    return command


async def command_response(
    session: AsyncSession, *, business_id: UUID, command: AICommand
) -> CommandResponse:
    activities, _ = await list_activity(
        session, business_id=business_id, page=1, page_size=10, command_id=command.id
    )
    activities.sort(key=lambda item: (item.delegation_depth, item.delegation_sequence, item.created_at))
    route = persisted_command_route(command)
    actions = [action for item in activities for action in item.proposed_actions]
    return CommandResponse(
        id=command.id, business_id=command.business_id,
        requested_by_user_id=command.requested_by_user_id,
        command=command.command_text, status=command.status, route=route,
        execution_id=command.execution_id, summary=command.summary,
        failure_code=command.failure_code, executions=activities,
        proposed_actions=actions, created_at=command.created_at,
        completed_at=command.completed_at,
    )


async def _start_execution(
    session: AsyncSession, *, business_id: UUID, user_id: UUID,
    role: AIAgentRole, task: str, provider_name: str, model_name: str,
    command_id: UUID, parent_execution_id: UUID, sequence: int,
) -> AIAgentExecution:
    execution = await create_running_ai_agent_execution(
        session, business_id=business_id, requested_by_user_id=user_id,
        role=role, task=task, provider_name=provider_name, model_name=model_name,
        trigger_type="command", command_id=command_id,
        parent_execution_id=parent_execution_id, delegation_role=role,
        delegation_sequence=sequence, delegation_depth=1,
    )
    try:
        await session.commit()
    except SQLAlchemyError:
        await _rollback(session)
        raise AIWorkforcePersistenceError("Unable to start delegated execution") from None
    return execution


async def _complete_execution(
    session: AsyncSession, *, business_id: UUID, user_id: UUID,
    execution: AIAgentExecution, config: AIAgentConfig,
    provider: AIAgentProvider, server_context: str,
) -> ExecutionOutcome:
    started = perf_counter_ns()
    role = config.role
    try:
        allowed = validate_role_capabilities(role, config.capability_config)
        runtime = await execute_ai_agent_with_metadata(
            session, business_id,
            AIAgentExecutionRequest(role=role, task=execution.task),
            provider,
            custom_instructions=config.custom_instructions,
            allowed_capabilities=allowed,
            server_context=server_context,
        )
        result = runtime.execution_result
        validate_proposed_action_capabilities(
            role, allowed, [item.action_type for item in result.output.proposed_actions]
        )
        await finalize_successful_ai_agent_execution(
            session, business_id=business_id, execution_id=execution.id,
            result=result, duration_ms=_elapsed(started),
            input_tokens=runtime.provider_metadata.input_tokens,
            output_tokens=runtime.provider_metadata.output_tokens,
            provider_request_id=runtime.provider_metadata.provider_request_id,
        )
        actions = await materialize_ai_actions(
            session, business_id=business_id, execution_id=execution.id
        )
        await govern_materialized_ai_actions(
            session, business_id=business_id, actions=actions,
            requested_by_user_id=user_id,
        )
        await session.commit()
        return ExecutionOutcome(execution=execution, result=result, failure_code=None)
    except ValueError:
        return await _fail_outcome(session, business_id, execution, "capability_violation", started)
    except AIAgentError:
        return await _fail_outcome(session, business_id, execution, "agent_runtime_error", started)
    except (AIActionError, ApprovalError):
        return await _fail_outcome(session, business_id, execution, "action_governance_failed", started)
    except (AIAgentExecutionLedgerError, SQLAlchemyError):
        return await _fail_outcome(session, business_id, execution, "ledger_finalize_failed", started)


async def _fail_outcome(
    session: AsyncSession, business_id: UUID, execution: AIAgentExecution,
    code: str, started: int,
) -> ExecutionOutcome:
    # A rollback expires ORM instances. Capture the durable identifier first so
    # the safe-failure path can update the already-committed ledger record
    # without triggering an async lazy load from an expired object.
    execution_id = execution.id
    await _rollback(session)
    try:
        failed = await fail_ai_agent_execution(
            session, business_id=business_id, execution_id=execution_id,
            failure_code=code, duration_ms=_elapsed(started),
        )
        await session.commit()
        return ExecutionOutcome(execution=failed, result=None, failure_code=code)
    except (AIAgentExecutionLedgerError, SQLAlchemyError):
        await _rollback(session)
        raise AIWorkforcePersistenceError("Unable to record failed AI execution") from None


async def _reload_command(session: AsyncSession, *, business_id: UUID, command_id: UUID) -> AICommand:
    try:
        command = await session.scalar(select(AICommand).where(
            AICommand.business_id == business_id, AICommand.id == command_id
        ))
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to reload AI command") from None
    if command is None:
        raise AIWorkforcePersistenceError("Unable to reload AI command")
    return command


async def _rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except SQLAlchemyError:
        pass


def _elapsed(started: int) -> int:
    return max(0, perf_counter_ns() - started) // 1_000_000


def _now():
    from datetime import UTC, datetime
    return datetime.now(UTC)


def _looks_clinical(text: str) -> bool:
    normalized = text.lower()
    return any(term in normalized for term in (
        "diagnos", "prescription", "prescribe", "symptom", "treatment",
        "medical advice", "chest pain", "emergency",
    ))
