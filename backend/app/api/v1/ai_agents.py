from __future__ import annotations

from time import perf_counter_ns
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.provider import (
    AIAgentProvider,
    get_agent_provider_model_name,
    validate_agent_provider,
)
from app.agents.runtime import (
    execute_ai_agent_with_metadata,
)
from app.api.dependencies.ai_agent import (
    AIAgentProviderDependency,
)
from app.api.dependencies.business import (
    BusinessAccessDependency,
)
from app.db.session import get_db_session
from app.exceptions.ai_agent import (
    AIAgentContextError,
    AIAgentError,
    AIAgentProviderError,
    AIAgentResponseError,
    AIAgentValidationError,
)
from app.exceptions.ai_agent_execution import (
    AIAgentExecutionLedgerError,
)
from app.exceptions.ai_action import (
    AIActionError,
)
from app.exceptions.approval import (
    ApprovalError,
)
from app.exceptions.ai_workforce import AIWorkforceError
from app.schemas.ai_agent import (
    AIAgentExecutionRequest,
    AIAgentExecutionResult,
)
from app.services.ai_agent_execution import (
    create_running_ai_agent_execution,
    fail_ai_agent_execution,
    finalize_successful_ai_agent_execution,
)
from app.services.ai_action import (
    materialize_ai_actions,
)
from app.services.action_governance import (
    govern_materialized_ai_actions,
)
from app.services.ai_capabilities import (
    ROLE_CAPABILITIES,
    validate_proposed_action_capabilities,
    validate_role_capabilities,
)
from app.services.ai_workforce import get_agent_config
from app.services.billing import require_capacity, require_feature


router = APIRouter(
    prefix="/businesses/{business_id}/agents",
    tags=["AI Agents"],
)

SessionDependency = Annotated[
    AsyncSession,
    Depends(get_db_session),
]


