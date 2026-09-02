from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Awaitable, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    AuditLogResponse,
    ConversationCreate,
    ConversationControlRequest,
    ConversationResponse,
    ConversationSendRequest,
    ConversationUpdate,
    CoreAnalyticsResponse,
    ConversationStatus,
    CustomerCreate,
    CustomerResponse,
    CustomerUpdate,
    CustomerStatus,
    LeadCreate,
    LeadQualificationUpdate,
    LeadResponse,
    LeadStageUpdate,
    LeadUpdate,
    LeadStage,
    MessageCreate,
    MessageResponse,
    NotificationCreate,
    NotificationResponse,
    OpportunityCreate,
    OpportunityResponse,
    OpportunityStatusUpdate,
    OpportunityUpdate,
    OpportunityStatus,
    OrderCreate,
    OrderResponse,
    OrderStatusUpdate,
    OrderStatus,
    PageResponse,
    ReportGenerateRequest,
    ReportResponse,
)
from app.services import operations as service
from app.services.billing import require_feature


router = APIRouter(prefix="/businesses/{business_id}", tags=["Business Operations"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Search = Annotated[str | None, Query(max_length=100)]


def _page(items, total: int, page: int, page_size: int):
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/customers", response_model=PageResponse[CustomerResponse])
async def read_customers(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, search: Search = None, customer_status: Annotated[CustomerStatus | None, Query(alias="status")] = None):
    items, total = await _read(response, service.list_customers(session, business_id=access.business.id, page=page, page_size=page_size, search=search, status=customer_status))
    return _page(items, total, page, page_size)


@router.post("/customers", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(data: CustomerCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.create_customer(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


@router.get("/customers/{customer_id}", response_model=CustomerResponse)
async def read_customer(customer_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _read(response, service.get_customer(session, business_id=access.business.id, customer_id=customer_id))


@router.patch("/customers/{customer_id}", response_model=CustomerResponse)
async def patch_customer(customer_id: UUID, data: CustomerUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.update_customer(session, business_id=access.business.id, customer_id=customer_id, actor_user_id=access.user.id, data=data))


@router.get("/crm/leads", response_model=PageResponse[LeadResponse])
async def read_leads(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, search: Search = None, stage: LeadStage | None = None):
    items, total = await _read(response, service.list_leads(session, business_id=access.business.id, page=page, page_size=page_size, search=search, stage=stage))
    return _page(items, total, page, page_size)


@router.post("/crm/leads", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def create_lead(data: LeadCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.create_lead(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


@router.get("/crm/leads/{lead_id}", response_model=LeadResponse)
async def read_lead(lead_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _read(response, service.get_lead(session, business_id=access.business.id, lead_id=lead_id))


@router.patch("/crm/leads/{lead_id}", response_model=LeadResponse)
async def patch_lead(lead_id: UUID, data: LeadUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.update_lead(session, business_id=access.business.id, lead_id=lead_id, actor_user_id=access.user.id, data=data))


@router.post("/crm/leads/{lead_id}/stage", response_model=LeadResponse)
async def change_lead_stage(lead_id: UUID, data: LeadStageUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.change_lead_state(session, business_id=access.business.id, lead_id=lead_id, actor_user_id=access.user.id, field="stage", value=data.stage))


@router.post("/crm/leads/{lead_id}/qualification", response_model=LeadResponse)
async def change_lead_qualification(lead_id: UUID, data: LeadQualificationUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.change_lead_state(session, business_id=access.business.id, lead_id=lead_id, actor_user_id=access.user.id, field="qualification_state", value=data.qualification_state))


@router.get("/orders", response_model=PageResponse[OrderResponse])
async def read_orders(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, search: Search = None, order_status: Annotated[OrderStatus | None, Query(alias="status")] = None):
    orders, total = await _read(response, service.list_orders(session, business_id=access.business.id, page=page, page_size=page_size, search=search, status=order_status))
    items = await _read(response, service.order_responses(session, orders))
    return _page(items, total, page, page_size)


@router.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(data: OrderCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    order = await _mutate(None, session, service.create_order(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))
    return await _read(response, service.order_response(session, order))


@router.get("/orders/{order_id}", response_model=OrderResponse)
async def read_order(order_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    order = await _read(response, service.get_order(session, business_id=access.business.id, order_id=order_id))
    return await _read(response, service.order_response(session, order))


@router.post("/orders/{order_id}/status", response_model=OrderResponse)
async def change_order_status(order_id: UUID, data: OrderStatusUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    order = await _mutate(None, session, service.change_order_status(session, business_id=access.business.id, order_id=order_id, actor_user_id=access.user.id, status=data.status))
    return await _read(response, service.order_response(session, order))


@router.get("/conversations", response_model=PageResponse[ConversationResponse])
async def read_conversations(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, search: Search = None, conversation_status: Annotated[ConversationStatus | None, Query(alias="status")] = None, channel: Annotated[str | None, Query(max_length=24, pattern=r"^(website|whatsapp|email|facebook|instagram|manual|other)$")] = None):
    records, total = await _read(response, service.list_conversations(session, business_id=access.business.id, page=page, page_size=page_size, search=search, status=conversation_status, channel=channel))
    items = await _read(response, service.conversation_responses(session, records))
    return _page(items, total, page, page_size)


@router.post("/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(data: ConversationCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    record = await _mutate(None, session, service.create_conversation(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))
    return await _read(response, service.conversation_response(session, record))


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def read_conversation(conversation_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    record = await _read(response, service.get_conversation(session, business_id=access.business.id, conversation_id=conversation_id))
    return await _read(response, service.conversation_response(session, record))


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def patch_conversation(conversation_id: UUID, data: ConversationUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    record = await _mutate(None, session, service.update_conversation(session, business_id=access.business.id, conversation_id=conversation_id, actor_user_id=access.user.id, data=data))
    return await _read(response, service.conversation_response(session, record))


@router.post("/conversations/{conversation_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def record_message(conversation_id: UUID, data: MessageCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.add_message(session, business_id=access.business.id, conversation_id=conversation_id, actor_user_id=access.user.id, data=data))


@router.post("/conversations/{conversation_id}/send", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(conversation_id: UUID, data: ConversationSendRequest, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(
        response,
        session,
        service.send_conversation_message(
            session,
            business_id=access.business.id,
            conversation_id=conversation_id,
            actor_user_id=access.user.id,
            content=data.content,
            client_request_id=data.client_request_id,
        ),
    )


@router.post("/conversations/{conversation_id}/control", response_model=ConversationResponse)
async def control_conversation(conversation_id: UUID, data: ConversationControlRequest, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    record = await _mutate(None, session, service.control_conversation(session, business_id=access.business.id, conversation_id=conversation_id, actor_user_id=access.user.id, action=data.action, reason=data.reason))
    return await _read(response, service.conversation_response(session, record))


@router.post("/conversations/{conversation_id}/read", response_model=ConversationResponse)
async def mark_conversation_read(conversation_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    record = await _mutate(None, session, service.mark_conversation_read(session, business_id=access.business.id, conversation_id=conversation_id, actor_user_id=access.user.id))
    return await _read(response, service.conversation_response(session, record))


@router.get("/notifications", response_model=PageResponse[NotificationResponse])
async def read_notifications(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, unread_only: bool = False):
    items, total = await _read(response, service.list_notifications(session, business_id=access.business.id, user_id=access.user.id, page=page, page_size=page_size, unread_only=unread_only))
    return _page(items, total, page, page_size)


@router.post("/notifications", response_model=NotificationResponse, status_code=status.HTTP_201_CREATED)
async def create_notification(data: NotificationCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.create_notification(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


@router.post("/notifications/read-all")
async def mark_all_notifications_read(access: BusinessAccessDependency, response: Response, session: SessionDependency):
    count = await _mutate(response, session, service.mark_all_notifications_read(session, business_id=access.business.id, user_id=access.user.id))
    return {"updated": count}


@router.post("/notifications/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(notification_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.mark_notification_read(session, business_id=access.business.id, notification_id=notification_id, user_id=access.user.id))


@router.get("/opportunities", response_model=PageResponse[OpportunityResponse])
async def read_opportunities(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, search: Search = None, opportunity_status: Annotated[OpportunityStatus | None, Query(alias="status")] = None):
    items, total = await _read(response, service.list_opportunities(session, business_id=access.business.id, page=page, page_size=page_size, search=search, status=opportunity_status))
    return _page(items, total, page, page_size)


@router.post("/opportunities", response_model=OpportunityResponse, status_code=status.HTTP_201_CREATED)
async def create_opportunity(data: OpportunityCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.create_opportunity(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityResponse)
async def read_opportunity(opportunity_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _read(response, service.get_opportunity(session, business_id=access.business.id, opportunity_id=opportunity_id))


@router.patch("/opportunities/{opportunity_id}", response_model=OpportunityResponse)
async def patch_opportunity(opportunity_id: UUID, data: OpportunityUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.update_opportunity(session, business_id=access.business.id, opportunity_id=opportunity_id, actor_user_id=access.user.id, data=data))


@router.post("/opportunities/{opportunity_id}/status", response_model=OpportunityResponse)
async def change_opportunity_status(opportunity_id: UUID, data: OpportunityStatusUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.change_opportunity_status(session, business_id=access.business.id, opportunity_id=opportunity_id, actor_user_id=access.user.id, status=data.status))


@router.get("/audit", response_model=PageResponse[AuditLogResponse])
async def read_audit_log(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, search: Search = None, event_type: Annotated[str | None, Query(max_length=80, pattern=r"^[a-z][a-z0-9_.]{0,79}$")] = None):
    items, total = await _read(response, service.list_audit_logs(session, business_id=access.business.id, page=page, page_size=page_size, search=search, event_type=event_type))
    return _page(items, total, page, page_size)


@router.get("/analytics/core", response_model=CoreAnalyticsResponse)
async def read_core_analytics(access: BusinessAccessDependency, response: Response, session: SessionDependency, period_start: date | None = None, period_end: date | None = None):
    try:
        business_today = datetime.now(ZoneInfo(getattr(access.business, "timezone", "UTC"))).date()
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        business_today = datetime.now(timezone.utc).date()
    end = period_end or business_today
    start = period_start or end - timedelta(days=29)
    return await _read(response, service.core_analytics(session, business_id=access.business.id, period_start=start, period_end=end))


@router.get("/reports", response_model=PageResponse[ReportResponse])
async def read_reports(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, report_type: Literal["daily_operations", "sales", "customer", "scheduling", "marketing"] | None = None):
    items, total = await _read(response, service.list_reports(session, business_id=access.business.id, page=page, page_size=page_size, report_type=report_type))
    return _page(items, total, page, page_size)


@router.post("/reports/generate", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
async def generate_report(data: ReportGenerateRequest, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    if isinstance(session, AsyncSession):
        await require_feature(session, business_id=access.business.id, key="reports")
    return await _mutate(response, session, service.generate_report(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


async def _read(response: Response, operation: Awaitable):
    try:
        value = await operation
    except OperationsNotFoundError:
        raise _not_found() from None
    except OperationsValidationError:
        raise _invalid() from None
    except (OperationsConflictError, OperationsStateError):
        raise _conflict() from None
    except OperationsPersistenceError:
        raise _unavailable() from None
    _set_private(response)
    return value


async def _mutate(response: Response | None, session: AsyncSession, operation: Awaitable):
    try:
        value = await operation
        await materialize_response_before_commit(session, value)
        await session.commit()
    except OperationsNotFoundError:
        await _rollback(session)
        raise _not_found() from None
    except OperationsValidationError:
        await _rollback(session)
        raise _invalid() from None
    except (OperationsConflictError, OperationsStateError):
        await _rollback(session)
        raise _conflict() from None
    except (OperationsPersistenceError, SQLAlchemyError):
        await _rollback(session)
        raise _unavailable() from None
    if response is not None:
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


def _not_found() -> HTTPException:
    return HTTPException(404, "Business resource not found.", headers=_PRIVATE_HEADERS)


def _invalid() -> HTTPException:
    return HTTPException(422, "Invalid business operations request.", headers=_PRIVATE_HEADERS)


def _conflict() -> HTTPException:
    return HTTPException(409, "Request conflicts with the current resource state.", headers=_PRIVATE_HEADERS)


def _unavailable() -> HTTPException:
    return HTTPException(503, "Business operations are temporarily unavailable.", headers=_PRIVATE_HEADERS)


_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
