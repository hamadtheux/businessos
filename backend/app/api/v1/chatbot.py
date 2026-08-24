from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from time import perf_counter_ns
from typing import Annotated, Awaitable, TypeVar
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.provider import get_agent_provider_model_name, validate_agent_provider
from app.api.dependencies.ai_agent import get_ai_agent_provider
from app.api.dependencies.business import BusinessAccessDependency, SessionDependency
from app.api.response_materialization import materialize_response_before_commit
from app.exceptions.ai_agent import AIAgentError
from app.exceptions.ai_agent_execution import AIAgentExecutionLedgerError
from app.exceptions.chatbot import (
    ChatbotAuthorizationError,
    ChatbotConflictError,
    ChatbotDisabledError,
    ChatbotError,
    ChatbotNotFoundError,
    ChatbotOriginError,
    ChatbotPersistenceError,
    ChatbotRateLimitError,
    ChatbotValidationError,
)
from app.schemas.chatbot import (
    ChatbotAnalyticsResponse,
    ChatbotConfigResponse,
    ChatbotConfigUpdate,
    ChatbotDeploymentList,
    ChatbotDeploymentTarget,
    PublicAppointmentBookingRequest,
    PublicAppointmentBookingResponse,
    PublicAvailabilityRequest,
    PublicAvailabilityResponse,
    PublicChatMessageRequest,
    PublicChatMessageResponse,
    PublicHandoffRequest,
    PublicHandoffResponse,
    PublicLeadCaptureRequest,
    PublicLeadCaptureResponse,
    PublicOrderLookupRequest,
    PublicOrderStatusResponse,
    PublicSessionResponse,
    PublicWidgetConfig,
)
from app.services import chatbot as service
from app.services.ai_agent_execution import (
    create_running_ai_agent_execution,
    fail_ai_agent_execution,
    finalize_successful_ai_agent_execution,
)
from app.services.billing import require_feature


router = APIRouter(tags=["Website Chatbot"])
T = TypeVar("T")
OriginHeader = Annotated[str | None, Header(alias="Origin")]
RefererHeader = Annotated[str | None, Header(alias="Referer")]
AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]
WidgetPublicId = Annotated[
    str,
    Path(min_length=40, max_length=96, pattern=r"^[A-Za-z0-9_-]+$"),
]


