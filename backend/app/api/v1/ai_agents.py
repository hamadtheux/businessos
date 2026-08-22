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
    - proposed actions are materialized into governed AIAction records
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
            )
        )

        result = (
            runtime_result.execution_result
        )

        provider_metadata = (
            runtime_result.provider_metadata
        )

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

        # Materialize every validated proposal as a durable governed action
        # before the terminal execution is committed.
        #
        # This still performs no external side effect. The future Policy /
        # Approval / Action Execution layers remain responsible for deciding
        # whether an action may ever execute.
        await materialize_ai_actions(
            session,
            business_id=access.business.id,
            execution_id=execution_id,
        )

        # The terminal ledger state and its governed action records become
        # durable atomically.
        await session.commit()

    except AIActionError:
        # Rollback restores the previously committed running ledger row before
        # recording a safe terminal failure. This prevents a completed agent
        # execution from existing without its governed action records.
        await _persist_failed_execution(
            session,
            business_id=access.business.id,
            execution_id=execution_id,
            failure_code="action_materialization_failed",
            duration_ms=duration_ms,
        )

        raise _ai_service_unavailable_exception() from None

    except (
        AIAgentExecutionLedgerError,
        SQLAlchemyError,
    ):
        # The provider may already have returned successfully, but the request
        # is not considered safely complete unless its ledger and governed
        # action records reach one durable terminal transaction.
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


_PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}