@router.post(
    "/execute",
    response_model=AIAgentExecutionResult,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Invalid AI agent execution request.",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "AI provider returned an invalid response.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "AI service is temporarily unavailable.",
        },
    },
)
async def execute_business_ai_agent(
    execution_request: AIAgentExecutionRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    provider: AIAgentProviderDependency,
) -> AIAgentExecutionResult:
    """
    Execute one authenticated, business-scoped AI employee task.

    Every accepted execution receives a durable audit-ledger record before
    the external model request begins.

    Security and audit properties:

    - business identity comes from authenticated tenant authorization
    - the client cannot override business identity or provider configuration
    - the running ledger entry is committed before the external AI call
    - trusted context remains server-assembled and tenant-scoped
    - provider credentials never enter ledger records or API responses
    - successful executions persist audit-safe provider request/token metadata
    - known runtime failures are recorded using safe failure codes
    - raw provider/database exception details are never persisted
    - proposed actions are materialized into durable AIAction records
    - server policy evaluates every materialized action before commit
    - approval-required actions receive durable ApprovalRequest records
    - proposed actions remain data only and are never executed here
    """
    try:
        validate_agent_provider(
            provider,
        )

        provider_name = (
            provider.provider_name.strip()
        )

        model_name = (
            get_agent_provider_model_name(
                provider
            )
        )

    except (TypeError, ValueError):
        raise _ai_service_unavailable_exception() from None

    started_ns = perf_counter_ns()

    configured_agent = None
    if isinstance(session, AsyncSession):
        try:
            configured_agent = await get_agent_config(
                session, business_id=access.business.id, role=execution_request.role
            )
            if not configured_agent.enabled:
                await session.commit()
                raise _agent_disabled_exception()
            await session.commit()
        except HTTPException:
            raise
        except (AIWorkforceError, SQLAlchemyError):
            await _rollback_safely(session)
            raise _ai_service_unavailable_exception() from None

        # The quota locks remain held until the running ledger row is durable,
        # preventing concurrent requests from all passing the same boundary.
        await require_feature(session, business_id=access.business.id, key="ai_agents")
        await require_capacity(session, business_id=access.business.id, key="max_ai_executions_month")
        await require_capacity(session, business_id=access.business.id, key="max_ai_input_tokens_month")
        await require_capacity(session, business_id=access.business.id, key="max_ai_output_tokens_month")

    try:
        execution = (
            await create_running_ai_agent_execution(
                session,
                business_id=access.business.id,
                requested_by_user_id=access.user.id,
                role=execution_request.role,
                task=execution_request.task,
                provider_name=provider_name,
                model_name=model_name,
                trigger_type="api",
            )
        )

        execution_id = execution.id

        # Make the running audit record durable before contacting an
        # external AI provider.
        await session.commit()

    except (
        AIAgentExecutionLedgerError,
        SQLAlchemyError,
    ):
        await _rollback_safely(
            session
        )

        raise _ai_service_unavailable_exception() from None

    try:
        runtime_result = (
            await execute_ai_agent_with_metadata(
                session,
                access.business.id,
                execution_request,
                provider,
                **(
                    {
                        "custom_instructions": configured_agent.custom_instructions,
                        "allowed_capabilities": validate_role_capabilities(
                            execution_request.role,
                            configured_agent.capability_config,
                        ),
                    }
                    if configured_agent is not None
                    else {}
                ),
            )
        )

        result = (
            runtime_result.execution_result
        )

        provider_metadata = (
            runtime_result.provider_metadata
        )

        try:
            allowed_capabilities = (
                validate_role_capabilities(
                    execution_request.role,
                    configured_agent.capability_config,
                )
                if configured_agent is not None
                else tuple(sorted(ROLE_CAPABILITIES[execution_request.role]))
            )
            validate_proposed_action_capabilities(
                execution_request.role,
                allowed_capabilities,
                [item.action_type for item in result.output.proposed_actions],
            )
        except ValueError:
            raise AIAgentValidationError("Agent capability violation") from None

    except AIAgentValidationError:
        await _persist_failed_execution(
            session,
            business_id=access.business.id,
            execution_id=execution_id,
            failure_code="agent_validation_error",
            duration_ms=_elapsed_milliseconds(
                started_ns
            ),
        )

        raise _invalid_agent_request_exception() from None

    except AIAgentContextError:
        await _persist_failed_execution(
            session,
            business_id=access.business.id,
            execution_id=execution_id,
            failure_code="context_unavailable",
            duration_ms=_elapsed_milliseconds(
                started_ns
            ),
        )

        raise _ai_service_unavailable_exception() from None

    except AIAgentProviderError:
        await _persist_failed_execution(
            session,
            business_id=access.business.id,
            execution_id=execution_id,
            failure_code="provider_unavailable",
            duration_ms=_elapsed_milliseconds(
                started_ns
            ),
        )

        raise _ai_service_unavailable_exception() from None

    except AIAgentResponseError:
        await _persist_failed_execution(
            session,
            business_id=access.business.id,
            execution_id=execution_id,
            failure_code="invalid_provider_response",
            duration_ms=_elapsed_milliseconds(
                started_ns
            ),
        )

        raise _invalid_ai_response_exception() from None

    except AIAgentError:
        await _persist_failed_execution(
            session,
            business_id=access.business.id,
            execution_id=execution_id,
            failure_code="agent_runtime_error",
            duration_ms=_elapsed_milliseconds(
                started_ns
            ),
        )

        raise _ai_service_unavailable_exception() from None

    duration_ms = _elapsed_milliseconds(
        started_ns
    )

    try:
        await finalize_successful_ai_agent_execution(
            session,
            business_id=access.business.id,
            execution_id=execution_id,
            result=result,
            duration_ms=duration_ms,
            input_tokens=provider_metadata.input_tokens,
            output_tokens=provider_metadata.output_tokens,
            provider_request_id=(
                provider_metadata.provider_request_id
            ),
        )

        # Materialize the validated AI proposals before the terminal execution
        # becomes durable. No external side effect is performed here.
        try:
            actions = await materialize_ai_actions(
                session,
                business_id=access.business.id,
                execution_id=execution_id,
            )

        except AIActionError:
            # Rollback restores the previously committed running ledger row
            # before recording a safe terminal failure.
            await _persist_failed_execution(
                session,
                business_id=access.business.id,
                execution_id=execution_id,
                failure_code="action_materialization_failed",
                duration_ms=duration_ms,
            )

            raise _ai_service_unavailable_exception() from None

        # Apply deterministic server policy to every durable proposal and
        # create tenant-scoped ApprovalRequest rows where required.
        #
        # Governance is part of the same terminal transaction as execution
        # finalization and action materialization. It still executes nothing.
        try:
            await govern_materialized_ai_actions(
                session,
                business_id=access.business.id,
                actions=actions,
                requested_by_user_id=access.user.id,
            )

        except (
            AIActionError,
            ApprovalError,
        ):
            # A terminal execution must never be committed with incomplete
            # action governance. Roll back all terminal/action/approval changes
            # and safely mark the previously durable running ledger as failed.
            await _persist_failed_execution(
                session,
                business_id=access.business.id,
                execution_id=execution_id,
                failure_code="action_governance_failed",
                duration_ms=duration_ms,
            )

            raise _ai_service_unavailable_exception() from None

        # Execution finalization, AIAction rows, policy decisions, and any
        # ApprovalRequest rows become durable together.
        await session.commit()

    except (
        AIAgentExecutionLedgerError,
        SQLAlchemyError,
    ):
        # The provider may already have returned successfully, but the request
        # is not considered safely complete unless its ledger, governed
        # actions, policy decisions, and approvals reach one durable terminal
        # transaction.
        await _persist_failed_execution(
            session,
            business_id=access.business.id,
            execution_id=execution_id,
            failure_code="ledger_finalize_failed",
            duration_ms=duration_ms,
        )

        raise _ai_service_unavailable_exception() from None

    _set_private_response_headers(
        response,
    )

    return result


