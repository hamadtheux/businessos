from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency
from app.api.response_materialization import materialize_response_before_commit
from app.db.session import get_db_session
from app.exceptions.catalog import (
    CatalogImportFileError,
    CatalogImportTooLargeError,
    CatalogImportValidationError,
    CatalogItemNotFoundError,
    CatalogPersistenceError,
    CatalogSkuConflictError,
)
from app.models.catalog_item import CatalogItem
from app.schemas.catalog import (
    CatalogImportPreviewResponse,
    CatalogImportResult,
    CatalogItemCreate,
    CatalogItemResponse,
    CatalogItemStatus,
    CatalogItemType,
    CatalogItemUpdate,
)
from app.services.catalog import (
    archive_catalog_item,
    create_catalog_item,
    create_catalog_items,
    get_catalog_item,
    list_catalog_items,
    update_catalog_item,
)
from app.services.catalog_import import (
    MAX_CATALOG_IMPORT_BYTES,
    PreparedCatalogImport,
    prepare_catalog_import,
    require_valid_catalog_import,
)
from app.services.automation_intelligence import schedule_competitor_discovery

router = APIRouter(
    prefix="/businesses/{business_id}/catalog",
    tags=["Catalog"],
)
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
CatalogUpload = Annotated[
    UploadFile,
    File(description="UTF-8 CSV or XLSX catalog file up to 10 MB."),
]


@router.post(
    "/import/preview",
    response_model=CatalogImportPreviewResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "description": "The upload exceeds the catalog import limit."
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The catalog import file is unsupported or invalid."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The business catalog is temporarily unavailable."
        },
    },
)
async def preview_catalog_import(
    file: CatalogUpload,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> CatalogImportPreviewResponse:
    prepared = await _prepare_upload(file, access.business.id, session)
    _set_private_response_headers(response)
    return prepared.preview


@router.post(
    "/import",
    response_model=CatalogImportResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "A concurrent catalog SKU conflict occurred."
        },
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "description": "The upload exceeds the catalog import limit."
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": CatalogImportPreviewResponse,
            "description": "No rows were created because import validation failed.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The business catalog is temporarily unavailable."
        },
    },
)
async def commit_catalog_import(
    file: CatalogUpload,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> CatalogImportResult | Response:
    prepared = await _prepare_upload(file, access.business.id, session)
    try:
        item_creates = require_valid_catalog_import(prepared)
    except CatalogImportValidationError as error:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=jsonable_encoder(error.preview),
            headers=_PRIVATE_RESPONSE_HEADERS,
        )

    try:
        items = await create_catalog_items(
            session,
            access.business.id,
            item_creates,
        )
        if isinstance(session, AsyncSession):
            await schedule_competitor_discovery(
                session, business_id=access.business.id, trigger_type="brain_change"
            )
        await session.commit()
    except CatalogSkuConflictError:
        await _rollback_safely(session)
        raise _sku_conflict_exception() from None
    except (CatalogPersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _catalog_unavailable_exception() from None

    _set_private_response_headers(response)
    return CatalogImportResult(
        created_count=len(items),
        total_rows=prepared.preview.total_rows,
    )


@router.get(
    "",
    response_model=list[CatalogItemResponse],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The business catalog is temporarily unavailable."
        }
    },
)
async def read_catalog(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    item_type: Annotated[CatalogItemType | None, Query()] = None,
    item_status: Annotated[
        CatalogItemStatus | None,
        Query(alias="status"),
    ] = None,
) -> list[CatalogItem]:
    try:
        items = await list_catalog_items(
            session,
            access.business.id,
            item_type=item_type,
            item_status=item_status,
        )
    except CatalogPersistenceError:
        raise _catalog_unavailable_exception() from None
    _set_private_response_headers(response)
    return items


