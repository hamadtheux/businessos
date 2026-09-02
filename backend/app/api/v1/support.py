from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency
from app.api.response_materialization import materialize_response_before_commit
from app.db.session import get_db_session
from app.exceptions.operations import (
    OperationsConflictError,
    OperationsNotFoundError,
    OperationsPersistenceError,
    OperationsStateError,
    OperationsValidationError,
)
from app.schemas.operations import (
    PageResponse,
    SupportCaseCreate,
    SupportCaseResponse,
    SupportCaseUpdate,
    SupportMetricsResponse,
)
from app.services import support as service


router = APIRouter(prefix="/businesses/{business_id}/support", tags=["Customer Support"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


@router.get("/cases", response_model=PageResponse[SupportCaseResponse])
async def read_support_cases(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    search: Annotated[str | None, Query(max_length=100)] = None,
    case_status: Annotated[str | None, Query(alias="status", pattern=r"^(new|open|ai_handling|waiting_for_customer|waiting_for_business|escalated|resolved|closed)$")] = None,
    priority: Annotated[str | None, Query(pattern=r"^(low|medium|high|urgent)$")] = None,
    channel: Annotated[str | None, Query(pattern=r"^(website|whatsapp|email|facebook|instagram|manual|other)$")] = None,
):
    cases, total = await _read(response, service.list_support_cases(session, business_id=access.business.id, page=page, page_size=page_size, search=search, status=case_status, priority=priority, channel=channel))
    items = await _read(response, service.support_case_responses(session, cases))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/metrics", response_model=SupportMetricsResponse)
async def read_support_metrics(access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _read(response, service.support_metrics(session, business_id=access.business.id))


@router.post("/cases", response_model=SupportCaseResponse, status_code=status.HTTP_201_CREATED)
async def create_support_case(data: SupportCaseCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    case = await _mutate(session, service.create_support_case(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))
    return await _read(response, service.support_case_response(session, case, include_conversation=True))


@router.get("/cases/{case_id}", response_model=SupportCaseResponse)
async def read_support_case(case_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    case = await _read(response, service.get_support_case(session, business_id=access.business.id, case_id=case_id))
    return await _read(response, service.support_case_response(session, case, include_conversation=True))


@router.patch("/cases/{case_id}", response_model=SupportCaseResponse)
async def patch_support_case(case_id: UUID, data: SupportCaseUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    case = await _mutate(session, service.update_support_case(session, business_id=access.business.id, case_id=case_id, actor_user_id=access.user.id, data=data))
    return await _read(response, service.support_case_response(session, case, include_conversation=True))


async def _read(response: Response, operation: Awaitable):
    try:
        value = await operation
    except OperationsNotFoundError:
        raise HTTPException(404, "Support resource not found.", headers=_PRIVATE_HEADERS) from None
    except OperationsValidationError:
        raise HTTPException(422, "Invalid support request.", headers=_PRIVATE_HEADERS) from None
    except (OperationsConflictError, OperationsStateError):
        raise HTTPException(409, "Support request conflicts with the current state.", headers=_PRIVATE_HEADERS) from None
    except OperationsPersistenceError:
        raise HTTPException(503, "Customer Support is temporarily unavailable.", headers=_PRIVATE_HEADERS) from None
    response.headers.update(_PRIVATE_HEADERS)
    return value


async def _mutate(session: AsyncSession, operation: Awaitable):
    try:
        value = await operation
        await materialize_response_before_commit(session, value)
        await session.commit()
        return value
    except OperationsNotFoundError:
        await _rollback(session)
        raise HTTPException(404, "Support resource not found.", headers=_PRIVATE_HEADERS) from None
    except OperationsValidationError:
        await _rollback(session)
        raise HTTPException(422, "Invalid support request.", headers=_PRIVATE_HEADERS) from None
    except (OperationsConflictError, OperationsStateError):
        await _rollback(session)
        raise HTTPException(409, "Support request conflicts with the current state.", headers=_PRIVATE_HEADERS) from None
    except (OperationsPersistenceError, SQLAlchemyError):
        await _rollback(session)
        raise HTTPException(503, "Customer Support is temporarily unavailable.", headers=_PRIVATE_HEADERS) from None


async def _rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except SQLAlchemyError:
        pass
