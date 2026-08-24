from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.ai_agent import AIAgentProviderDependency
from app.api.dependencies.business import BusinessAccessDependency
from app.api.response_materialization import materialize_response_before_commit
from app.db.session import get_db_session
from app.domain.ai_workforce import CANONICAL_AGENT_ROLES
from app.exceptions.ai_workforce import (
    AIWorkforceConflictError,
    AIWorkforceNotFoundError,
    AIWorkforcePersistenceError,
    AIWorkforceValidationError,
)
from app.models.ai_workforce import AICommand
from app.schemas.ai_agent import AIAgentRole
from app.schemas.ai_workforce import (
    AgentActivityPage,
    AgentActivityResponse,
    AgentConfigResponse,
    AgentConfigUpdate,
    CapabilityResponse,
    CommandCreateRequest,
    CommandPage,
    CommandResponse,
    DailyBriefResponse,
    SuggestedCommandResponse,
)
from app.services.ai_capabilities import AI_CAPABILITY_REGISTRY
from app.services.ai_command_execution import command_response, execute_command
from app.services.ai_workforce import (
    agent_metrics_by_role,
    cancel_command,
    config_response,
    daily_brief,
    ensure_agent_configs,
    get_activity,
    get_agent_config,
    get_command,
    list_activity,
    list_commands,
    persisted_command_route,
    reset_agent_config,
    suggested_commands,
    update_agent_config,
)
from app.services.billing import require_capacity, require_feature


