from __future__ import annotations

from typing import Annotated, Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
import json
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency, require_business_role
from app.api.response_materialization import materialize_response_before_commit
from app.db.session import get_db_session
from app.exceptions.commerce import (
    CommerceConflictError,
    CommerceNotFoundError,
    CommercePersistenceError,
    CommerceValidationError,
    CommerceConfigurationRequiredError,
    CommerceProviderError,
)
from app.integrations.commerce_registry import commerce_connectors
from app.schemas.commerce import (
    AudienceExportPreflightRequest,
    AudienceExportPreflightResponse,
    AudienceSegmentCompileRequest,
    AudienceSegmentCreate,
    AudienceSegmentResponse,
    CommerceConnectionCreate,
    CommerceConnectionConfigure,
    CommerceConnectionResponse,
    CommerceEventCreate,
    CommerceEventResponse,
    CommerceSyncRequest,
    CommerceSyncRunResponse,
    CommerceSyncIssueResponse,
    CommerceWebhookReceiptResponse,
    CommerceImportMapping,
    CommerceImportPreviewResponse,
    CommerceImportResultResponse,
    FeedDestinationCreate,
    FeedDestinationResponse,
    FeedDestinationSyncRequest,
    FeedProductStatusResponse,
    ProductGroupCreate,
    ProductGroupDestinationResponse,
    ProductGroupResponse,
    ProductGroupSyncRequest,
)
from app.services import ad_commerce as ad_commerce_service
from app.services import commerce as service
from app.services import commerce_import as import_service
from app.core.config import settings