@router.post(
    "",
    response_model=CatalogItemResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "The SKU already exists in this business."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The business catalog is temporarily unavailable."
        },
    },
)
async def create_item(
    item_create: CatalogItemCreate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> CatalogItem:
    try:
        item = await create_catalog_item(
            session,
            access.business.id,
            item_create,
        )
        if isinstance(session, AsyncSession):
            await schedule_competitor_discovery(
                session, business_id=access.business.id, trigger_type="brain_change"
            )
        await materialize_response_before_commit(session, item)
        await session.commit()
    except CatalogSkuConflictError:
        await _rollback_safely(session)
        raise _sku_conflict_exception() from None
    except (CatalogPersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _catalog_unavailable_exception() from None
    _set_private_response_headers(response)
    return item


@router.get(
    "/{item_id}",
    response_model=CatalogItemResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Catalog item not found."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The business catalog is temporarily unavailable."
        },
    },
)
async def read_catalog_item(
    item_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> CatalogItem:
    try:
        item = await get_catalog_item(session, access.business.id, item_id)
    except CatalogItemNotFoundError:
        raise _item_not_found_exception() from None
    except CatalogPersistenceError:
        raise _catalog_unavailable_exception() from None
    _set_private_response_headers(response)
    return item


@router.patch(
    "/{item_id}",
    response_model=CatalogItemResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Catalog item not found."},
        status.HTTP_409_CONFLICT: {
            "description": "The SKU already exists in this business."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The business catalog is temporarily unavailable."
        },
    },
)
async def patch_catalog_item(
    item_id: UUID,
    item_update: CatalogItemUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> CatalogItem:
    try:
        item = await update_catalog_item(
            session,
            access.business.id,
            item_id,
            item_update,
        )
        if isinstance(session, AsyncSession):
            await schedule_competitor_discovery(
                session, business_id=access.business.id, trigger_type="brain_change"
            )
        await materialize_response_before_commit(session, item)
        await session.commit()
    except CatalogItemNotFoundError:
        await _rollback_safely(session)
        raise _item_not_found_exception() from None
    except CatalogSkuConflictError:
        await _rollback_safely(session)
        raise _sku_conflict_exception() from None
    except (CatalogPersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _catalog_unavailable_exception() from None
    _set_private_response_headers(response)
    return item


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Catalog item not found."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The business catalog is temporarily unavailable."
        },
    },
)
async def delete_catalog_item(
    item_id: UUID,
    access: BusinessAccessDependency,
    session: SessionDependency,
) -> Response:
    try:
        await archive_catalog_item(session, access.business.id, item_id)
        if isinstance(session, AsyncSession):
            await schedule_competitor_discovery(
                session, business_id=access.business.id, trigger_type="brain_change"
            )
        await session.commit()
    except CatalogItemNotFoundError:
        await _rollback_safely(session)
        raise _item_not_found_exception() from None
    except (CatalogPersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _catalog_unavailable_exception() from None
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


async def _prepare_upload(
    file: UploadFile,
    business_id: UUID,
    session: AsyncSession,
) -> PreparedCatalogImport:
    content = await file.read(MAX_CATALOG_IMPORT_BYTES + 1)
    try:
        return await prepare_catalog_import(
            session,
            business_id,
            filename=file.filename,
            content=content,
        )
    except CatalogImportTooLargeError as error:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(error),
            headers=_PRIVATE_RESPONSE_HEADERS,
        ) from None
    except CatalogImportFileError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
            headers=_PRIVATE_RESPONSE_HEADERS,
        ) from None
    except CatalogPersistenceError:
        raise _catalog_unavailable_exception() from None


async def _rollback_safely(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except SQLAlchemyError:
        return


def _set_private_response_headers(response: Response) -> None:
    for name, value in _PRIVATE_RESPONSE_HEADERS.items():
        response.headers[name] = value


def _item_not_found_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Catalog item not found.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


def _sku_conflict_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="SKU already exists in this business.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


def _catalog_unavailable_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Business catalog is temporarily unavailable.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


_PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}