router = APIRouter(prefix="/businesses/{business_id}", tags=["AI Workforce"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
Page = Annotated[int, Query(ge=1, le=1_000_000)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("/agents/capabilities", response_model=list[CapabilityResponse])
async def read_capability_registry(
    access: BusinessAccessDependency, response: Response
) -> list[CapabilityResponse]:
    _set_private(response)
    return [
        CapabilityResponse(key=item.key, category=item.category, description=item.description)
        for item in AI_CAPABILITY_REGISTRY.values()
    ]


@router.get("/agents/activity", response_model=AgentActivityPage)
async def read_agent_activity(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    page: Page = 1,
    page_size: PageSize = 25,
    role: AIAgentRole | None = None,
    status_filter: Annotated[
        Literal["running", "completed", "needs_approval", "blocked", "failed"] | None,
        Query(alias="status"),
    ] = None,
) -> AgentActivityPage:
    try:
        items, total = await list_activity(
            session, business_id=access.business.id, page=page, page_size=page_size,
            role=role, status=status_filter,
        )
    except AIWorkforcePersistenceError:
        raise _unavailable() from None
    _set_private(response)
    return AgentActivityPage(items=items, page=page, page_size=page_size, total=total)


@router.get("/agents/activity/{execution_id}", response_model=AgentActivityResponse)
async def read_agent_activity_detail(
    execution_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> AgentActivityResponse:
    try:
        value = await get_activity(
            session, business_id=access.business.id, execution_id=execution_id
        )
    except AIWorkforceNotFoundError:
        raise _not_found() from None
    except AIWorkforcePersistenceError:
        raise _unavailable() from None
    _set_private(response)
    return value


@router.get("/agents", response_model=list[AgentConfigResponse])
async def read_agent_configs(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> list[AgentConfigResponse]:
    try:
        configs = await ensure_agent_configs(session, business_id=access.business.id)
        metrics, last_activity = await agent_metrics_by_role(
            session, business_id=access.business.id
        )
        await session.commit()
    except (AIWorkforcePersistenceError, SQLAlchemyError):
        await _rollback(session)
        raise _unavailable() from None
    _set_private(response)
    return [
        config_response(
            item, metrics=metrics[item.role], last_activity_at=last_activity.get(item.role)
        )
        for item in configs
    ]


@router.get("/agents/{role}", response_model=AgentConfigResponse)
async def read_agent_config(
    role: AIAgentRole,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> AgentConfigResponse:
    try:
        config = await get_agent_config(session, business_id=access.business.id, role=role)
        metrics, last_activity = await agent_metrics_by_role(session, business_id=access.business.id)
        await session.commit()
    except (AIWorkforcePersistenceError, SQLAlchemyError):
        await _rollback(session)
        raise _unavailable() from None
    _set_private(response)
    return config_response(config, metrics=metrics[role], last_activity_at=last_activity.get(role))


@router.patch("/agents/{role}", response_model=AgentConfigResponse)
async def patch_agent_config(
    role: AIAgentRole,
    data: AgentConfigUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> AgentConfigResponse:
    try:
        config = await update_agent_config(
            session, business_id=access.business.id, role=role,
            actor_user_id=access.user.id, display_name=data.display_name,
            enabled=data.enabled, autonomy_mode=data.autonomy_mode,
            custom_instructions=data.custom_instructions,
            capabilities=data.capabilities, changed_fields=data.model_fields_set,
        )
        await materialize_response_before_commit(session, config)
        await session.commit()
    except AIWorkforceValidationError:
        await _rollback(session)
        raise _invalid() from None
    except AIWorkforceConflictError:
        await _rollback(session)
        raise _conflict() from None
    except (AIWorkforcePersistenceError, SQLAlchemyError):
        await _rollback(session)
        raise _unavailable() from None
    _set_private(response)
    return config_response(config)


@router.post("/agents/{role}/reset", response_model=AgentConfigResponse)
async def reset_role_config(
    role: AIAgentRole,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> AgentConfigResponse:
    try:
        config = await reset_agent_config(
            session, business_id=access.business.id, role=role,
            actor_user_id=access.user.id,
        )
        await materialize_response_before_commit(session, config)
        await session.commit()
    except (AIWorkforcePersistenceError, SQLAlchemyError):
        await _rollback(session)
        raise _unavailable() from None
    _set_private(response)
    return config_response(config)


@router.get("/commands/suggestions", response_model=list[SuggestedCommandResponse])
async def read_suggested_commands(
    access: BusinessAccessDependency, response: Response, session: SessionDependency
) -> list[SuggestedCommandResponse]:
    try:
        values = await suggested_commands(session, business_id=access.business.id)
    except AIWorkforcePersistenceError:
        raise _unavailable() from None
    _set_private(response)
    return values


@router.get("/commands/daily-brief", response_model=DailyBriefResponse)
async def read_daily_brief(
    access: BusinessAccessDependency, response: Response, session: SessionDependency
) -> DailyBriefResponse:
    try:
        value = await daily_brief(session, business_id=access.business.id)
    except AIWorkforcePersistenceError:
        raise _unavailable() from None
    _set_private(response)
    return value


@router.post("/commands", response_model=CommandResponse, status_code=status.HTTP_201_CREATED)
async def submit_command(
    data: CommandCreateRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    provider: AIAgentProviderDependency,
) -> CommandResponse:
    try:
        if isinstance(session, AsyncSession):
            await require_feature(session, business_id=access.business.id, key="ai_command_center")
            await require_capacity(session, business_id=access.business.id, key="max_ai_executions_month")
            await require_capacity(session, business_id=access.business.id, key="max_ai_input_tokens_month")
            await require_capacity(session, business_id=access.business.id, key="max_ai_output_tokens_month")
        command = await execute_command(
            session, business_id=access.business.id, user_id=access.user.id,
            request=data, provider=provider,
        )
        value = await command_response(session, business_id=access.business.id, command=command)
    except AIWorkforceValidationError:
        await _rollback(session)
        raise _invalid() from None
    except (AIWorkforcePersistenceError, SQLAlchemyError):
        await _rollback(session)
        raise _unavailable() from None
    _set_private(response)
    return value


@router.get("/commands", response_model=CommandPage)
async def read_command_history(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    page: Page = 1,
    page_size: PageSize = 25,
    status_filter: Annotated[
        Literal["queued", "running", "completed", "needs_approval", "failed", "canceled"] | None,
        Query(alias="status"),
    ] = None,
) -> CommandPage:
    try:
        commands, total = await list_commands(
            session, business_id=access.business.id, page=page,
            page_size=page_size, status=status_filter,
        )
    except AIWorkforcePersistenceError:
        raise _unavailable() from None
    _set_private(response)
    return CommandPage(
        items=[_command_shell(item) for item in commands],
        page=page, page_size=page_size, total=total,
    )


@router.get("/commands/{command_id}", response_model=CommandResponse)
async def read_command_detail(
    command_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> CommandResponse:
    try:
        command = await get_command(
            session, business_id=access.business.id, command_id=command_id
        )
        value = await command_response(session, business_id=access.business.id, command=command)
    except AIWorkforceNotFoundError:
        raise _not_found() from None
    except AIWorkforcePersistenceError:
        raise _unavailable() from None
    _set_private(response)
    return value


@router.post("/commands/{command_id}/cancel", response_model=CommandResponse)
async def cancel_queued_command(
    command_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> CommandResponse:
    try:
        command = await cancel_command(
            session, business_id=access.business.id,
            command_id=command_id, user_id=access.user.id,
        )
        await session.commit()
    except AIWorkforceNotFoundError:
        await _rollback(session)
        raise _not_found() from None
    except AIWorkforceConflictError:
        await _rollback(session)
        raise _conflict() from None
    except (AIWorkforcePersistenceError, SQLAlchemyError):
        await _rollback(session)
        raise _unavailable() from None
    _set_private(response)
    return _command_shell(command)


def _command_shell(command: AICommand) -> CommandResponse:
    return CommandResponse(
        id=command.id, business_id=command.business_id,
        requested_by_user_id=command.requested_by_user_id,
        command=command.command_text, status=command.status,
        route=persisted_command_route(command), execution_id=command.execution_id,
        summary=command.summary, failure_code=command.failure_code,
        executions=[], proposed_actions=[], created_at=command.created_at,
        completed_at=command.completed_at,
    )


async def _rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except SQLAlchemyError:
        pass


def _set_private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _not_found() -> HTTPException:
    return HTTPException(404, "AI workforce resource not found.", headers=_PRIVATE)


def _invalid() -> HTTPException:
    return HTTPException(422, "Invalid AI workforce request.", headers=_PRIVATE)


def _conflict() -> HTTPException:
    return HTTPException(409, "AI workforce request conflicts with current state.", headers=_PRIVATE)


def _unavailable() -> HTTPException:
    return HTTPException(503, "AI workforce is temporarily unavailable.", headers=_PRIVATE)


_PRIVATE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