router = APIRouter(prefix="/businesses/{business_id}/commerce", tags=["Commerce"])
webhook_router = APIRouter(prefix="/commerce/webhooks", tags=["Commerce webhooks"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/providers")
async def read_provider_registry(access: BusinessAccessDependency) -> list[dict[str, object]]:
    _ = access
    return [
        {
            "provider": item.provider,
            "display_name": item.display_name,
            "authentication": item.authentication,
            "capabilities": list(item.capabilities),
            "configured": item.configured,
            "implementation_status": item.implementation_status,
        }
        for item in commerce_connectors.provider_definitions()
    ]


@router.get("/connections", response_model=list[CommerceConnectionResponse])
async def read_connections(access: BusinessAccessDependency, session: SessionDependency):
    return await _read(lambda: service.list_connections(session, business_id=access.business.id))


@router.post("/connections", response_model=CommerceConnectionResponse, status_code=status.HTTP_201_CREATED)
async def add_connection(
    data: CommerceConnectionCreate, access: BusinessAccessDependency,
    response: Response, session: SessionDependency,
):
    require_business_role(access)
    return await _write(session, response, lambda: service.create_connection(
        session, business_id=access.business.id, actor_user_id=access.user.id, data=data,
    ))


@router.get("/connections/{connection_id}", response_model=CommerceConnectionResponse)
async def read_connection(connection_id: UUID, access: BusinessAccessDependency, session: SessionDependency):
    return await _read(lambda: service.get_connection(
        session, business_id=access.business.id, connection_id=connection_id,
    ))


@router.post("/connections/{connection_id}/configure", response_model=CommerceConnectionResponse)
async def configure_connection(
    connection_id: UUID, data: CommerceConnectionConfigure,
    access: BusinessAccessDependency, response: Response, session: SessionDependency,
):
    require_business_role(access)
    return await _write(session, response, lambda: service.configure_connection(
        session, business_id=access.business.id, connection_id=connection_id,
        actor_user_id=access.user.id, data=data,
    ))


@router.post("/connections/{connection_id}/sync", response_model=CommerceSyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def request_sync(
    connection_id: UUID, data: CommerceSyncRequest, access: BusinessAccessDependency,
    response: Response, session: SessionDependency,
):
    require_business_role(access)
    run, _created = await _write(session, response, lambda: service.request_sync(
        session, business_id=access.business.id, connection_id=connection_id,
        mode=data.mode, idempotency_key=data.idempotency_key,
    ))
    if run.status == "configuration_required":
        raise HTTPException(status_code=409, detail={
            "code": run.failure_code or "configuration_required",
            "message": "Configure and connect the commerce provider before synchronization.",
            "sync_run_id": str(run.id),
        })
    return run


@router.get("/connections/{connection_id}/sync-runs", response_model=list[CommerceSyncRunResponse])
async def read_sync_runs(
    connection_id: UUID, access: BusinessAccessDependency, session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
):
    return await _read(lambda: service.list_sync_runs(
        session, business_id=access.business.id, connection_id=connection_id, limit=limit,
    ))


@router.get("/sync-runs/{sync_run_id}/issues", response_model=list[CommerceSyncIssueResponse])
async def read_sync_issues(
    sync_run_id: UUID, access: BusinessAccessDependency, session: SessionDependency,
):
    return await _read(lambda: service.list_sync_issues(
        session, business_id=access.business.id, sync_run_id=sync_run_id,
    ))


@webhook_router.post("/{provider}/{connection_id}", response_model=CommerceWebhookReceiptResponse, status_code=status.HTTP_202_ACCEPTED)
async def receive_commerce_webhook(
    provider: str, connection_id: UUID, request: Request,
    response: Response, session: SessionDependency,
):
    body_buffer = bytearray()
    async for chunk in request.stream():
        body_buffer.extend(chunk)
        if len(body_buffer) > settings.integration_webhook_max_bytes:
            raise HTTPException(413, detail={"code": "webhook_payload_too_large"})
    body = bytes(body_buffer)
    if not body:
        raise HTTPException(400, detail={"code": "webhook_payload_empty"})
    try:
        receipt, duplicate = await service.ingest_provider_webhook(
            session, provider=provider, connection_id=connection_id,
            headers={key.casefold(): value for key, value in request.headers.items()},
            body=body,
        )
        result = CommerceWebhookReceiptResponse.model_validate(receipt).model_copy(update={"duplicate": duplicate})
        await session.commit()
    except CommerceNotFoundError:
        await session.rollback()
        raise HTTPException(404, detail={"code": "connection_not_found"}) from None
    except (CommerceConfigurationRequiredError, CommerceProviderError):
        await session.rollback()
        raise HTTPException(401, detail={"code": "webhook_verification_failed"}) from None
    except (CommercePersistenceError, SQLAlchemyError):
        await session.rollback()
        raise HTTPException(503, detail={"code": "temporary_failure"}) from None
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
    return result


@router.post("/imports/preview", response_model=CommerceImportPreviewResponse)
async def preview_commerce_import(
    access: BusinessAccessDependency,
    file_type: Annotated[str, Form()],
    mapping_json: Annotated[str, Form()] = "{}",
    upload: UploadFile = File(...),
):
    _ = access
    try:
        mapping = _parse_import_mapping(mapping_json)
        return import_service.preview_import(
            upload.file, filename=upload.filename or "import",
            file_type=file_type, mapping=mapping,
        )
    except CommerceValidationError as error:
        raise HTTPException(422, detail={"code": str(error) or "import_invalid"}) from None
    finally:
        await upload.close()


@router.post("/connections/{connection_id}/imports", response_model=CommerceImportResultResponse)
async def apply_commerce_import(
    connection_id: UUID, access: BusinessAccessDependency,
    response: Response, session: SessionDependency,
    file_type: Annotated[str, Form()],
    idempotency_key: Annotated[str, Form(min_length=8, max_length=255)],
    mapping_json: Annotated[str, Form()] = "{}",
    upload: UploadFile = File(...),
):
    try:
        mapping = _parse_import_mapping(mapping_json)
        return await _write(session, response, lambda: import_service.import_products(
            session, business_id=access.business.id, connection_id=connection_id,
            stream=upload.file, filename=upload.filename or "import",
            file_type=file_type, mapping=mapping, idempotency_key=idempotency_key,
        ))
    finally:
        await upload.close()


@router.post("/events", response_model=CommerceEventResponse, status_code=status.HTTP_201_CREATED)
async def add_event(
    data: CommerceEventCreate, access: BusinessAccessDependency,
    response: Response, session: SessionDependency,
):
    event, duplicate = await _write(session, response, lambda: service.ingest_event(
        session, business_id=access.business.id, actor_user_id=access.user.id, data=data,
    ))
    return CommerceEventResponse.model_validate(event).model_copy(update={"duplicate": duplicate})


@router.get("/events", response_model=list[CommerceEventResponse])
async def read_events(
    access: BusinessAccessDependency, session: SessionDependency,
    event_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    items = await _read(lambda: service.list_events(
        session, business_id=access.business.id, event_type=event_type, limit=limit,
    ))
    return [CommerceEventResponse.model_validate(item) for item in items]


@router.get("/audience-segments", response_model=list[AudienceSegmentResponse])
async def read_segments(access: BusinessAccessDependency, session: SessionDependency):
    return await _read(lambda: service.list_segments(session, business_id=access.business.id))


@router.post("/audience-segments", response_model=AudienceSegmentResponse, status_code=status.HTTP_201_CREATED)
async def add_segment(
    data: AudienceSegmentCreate, access: BusinessAccessDependency,
    response: Response, session: SessionDependency,
):
    return await _write(session, response, lambda: service.create_segment(
        session, business_id=access.business.id, actor_user_id=access.user.id, data=data,
    ))


@router.post("/audience-segments/compile", response_model=AudienceSegmentResponse, status_code=status.HTTP_201_CREATED)
async def compile_segment(
    data: AudienceSegmentCompileRequest, access: BusinessAccessDependency,
    response: Response, session: SessionDependency,
):
    return await _write(session, response, lambda: service.compile_segment(
        session, business_id=access.business.id, actor_user_id=access.user.id, data=data,
    ))


@router.post("/audience-segments/{segment_id}/refresh", response_model=AudienceSegmentResponse)
async def refresh_segment(
    segment_id: UUID, access: BusinessAccessDependency,
    response: Response, session: SessionDependency,
):
    return await _write(session, response, lambda: service.refresh_segment(
        session, business_id=access.business.id, segment_id=segment_id,
    ))


@router.post(
    "/audience-segments/{segment_id}/export-preflight",
    response_model=AudienceExportPreflightResponse,
)
async def preflight_audience_export(
    segment_id: UUID, data: AudienceExportPreflightRequest,
    access: BusinessAccessDependency, session: SessionDependency,
):
    return await _read(lambda: service.audience_export_preflight(
        session, business_id=access.business.id, segment_id=segment_id,
        provider=data.provider,
    ))


@router.get("/feed-destinations", response_model=list[FeedDestinationResponse])
async def read_feed_destinations(access: BusinessAccessDependency, session: SessionDependency):
    return await _read(lambda: service.list_feed_destinations(session, business_id=access.business.id))


@router.post("/feed-destinations", response_model=FeedDestinationResponse, status_code=status.HTTP_201_CREATED)
async def add_feed_destination(
    data: FeedDestinationCreate, access: BusinessAccessDependency,
    response: Response, session: SessionDependency,
):
    require_business_role(access)
    return await _write(session, response, lambda: service.create_feed_destination(
        session, business_id=access.business.id, actor_user_id=access.user.id, data=data,
    ))


@router.post("/feed-destinations/{destination_id}/evaluate", response_model=FeedDestinationResponse)
async def evaluate_feed_destination(
    destination_id: UUID, access: BusinessAccessDependency,
    response: Response, session: SessionDependency,
):
    return await _write(session, response, lambda: service.evaluate_feed_quality(
        session, business_id=access.business.id, destination_id=destination_id,
    ))


@router.get("/feed-destinations/{destination_id}/products", response_model=list[FeedProductStatusResponse])
async def read_feed_product_statuses(
    destination_id: UUID, access: BusinessAccessDependency, session: SessionDependency,
):
    return await _read(lambda: service.list_feed_product_statuses(
        session, business_id=access.business.id, destination_id=destination_id,
    ))


@router.post("/feed-destinations/{destination_id}/sync", response_model=FeedDestinationResponse)
async def synchronize_feed_destination(
    destination_id: UUID, data: FeedDestinationSyncRequest,
    access: BusinessAccessDependency, response: Response, session: SessionDependency,
):
    require_business_role(access)
    return await _write(session, response, lambda: ad_commerce_service.synchronize_destination(
        session, business_id=access.business.id, destination_id=destination_id,
        actor_user_id=access.user.id, idempotency_key=data.idempotency_key,
        reconcile_only=data.reconcile_only,
    ))


@router.get("/product-groups", response_model=list[ProductGroupResponse])
async def read_product_groups(access: BusinessAccessDependency, session: SessionDependency):
    return await _read(lambda: ad_commerce_service.list_product_groups(
        session, business_id=access.business.id,
    ))


@router.post("/product-groups", response_model=ProductGroupResponse, status_code=status.HTTP_201_CREATED)
async def add_product_group(
    data: ProductGroupCreate, access: BusinessAccessDependency,
    response: Response, session: SessionDependency,
):
    group = await _write(session, response, lambda: ad_commerce_service.create_product_group(
        session, business_id=access.business.id, actor_user_id=access.user.id, data=data,
    ))
    values = await ad_commerce_service.list_product_groups(session, business_id=access.business.id)
    return next(item for item in values if item["id"] == group.id)


@router.post("/product-groups/{product_group_id}/sync", response_model=ProductGroupDestinationResponse)
async def synchronize_product_group(
    product_group_id: UUID, data: ProductGroupSyncRequest,
    access: BusinessAccessDependency, response: Response, session: SessionDependency,
):
    require_business_role(access)
    return await _write(session, response, lambda: ad_commerce_service.synchronize_product_group(
        session, business_id=access.business.id, product_group_id=product_group_id,
        destination_id=data.destination_id, actor_user_id=access.user.id,
        idempotency_key=data.idempotency_key,
    ))


async def _read(operation: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await operation()
    except CommerceNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "commerce_resource_not_found"}) from None
    except CommerceValidationError as error:
        raise HTTPException(status_code=422, detail={"code": str(error) or "validation_error"}) from None
    except CommerceConflictError as error:
        raise HTTPException(status_code=409, detail={"code": str(error) or "commerce_conflict"}) from None
    except CommerceConfigurationRequiredError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
    except CommerceProviderError as error:
        status_code = 401 if error.code == "authentication_failed" else 429 if error.code == "rate_limited" else 503
        raise HTTPException(status_code=status_code, detail={"code": error.code}) from None
    except (CommercePersistenceError, SQLAlchemyError):
        raise HTTPException(status_code=503, detail={"code": "temporary_failure", "message": "Commerce data is temporarily unavailable."}) from None


async def _write(
    session: AsyncSession, response: Response, operation: Callable[[], Awaitable[Any]],
) -> Any:
    try:
        result = await operation()
        await materialize_response_before_commit(session, result)
        await session.commit()
    except HTTPException:
        await session.rollback()
        raise
    except CommerceNotFoundError:
        await session.rollback()
        raise HTTPException(status_code=404, detail={"code": "commerce_resource_not_found"}) from None
    except CommerceValidationError as error:
        await session.rollback()
        raise HTTPException(status_code=422, detail={"code": str(error) or "validation_error"}) from None
    except CommerceConflictError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail={"code": str(error) or "commerce_conflict"}) from None
    except CommerceConfigurationRequiredError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
    except CommerceProviderError as error:
        await session.rollback()
        status_code = 401 if error.code == "authentication_failed" else 429 if error.code == "rate_limited" else 503
        raise HTTPException(status_code=status_code, detail={"code": error.code}) from None
    except (CommercePersistenceError, SQLAlchemyError):
        await session.rollback()
        raise HTTPException(status_code=503, detail={"code": "temporary_failure", "message": "Commerce data is temporarily unavailable."}) from None
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
    return result


def _parse_import_mapping(value: str) -> CommerceImportMapping:
    try:
        decoded = json.loads(value)
        if decoded == {}:
            decoded = {"fields": {}}
        return CommerceImportMapping.model_validate(decoded)
    except (json.JSONDecodeError, ValidationError):
        raise CommerceValidationError("import_mapping_invalid") from None
