from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency
from app.db.session import get_db_session
from app.exceptions.business_brain import (
    BusinessKnowledgeEntryNotFoundError,
    BusinessKnowledgePersistenceError,
)
from app.models.business_knowledge_entry import BusinessKnowledgeEntry
from app.schemas.business_brain import (
    BusinessKnowledgeCategory,
    BusinessKnowledgeEntryCreate,
    BusinessKnowledgeEntryResponse,
    BusinessKnowledgeEntryUpdate,
    BusinessKnowledgeStatus,
)
from app.services.business_brain import (
    archive_knowledge_entry,
    create_knowledge_entry,
    get_knowledge_entry,
    list_knowledge_entries,
    update_knowledge_entry,
)

router = APIRouter(
    prefix="/businesses/{business_id}/brain/knowledge",
    tags=["Business Brain"],
)
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


@router.get(
    "",
    response_model=list[BusinessKnowledgeEntryResponse],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business Brain knowledge is temporarily unavailable."
        }
    },
)
async def read_knowledge_entries(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    category: Annotated[BusinessKnowledgeCategory | None, Query()] = None,
    entry_status: Annotated[
        BusinessKnowledgeStatus | None,
        Query(alias="status"),
    ] = None,
) -> list[BusinessKnowledgeEntry]:
    try:
        entries = await list_knowledge_entries(
            session,
            access.business.id,
            category=category,
            entry_status=entry_status,
        )
    except BusinessKnowledgePersistenceError:
        raise _knowledge_unavailable_exception() from None
    _set_private_response_headers(response)
    return entries


@router.post(
    "",
    response_model=BusinessKnowledgeEntryResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business Brain knowledge is temporarily unavailable."
        }
    },
)
async def create_knowledge(
    entry_create: BusinessKnowledgeEntryCreate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> BusinessKnowledgeEntry:
    try:
        entry = await create_knowledge_entry(
            session,
            access.business.id,
            entry_create,
        )
        await session.commit()
    except (BusinessKnowledgePersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _knowledge_unavailable_exception() from None
    _set_private_response_headers(response)
    return entry


@router.get(
    "/{entry_id}",
    response_model=BusinessKnowledgeEntryResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Knowledge entry not found."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business Brain knowledge is temporarily unavailable."
        },
    },
)
async def read_knowledge_entry(
    entry_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> BusinessKnowledgeEntry:
    try:
        entry = await get_knowledge_entry(session, access.business.id, entry_id)
    except BusinessKnowledgeEntryNotFoundError:
        raise _entry_not_found_exception() from None
    except BusinessKnowledgePersistenceError:
        raise _knowledge_unavailable_exception() from None
    _set_private_response_headers(response)
    return entry


@router.patch(
    "/{entry_id}",
    response_model=BusinessKnowledgeEntryResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Knowledge entry not found."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business Brain knowledge is temporarily unavailable."
        },
    },
)
async def patch_knowledge_entry(
    entry_id: UUID,
    entry_update: BusinessKnowledgeEntryUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> BusinessKnowledgeEntry:
    try:
        entry = await update_knowledge_entry(
            session,
            access.business.id,
            entry_id,
            entry_update,
        )
        await session.commit()
    except BusinessKnowledgeEntryNotFoundError:
        await _rollback_safely(session)
        raise _entry_not_found_exception() from None
    except (BusinessKnowledgePersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _knowledge_unavailable_exception() from None
    _set_private_response_headers(response)
    return entry


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Knowledge entry not found."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Business Brain knowledge is temporarily unavailable."
        },
    },
)
async def delete_knowledge_entry(
    entry_id: UUID,
    access: BusinessAccessDependency,
    session: SessionDependency,
) -> Response:
    try:
        await archive_knowledge_entry(session, access.business.id, entry_id)
        await session.commit()
    except BusinessKnowledgeEntryNotFoundError:
        await _rollback_safely(session)
        raise _entry_not_found_exception() from None
    except (BusinessKnowledgePersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _knowledge_unavailable_exception() from None
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


def _entry_not_found_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Knowledge entry not found.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


def _knowledge_unavailable_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Business Brain knowledge is temporarily unavailable.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


_PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}