async def _persist_failed_execution(
    session: AsyncSession,
    *,
    business_id: UUID,
    execution_id: UUID,
    failure_code: str,
    duration_ms: int,
) -> None:
    """
    Persist a safe terminal failure after rolling back any transaction opened
    during context assembly or runtime processing.

    Raw provider/database error content must never be passed here.

    Failure persistence itself is mandatory for known runtime failures.
    If the audit ledger cannot reach its terminal failure state, the request
    fails closed with a generic 503 response.
    """
    await _rollback_safely(
        session
    )

    try:
        await fail_ai_agent_execution(
            session,
            business_id=business_id,
            execution_id=execution_id,
            failure_code=failure_code,
            duration_ms=duration_ms,
        )

        await session.commit()

    except (
        AIAgentExecutionLedgerError,
        SQLAlchemyError,
    ):
        await _rollback_safely(
            session
        )

        raise _ai_service_unavailable_exception() from None


async def _rollback_safely(
    session: AsyncSession,
) -> None:
    try:
        await session.rollback()

    except SQLAlchemyError:
        return


def _elapsed_milliseconds(
    started_ns: int,
) -> int:
    elapsed_ns = max(
        0,
        perf_counter_ns() - started_ns,
    )

    return elapsed_ns // 1_000_000


def _set_private_response_headers(
    response: Response,
) -> None:
    for name, value in _PRIVATE_RESPONSE_HEADERS.items():
        response.headers[name] = value


def _invalid_agent_request_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Invalid AI agent execution request.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


def _invalid_ai_response_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="AI service returned an invalid response.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


def _ai_service_unavailable_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="AI service is temporarily unavailable.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


def _agent_disabled_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="AI agent is disabled for this business.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


_PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}
