from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency
from app.db.session import get_db_session
from app.exceptions.business_memory import (
    BusinessMemoryCursorError,
    BusinessMemoryNotFoundError,
    BusinessMemoryPersistenceError,
    BusinessMemorySupersessionError,
)
from app.models.business_memory import BusinessMemory
from app.schemas.business_memory import (
    BusinessMemoryCreate,
    BusinessMemoryPageResponse,
    BusinessMemoryResponse,
    BusinessMemoryStatus,
    BusinessMemoryType,
    BusinessMemoryUpdate,
)
from app.services.business_memory import (
    DEFAULT_MEMORY_PAGE_SIZE,
    MAX_MEMORY_PAGE_SIZE,
    archive_business_memory,
    create_manual_memory,
    get_business_memory,
    list_business_memories,
    update_business_memory,
)

router = APIRouter(
    prefix="/businesses/{business_id}/memory",
    tags=["Business Memory"],
)

SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


@router.get(
    "",
    response_model=BusinessMemoryPageResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Invalid memory pagination cursor."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business memory is temporarily unavailable."
        },
    },
)
async def read_business_memories(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    memory_type: Annotated[BusinessMemoryType | None, Query()] = None,
    memory_status: Annotated[
        BusinessMemoryStatus | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_MEMORY_PAGE_SIZE,
        ),
    ] = DEFAULT_MEMORY_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> BusinessMemoryPageResponse:
    try:
        items, next_cursor = await list_business_memories(
            session,
            access.business.id,
            memory_type=memory_type,
            status=memory_status,
            limit=limit,
            cursor=cursor,
        )
    except BusinessMemoryCursorError:
        raise _invalid_cursor_exception() from None
    except BusinessMemoryPersistenceError:
        raise _memory_unavailable_exception() from None

    _set_private_response_headers(response)

    return BusinessMemoryPageResponse(
        items=items,
        next_cursor=next_cursor,
    )


@router.post(
    "",
    response_model=BusinessMemoryResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business memory is temporarily unavailable."
        }
    },
)
async def create_business_memory(
    memory_create: BusinessMemoryCreate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> BusinessMemory:
    try:
        memory = await create_manual_memory(
            session,
            access.business.id,
            memory_create,
        )
        await session.commit()
    except (BusinessMemoryPersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _memory_unavailable_exception() from None

    _set_private_response_headers(response)
    return memory


@router.get(
    "/{memory_id}",
    response_model=BusinessMemoryResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Business memory not found."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business memory is temporarily unavailable."
        },
    },
)
async def read_business_memory(
    memory_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> BusinessMemory:
    try:
        memory = await get_business_memory(
            session,
            access.business.id,
            memory_id,
        )
    except BusinessMemoryNotFoundError:
        raise _memory_not_found_exception() from None
    except BusinessMemoryPersistenceError:
        raise _memory_unavailable_exception() from None

    _set_private_response_headers(response)
    return memory


@router.patch(
    "/{memory_id}",
    response_model=BusinessMemoryResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Business memory not found."
        },
        status.HTTP_409_CONFLICT: {
            "description": "Requested memory lifecycle transition is invalid."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business memory is temporarily unavailable."
        },
    },
)
async def patch_business_memory(
    memory_id: UUID,
    memory_update: BusinessMemoryUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> BusinessMemory:
    try:
        memory = await update_business_memory(
            session,
            access.business.id,
            memory_id,
            memory_update,
        )
        await session.commit()
    except BusinessMemoryNotFoundError:
        await _rollback_safely(session)
        raise _memory_not_found_exception() from None
    except BusinessMemorySupersessionError:
        await _rollback_safely(session)
        raise _invalid_lifecycle_exception() from None
    except (BusinessMemoryPersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _memory_unavailable_exception() from None

    _set_private_response_headers(response)
    return memory


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Business memory not found."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business memory is temporarily unavailable."
        },
    },
)
async def delete_business_memory(
    memory_id: UUID,
    access: BusinessAccessDependency,
    session: SessionDependency,
) -> Response:
    try:
        await archive_business_memory(
            session,
            access.business.id,
            memory_id,
        )
        await session.commit()
    except BusinessMemoryNotFoundError:
        await _rollback_safely(session)
        raise _memory_not_found_exception() from None
    except (BusinessMemoryPersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _memory_unavailable_exception() from None

    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


async def _rollback_safely(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except SQLAlchemyError:
        return


def _set_private_response_headers(response: Response) -> None:
    for name, value in _PRIVATE_RESPONSE_HEADERS.items():
        response.headers[name] = value


def _memory_not_found_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Business memory not found.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


def _invalid_cursor_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Invalid memory pagination cursor.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


def _invalid_lifecycle_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Requested memory lifecycle transition is invalid.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


def _memory_unavailable_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Business memory is temporarily unavailable.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


_PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}