@router.get(
    "/businesses/{business_id}/chatbot",
    response_model=ChatbotConfigResponse,
)
async def read_chatbot_config(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ChatbotConfigResponse:
    config = await _mutate(
        response,
        session,
        service.get_or_create_config(session, business=access.business),
    )
    return service.config_response(config, access.business)


@router.put(
    "/businesses/{business_id}/chatbot",
    response_model=ChatbotConfigResponse,
)
async def replace_chatbot_config(
    data: ChatbotConfigUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ChatbotConfigResponse:
    if data.enabled and isinstance(session, AsyncSession):
        await require_feature(session, business_id=access.business.id, key="website_chatbot")
    config = await _mutate(
        response,
        session,
        service.update_config(
            session,
            business=access.business,
            actor_user_id=access.user.id,
            data=data,
        ),
    )
    return service.config_response(config, access.business)


@router.post(
    "/businesses/{business_id}/chatbot/widget-id/rotate",
    response_model=ChatbotConfigResponse,
)
async def rotate_chatbot_widget_id(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ChatbotConfigResponse:
    config = await _mutate(
        response,
        session,
        service.rotate_widget_public_id(
            session, business=access.business, actor_user_id=access.user.id
        ),
    )
    return service.config_response(config, access.business)


@router.get(
    "/businesses/{business_id}/chatbot/deployments",
    response_model=ChatbotDeploymentList,
)
async def read_chatbot_deployments(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ChatbotDeploymentList:
    return await _read(
        response,
        service.list_deployment_targets(session, business=access.business),
    )


@router.post(
    "/businesses/{business_id}/chatbot/deployments/hosted",
    response_model=ChatbotDeploymentTarget,
    status_code=status.HTTP_201_CREATED,
)
async def install_hosted_chatbot(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ChatbotDeploymentTarget:
    if isinstance(session, AsyncSession):
        await require_feature(session, business_id=access.business.id, key="website_chatbot")
    return await _mutate(
        response,
        session,
        service.install_hosted_deployment(
            session, business=access.business, actor_user_id=access.user.id
        ),
    )


@router.get(
    "/businesses/{business_id}/chatbot/analytics",
    response_model=ChatbotAnalyticsResponse,
)
async def read_chatbot_analytics(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    period_start: date | None = None,
    period_end: date | None = None,
) -> ChatbotAnalyticsResponse:
    end = period_end or datetime.now(UTC).date()
    start = period_start or (end - timedelta(days=29))
    return await _read(
        response,
        service.chatbot_analytics(
            session, business_id=access.business.id,
            period_start=start, period_end=end,
        ),
    )


@router.get(
    "/public/widgets/{widget_public_id}/config",
    response_model=PublicWidgetConfig,
)
async def read_public_widget_config(
    widget_public_id: WidgetPublicId,
    response: Response,
    session: SessionDependency,
    origin: OriginHeader = None,
    referer: RefererHeader = None,
) -> PublicWidgetConfig:
    value, response_origin = await _public_read(
        response,
        service.public_widget_config(
            session,
            widget_public_id=widget_public_id,
            origin=origin,
            referer=referer,
        ),
    )
    _set_public_cors(response, response_origin)
    return value


@router.get(
    "/public/hosted-widgets/{widget_public_id}/config",
    response_model=PublicWidgetConfig,
)
async def read_public_hosted_widget_config(
    widget_public_id: WidgetPublicId,
    response: Response,
    session: SessionDependency,
) -> PublicWidgetConfig:
    value, response_origin = await _public_read(
        response,
        service.public_widget_config(
            session,
            widget_public_id=widget_public_id,
            origin=None,
            referer=None,
            hosted=True,
        ),
    )
    _set_public_cors(response, response_origin)
    return value


@router.post(
    "/public/widgets/{widget_public_id}/sessions",
    response_model=PublicSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_widget_session(
    widget_public_id: WidgetPublicId,
    request: Request,
    response: Response,
    session: SessionDependency,
    origin: OriginHeader = None,
    referer: RefererHeader = None,
) -> PublicSessionResponse:
    value, response_origin = await _public_mutate(
        response,
        session,
        service.create_public_session(
            session,
            widget_public_id=widget_public_id,
            origin=origin,
            referer=referer,
            client_rate_identity=(request.client.host if request.client else "unknown"),
        ),
    )
    _set_public_cors(response, response_origin)
    return value


@router.post(
    "/public/hosted-widgets/{widget_public_id}/sessions",
    response_model=PublicSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_hosted_widget_session(
    widget_public_id: WidgetPublicId,
    request: Request,
    response: Response,
    session: SessionDependency,
) -> PublicSessionResponse:
    value, response_origin = await _public_mutate(
        response,
        session,
        service.create_public_session(
            session,
            widget_public_id=widget_public_id,
            origin=None,
            referer=None,
            client_rate_identity=(request.client.host if request.client else "unknown"),
            hosted=True,
        ),
    )
    _set_public_cors(response, response_origin)
    return value


@router.post(
    "/public/widgets/{widget_public_id}/sessions/messages",
    response_model=PublicChatMessageResponse,
)
async def send_widget_message(
    widget_public_id: WidgetPublicId,
    data: PublicChatMessageRequest,
    response: Response,
    session: SessionDependency,
    authorization: AuthorizationHeader = None,
) -> PublicChatMessageResponse:
    token = _session_token(authorization)
    _set_public_private(response)
    started_ns = perf_counter_ns()
    ai_path_committed = False
    public_business_id = None
    public_session_id = None
    execution_id = None
    try:
        prepared = await service.prepare_public_message(
            session,
            widget_public_id=widget_public_id,
            session_token=token,
            data=data,
        )
        if prepared.direct_response is not None:
            value = await service.complete_direct_response(session, prepared=prepared)
            await session.commit()
            return value

        try:
            provider = get_ai_agent_provider()
            validate_agent_provider(provider)
        except (HTTPException, TypeError, ValueError):
            await service.record_ai_failure(session, prepared=prepared)
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "provider_not_configured",
                    "message": "AI provider configuration required.",
                },
                headers=_NO_STORE_HEADERS,
            ) from None
        public_business_id = prepared.context.business.id
        public_session_id = prepared.context.session.id
        await session.commit()
        ai_path_committed = True
        execution = await create_running_ai_agent_execution(
            session,
            business_id=prepared.context.business.id,
            requested_by_user_id=None,
            role="support",
            task="Website visitor support request",
            provider_name=provider.provider_name,
            model_name=get_agent_provider_model_name(provider),
            trigger_type="website_widget",
        )
        execution_id = execution.id
        await session.commit()
        runtime = await service.run_public_ai(
            session, prepared=prepared, provider=provider
        )
        duration_ms = max(0, (perf_counter_ns() - started_ns) // 1_000_000)
        await finalize_successful_ai_agent_execution(
            session,
            business_id=prepared.context.business.id,
            execution_id=execution.id,
            result=runtime.execution_result,
            duration_ms=duration_ms,
            input_tokens=runtime.provider_metadata.input_tokens,
            output_tokens=runtime.provider_metadata.output_tokens,
            provider_request_id=runtime.provider_metadata.provider_request_id,
        )
        value = await service.complete_public_message(
            session,
            prepared=prepared,
            assistant_message=runtime.execution_result.output.summary,
            duration_ms=duration_ms,
        )
        await session.commit()
        return value
    except HTTPException:
        await _rollback(session)
        raise
    except (AIAgentError, AIAgentExecutionLedgerError):
        if ai_path_committed:
            await _record_public_ai_failure(
                session,
                business_id=public_business_id,
                public_session_id=public_session_id,
                execution_id=execution_id,
                started_ns=started_ns,
            )
        else:
            await _rollback(session)
        raise _ai_unavailable() from None
    except ChatbotError as exc:
        if ai_path_committed:
            await _record_public_ai_failure(
                session,
                business_id=public_business_id,
                public_session_id=public_session_id,
                execution_id=execution_id,
                started_ns=started_ns,
            )
            raise _ai_unavailable() from None
        await _rollback(session)
        raise _chatbot_http_error(exc) from None
    except (SQLAlchemyError, TypeError, ValueError):
        if ai_path_committed:
            await _record_public_ai_failure(
                session,
                business_id=public_business_id,
                public_session_id=public_session_id,
                execution_id=execution_id,
                started_ns=started_ns,
            )
            raise _ai_unavailable() from None
        await _rollback(session)
        raise _unavailable() from None


@router.post(
    "/public/widgets/{widget_public_id}/sessions/lead",
    response_model=PublicLeadCaptureResponse,
)
async def capture_widget_lead(
    widget_public_id: WidgetPublicId,
    data: PublicLeadCaptureRequest,
    response: Response,
    session: SessionDependency,
    authorization: AuthorizationHeader = None,
) -> PublicLeadCaptureResponse:
    return await _public_mutate(
        response,
        session,
        service.capture_public_lead(
            session,
            widget_public_id=widget_public_id,
            session_token=_session_token(authorization),
            data=data,
        ),
    )


@router.post(
    "/public/widgets/{widget_public_id}/sessions/handoff",
    response_model=PublicHandoffResponse,
)
async def request_widget_handoff(
    widget_public_id: WidgetPublicId,
    data: PublicHandoffRequest,
    response: Response,
    session: SessionDependency,
    authorization: AuthorizationHeader = None,
) -> PublicHandoffResponse:
    return await _public_mutate(
        response,
        session,
        service.request_public_handoff(
            session,
            widget_public_id=widget_public_id,
            session_token=_session_token(authorization),
            reason=data.reason,
        ),
    )


@router.post(
    "/public/widgets/{widget_public_id}/sessions/order-status",
    response_model=PublicOrderStatusResponse,
)
async def lookup_widget_order(
    widget_public_id: WidgetPublicId,
    data: PublicOrderLookupRequest,
    response: Response,
    session: SessionDependency,
    authorization: AuthorizationHeader = None,
) -> PublicOrderStatusResponse:
    return await _public_bounded_attempt_mutate(
        response,
        session,
        service.lookup_public_order(
            session,
            widget_public_id=widget_public_id,
            session_token=_session_token(authorization),
            data=data,
        ),
    )


@router.post(
    "/public/widgets/{widget_public_id}/sessions/availability",
    response_model=PublicAvailabilityResponse,
)
async def find_widget_availability(
    widget_public_id: WidgetPublicId,
    data: PublicAvailabilityRequest,
    response: Response,
    session: SessionDependency,
    authorization: AuthorizationHeader = None,
) -> PublicAvailabilityResponse:
    return await _public_read(
        response,
        service.public_availability(
            session,
            widget_public_id=widget_public_id,
            session_token=_session_token(authorization),
            data=data,
        ),
    )


@router.post(
    "/public/widgets/{widget_public_id}/sessions/appointments",
    response_model=PublicAppointmentBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def book_widget_appointment(
    widget_public_id: WidgetPublicId,
    data: PublicAppointmentBookingRequest,
    response: Response,
    session: SessionDependency,
    authorization: AuthorizationHeader = None,
) -> PublicAppointmentBookingResponse:
    return await _public_bounded_attempt_mutate(
        response,
        session,
        service.book_public_appointment(
            session,
            widget_public_id=widget_public_id,
            session_token=_session_token(authorization),
            data=data,
        ),
    )


async def _read(response: Response, operation: Awaitable[T]) -> T:
    _set_private(response)
    try:
        return await operation
    except ChatbotError as exc:
        raise _chatbot_http_error(exc) from None


async def _mutate(
    response: Response, session: AsyncSession, operation: Awaitable[T]
) -> T:
    _set_private(response)
    try:
        value = await operation
        await materialize_response_before_commit(session, value)
        await session.commit()
        return value
    except ChatbotError as exc:
        await _rollback(session)
        raise _chatbot_http_error(exc) from None
    except SQLAlchemyError:
        await _rollback(session)
        raise _unavailable() from None


async def _public_read(response: Response, operation: Awaitable[T]) -> T:
    _set_public_private(response)
    try:
        return await operation
    except ChatbotError as exc:
        raise _chatbot_http_error(exc) from None


async def _public_mutate(
    response: Response, session: AsyncSession, operation: Awaitable[T]
) -> T:
    _set_public_private(response)
    try:
        value = await operation
        await session.commit()
        return value
    except ChatbotError as exc:
        await _rollback(session)
        raise _chatbot_http_error(exc) from None
    except SQLAlchemyError:
        await _rollback(session)
        raise _unavailable() from None


async def _public_bounded_attempt_mutate(
    response: Response, session: AsyncSession, operation: Awaitable[T]
) -> T:
    """Persist bounded public attempts when an expected domain check fails."""
    _set_public_private(response)
    try:
        value = await operation
        await session.commit()
        return value
    except (
        ChatbotAuthorizationError,
        ChatbotConflictError,
        ChatbotNotFoundError,
        ChatbotValidationError,
    ) as exc:
        try:
            await session.commit()
        except SQLAlchemyError:
            await _rollback(session)
            raise _unavailable() from None
        raise _chatbot_http_error(exc) from None
    except ChatbotError as exc:
        await _rollback(session)
        raise _chatbot_http_error(exc) from None
    except SQLAlchemyError:
        await _rollback(session)
        raise _unavailable() from None


async def _record_public_ai_failure(
    session: AsyncSession,
    *,
    business_id: UUID | None,
    public_session_id: UUID | None,
    execution_id: UUID | None,
    started_ns: int,
) -> None:
    await _rollback(session)
    if business_id is None or public_session_id is None:
        return
    try:
        await service.record_ai_failure_by_id(
            session,
            business_id=business_id,
            public_session_id=public_session_id,
        )
        if execution_id is not None:
            await fail_ai_agent_execution(
                session,
                business_id=business_id,
                execution_id=execution_id,
                failure_code="public_ai_unavailable",
                duration_ms=max(0, (perf_counter_ns() - started_ns) // 1_000_000),
            )
        await session.commit()
    except Exception:
        await _rollback(session)


async def _rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except Exception:
        return


def _session_token(authorization: str | None) -> str:
    if authorization is None:
        raise _auth_error()
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not token.strip():
        raise _auth_error()
    token = token.strip()
    if not 48 <= len(token) <= 128 or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for character in token
    ):
        raise _auth_error()
    return token


def _set_private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _set_public_private(response: Response) -> None:
    _set_private(response)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"


def _set_public_cors(response: Response, response_origin: str) -> None:
    response.headers["Access-Control-Allow-Origin"] = response_origin
    response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"


def _chatbot_http_error(exc: ChatbotError) -> HTTPException:
    if isinstance(exc, (ChatbotNotFoundError, ChatbotDisabledError)):
        return HTTPException(status.HTTP_404_NOT_FOUND, "Website chatbot not found.", headers=_NO_STORE_HEADERS)
    if isinstance(exc, (ChatbotOriginError, ChatbotAuthorizationError)):
        return HTTPException(status.HTTP_403_FORBIDDEN, "Public chatbot access is unavailable.", headers=_NO_STORE_HEADERS)
    if isinstance(exc, ChatbotRateLimitError):
        return HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests. Please try again later.",
            headers={**_NO_STORE_HEADERS, "Retry-After": "60"},
        )
    if isinstance(exc, ChatbotConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, "The request could not be completed.", headers=_NO_STORE_HEADERS)
    if isinstance(exc, ChatbotValidationError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The request is invalid.", headers=_NO_STORE_HEADERS)
    return _unavailable()


def _auth_error() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "A valid widget session is required.",
        headers={**_NO_STORE_HEADERS, "WWW-Authenticate": "Bearer"},
    )


def _unavailable() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "The website chatbot is temporarily unavailable.",
        headers=_NO_STORE_HEADERS,
    )


def _ai_unavailable() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "The assistant is temporarily unavailable.",
        headers=_NO_STORE_HEADERS,
    )


_NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
