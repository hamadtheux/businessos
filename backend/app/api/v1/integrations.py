from __future__ import annotations

import json
from collections.abc import Awaitable, Mapping
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency, require_business_role
from app.api.response_materialization import materialize_response_before_commit
from app.core.config import settings
from app.db.session import get_db_session
from app.domain.integrations import ConnectorType
from app.exceptions.integration import (
    IntegrationConflictError,
    IntegrationCredentialUnavailableError,
    IntegrationNotFoundError,
    IntegrationPersistenceError,
    IntegrationProviderUnavailableError,
    IntegrationStateError,
    IntegrationValidationError,
    IntegrationWebhookVerificationError,
)
from app.integrations.webhooks import DisabledWebhookSignatureVerifier, MetaWebhookSignatureVerifier
from app.schemas.integration import (
    AuthorizationCallbackResponse,
    AuthorizationStartRequest,
    AuthorizationStartResponse,
    ConnectorDefinitionResponse,
    EntityLinkCreate,
    ExternalResourceResponse,
    IntegrationConnectionResponse,
    IntegrationEntityLinkResponse,
    IntegrationWebhookEventResponse,
    ResourceSelectionRequest,
)
from app.schemas.operations import PageResponse
from app.services import integrations as service


router = APIRouter(tags=["Integrations"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


@router.get(
    "/businesses/{business_id}/integrations/registry",
    response_model=list[ConnectorDefinitionResponse],
)
async def read_connector_registry(access: BusinessAccessDependency, response: Response):
    _set_private(response)
    return service.connector_catalog()


@router.get(
    "/businesses/{business_id}/integrations/connections",
    response_model=list[IntegrationConnectionResponse],
)
async def read_connections(
    access: BusinessAccessDependency, response: Response, session: SessionDependency,
):
    return await _read(
        response,
        service.list_connections(session, business_id=access.business.id),
    )


@router.get(
    "/businesses/{business_id}/integrations/connections/{connection_id}",
    response_model=IntegrationConnectionResponse,
)
async def read_connection(
    connection_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    return await _read(
        response,
        service.get_connection(
            session, business_id=access.business.id, connection_id=connection_id
        ),
    )


@router.post(
    "/businesses/{business_id}/integrations/{connector_type}/authorize",
    response_model=AuthorizationStartResponse,
)
async def authorize_connector(
    connector_type: ConnectorType,
    data: AuthorizationStartRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    require_business_role(access)
    return await _mutate(
        response,
        session,
        service.begin_authorization(
            session,
            business_id=access.business.id,
            user_id=access.user.id,
            connector_type=connector_type,
            redirect_target=data.redirect_target,
        ),
    )


@router.post(
    "/businesses/{business_id}/integrations/connections/{connection_id}/reconnect",
    response_model=AuthorizationStartResponse,
)
async def reconnect_connector(
    connection_id: UUID,
    data: AuthorizationStartRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    require_business_role(access)
    connection = await _read(
        response,
        service.get_connection(
            session, business_id=access.business.id, connection_id=connection_id
        ),
    )
    return await _mutate(
        response,
        session,
        service.begin_authorization(
            session,
            business_id=access.business.id,
            user_id=access.user.id,
            connector_type=connection.connector_type,
            redirect_target=data.redirect_target,
        ),
    )


@router.get(
    "/integrations/oauth/callback",
    response_model=AuthorizationCallbackResponse,
)
async def oauth_callback(
    state: Annotated[str, Query(min_length=1, max_length=512)],
    code: Annotated[str, Query(min_length=1, max_length=4096)],
    response: Response,
    session: SessionDependency,
):
    return await _mutate(
        response,
        session,
        service.complete_authorization(
            session,
            connector_type=None,
            state=state,
            code=code,
        ),
    )


@router.get(
    "/integrations/oauth/{connector_type}/callback",
    response_model=AuthorizationCallbackResponse,
    deprecated=True,
)
async def legacy_oauth_callback(
    connector_type: ConnectorType,
    state: Annotated[str, Query(min_length=1, max_length=512)],
    code: Annotated[str, Query(min_length=1, max_length=4096)],
    response: Response,
    session: SessionDependency,
):
    return await _mutate(
        response,
        session,
        service.complete_authorization(
            session,
            connector_type=connector_type,
            state=state,
            code=code,
        ),
    )


@router.get(
    "/businesses/{business_id}/integrations/connections/{connection_id}/resources",
    response_model=list[ExternalResourceResponse],
)
async def read_resources(
    connection_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    return await _read(
        response,
        service.list_resources(
            session, business_id=access.business.id, connection_id=connection_id
        ),
    )


@router.post(
    "/businesses/{business_id}/integrations/connections/{connection_id}/resources/select",
    response_model=IntegrationConnectionResponse,
)
async def select_connection_resource(
    connection_id: UUID,
    data: ResourceSelectionRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    require_business_role(access)
    return await _mutate(
        response,
        session,
        service.select_resource(
            session,
            business_id=access.business.id,
            connection_id=connection_id,
            actor_user_id=access.user.id,
            data=data,
        ),
    )


@router.post(
    "/businesses/{business_id}/integrations/connections/{connection_id}/health",
    response_model=IntegrationConnectionResponse,
)
async def check_connection_health(
    connection_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    require_business_role(access)

    return await _mutate(
        response,
        session,
        service.check_health(
            session,
            business_id=access.business.id,
            connection_id=connection_id,
            actor_user_id=access.user.id,
        ),
    )


@router.post(
    "/businesses/{business_id}/integrations/connections/{connection_id}/disconnect",
    response_model=IntegrationConnectionResponse,
)
async def disconnect_connection(
    connection_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    require_business_role(access)
    return await _mutate(
        response,
        session,
        service.disconnect(
            session,
            business_id=access.business.id,
            connection_id=connection_id,
            actor_user_id=access.user.id,
        ),
    )


@router.get(
    "/businesses/{business_id}/integrations/connections/{connection_id}/events",
    response_model=PageResponse[IntegrationWebhookEventResponse],
)
async def read_connection_events(
    connection_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    page: Page = 1,
    page_size: PageSize = 25,
):
    items, total = await _read(
        response,
        service.list_webhook_events(
            session,
            business_id=access.business.id,
            connection_id=connection_id,
            page=page,
            page_size=page_size,
        ),
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post(
    "/businesses/{business_id}/integrations/connections/{connection_id}/entity-links",
    response_model=IntegrationEntityLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_entity_link(
    connection_id: UUID,
    data: EntityLinkCreate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    require_business_role(access)
    return await _mutate(
        response,
        session,
        service.create_entity_link(
            session,
            business_id=access.business.id,
            connection_id=connection_id,
            actor_user_id=access.user.id,
            data=data,
        ),
    )


@router.get("/integrations/webhooks/meta")
async def verify_meta_webhook(
    hub_mode: Annotated[str, Query(alias="hub.mode", max_length=64)],
    hub_verify_token: Annotated[str, Query(alias="hub.verify_token", max_length=512)],
    hub_challenge: Annotated[str, Query(alias="hub.challenge", max_length=512)],
    response: Response,
):
    configured = settings.meta_webhook_verify_token
    supplied_matches = bool(
        configured
        and secrets_compare(configured.get_secret_value(), hub_verify_token)
    )
    if hub_mode != "subscribe" or not supplied_matches:
        raise HTTPException(401, "Webhook verification failed.", headers=_PRIVATE_HEADERS)
    _set_private(response)
    return Response(content=hub_challenge, media_type="text/plain", headers=_PRIVATE_HEADERS)


@router.post(
    "/integrations/webhooks/{connector_type}/{connection_id}",
    response_model=IntegrationWebhookEventResponse,
)
async def receive_webhook(
    connector_type: ConnectorType,
    connection_id: UUID,
    request: Request,
    response: Response,
    session: SessionDependency,
):
    body = await request.body()
    if not body or len(body) > settings.integration_webhook_max_bytes:
        raise HTTPException(413, "Webhook payload is too large.", headers=_PRIVATE_HEADERS)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(422, "Webhook payload is invalid.", headers=_PRIVATE_HEADERS) from None
    if not isinstance(payload, Mapping):
        raise HTTPException(422, "Webhook payload is invalid.", headers=_PRIVATE_HEADERS)
    verifier = _webhook_verifier(connector_type)
    return await _mutate(
        response,
        session,
        service.ingest_webhook(
            session,
            connector_type=connector_type,
            connection_id=connection_id,
            body=body,
            headers=request.headers,
            payload=payload,
            verifier=verifier,
        ),
    )


async def _read(response: Response, operation: Awaitable):
    try:
        value = await operation
    except Exception as exc:
        raise _http_error(exc) from None
    _set_private(response)
    return value


async def _mutate(response: Response, session: AsyncSession, operation: Awaitable):
    try:
        value = await operation
        await materialize_response_before_commit(session, value)
        await session.commit()
    except Exception as exc:
        await _rollback(session)
        raise _http_error(exc) from None
    _set_private(response)
    return value


async def _rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except SQLAlchemyError:
        pass


def _set_private(response: Response) -> None:
    for key, value in _PRIVATE_HEADERS.items():
        response.headers[key] = value


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, IntegrationNotFoundError):
        return HTTPException(404, "Integration resource not found.", headers=_PRIVATE_HEADERS)
    if isinstance(exc, IntegrationWebhookVerificationError):
        return HTTPException(401, "Webhook verification failed.", headers=_PRIVATE_HEADERS)
    if isinstance(exc, IntegrationValidationError):
        return HTTPException(422, "Invalid integration request.", headers=_PRIVATE_HEADERS)
    if isinstance(exc, (IntegrationConflictError, IntegrationStateError)):
        return HTTPException(409, "Integration request conflicts with its current state.", headers=_PRIVATE_HEADERS)
    if isinstance(exc, (IntegrationProviderUnavailableError, IntegrationCredentialUnavailableError)):
        return HTTPException(503, "Integration provider setup or service is unavailable.", headers=_PRIVATE_HEADERS)
    if isinstance(exc, (IntegrationPersistenceError, SQLAlchemyError)):
        return HTTPException(503, "Integrations are temporarily unavailable.", headers=_PRIVATE_HEADERS)
    return HTTPException(503, "Integrations are temporarily unavailable.", headers=_PRIVATE_HEADERS)


def _webhook_verifier(connector_type: str):
    if connector_type in {"whatsapp_business", "meta_ads", "facebook", "instagram"}:
        secret = settings.meta_webhook_signing_secret
        if secret:
            return MetaWebhookSignatureVerifier(secret.get_secret_value())
    return DisabledWebhookSignatureVerifier()


def secrets_compare(expected: str, supplied: str) -> bool:
    import hmac

    return hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))
