from __future__ import annotations

from datetime import datetime
from typing import Annotated, Awaitable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency
from app.api.response_materialization import materialize_response_before_commit
from app.db.session import get_db_session
from app.exceptions.scheduling import (
    SchedulingConflictError,
    SchedulingNotFoundError,
    SchedulingPersistenceError,
    SchedulingStateError,
    SchedulingValidationError,
)
from app.exceptions.automation_intelligence import (
    AutomationIntelligencePersistenceError,
)
from app.models.appointment import Appointment
from app.models.appointment_type import AppointmentType
from app.models.customer import Customer
from app.models.provider_appointment_type import ProviderAppointmentType
from app.models.provider_availability_exception import ProviderAvailabilityException
from app.models.provider_availability_rule import ProviderAvailabilityRule
from app.models.service_provider import ServiceProvider
from app.models.notification import Notification
from app.services.billing import require_feature
from app.schemas.scheduling import (
    AppointmentCancelRequest,
    AppointmentCreate,
    AppointmentResponse,
    AppointmentRescheduleRequest,
    AppointmentStatus,
    AppointmentTypeCreate,
    AppointmentTypeResponse,
    AppointmentTypeUpdate,
    AvailabilityExceptionCreate,
    AvailabilityExceptionResponse,
    AvailabilityExceptionUpdate,
    AvailabilityResponse,
    AvailabilityRuleCreate,
    AvailabilityRuleResponse,
    AvailabilityRuleUpdate,
    AvailabilitySearchRequest,
    CustomerCreate,
    CustomerResponse,
    NextAvailabilitySearchRequest,
    ProviderAppointmentTypeResponse,
    ServiceProviderCreate,
    ServiceProviderResponse,
    ServiceProviderUpdate,
)
from app.services.scheduling import (
    book_appointment,
    cancel_appointment,
    find_available_slots,
    find_next_available_slots,
    get_appointment,
    list_appointments,
    reschedule_appointment,
)
from app.services.scheduling_management import (
    assign_provider_appointment_type,
    create_appointment_type,
    create_availability_exception,
    create_availability_rule,
    create_customer,
    create_service_provider,
    delete_availability_exception,
    delete_availability_rule,
    get_appointment_type,
    get_service_provider,
    list_appointment_types,
    list_availability_exceptions,
    list_availability_rules,
    list_customers,
    list_provider_appointment_types,
    list_service_providers,
    unassign_provider_appointment_type,
    update_appointment_type,
    update_availability_exception,
    update_availability_rule,
    update_service_provider,
)
from app.services.operations import record_audit
from app.services.automation_intelligence import schedule_competitor_discovery


router = APIRouter(
    prefix="/businesses/{business_id}/scheduling",
    tags=["Scheduling"],
)
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/providers", response_model=list[ServiceProviderResponse])
async def read_providers(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    active: Annotated[bool | None, Query()] = None,
) -> list[ServiceProvider]:
    return await _read(
        response,
        list_service_providers(session, business_id=access.business.id, active=active),
    )


@router.post(
    "/providers",
    response_model=ServiceProviderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider(
    data: ServiceProviderCreate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ServiceProvider:
    return await _mutate(
        response,
        session,
        _audited(
            session,
            create_service_provider(session, business_id=access.business.id, data=data),
            business_id=access.business.id,
            actor_user_id=access.user.id,
            event_type="scheduling.provider_created",
            entity_type="service_provider",
            summary="Created a scheduling provider.",
        ),
    )


@router.get("/providers/{provider_id}", response_model=ServiceProviderResponse)
async def read_provider(
    provider_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ServiceProvider:
    return await _read(
        response,
        get_service_provider(
            session, business_id=access.business.id, provider_id=provider_id
        ),
    )


@router.patch("/providers/{provider_id}", response_model=ServiceProviderResponse)
async def patch_provider(
    provider_id: UUID,
    data: ServiceProviderUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ServiceProvider:
    return await _mutate(
        response,
        session,
        _audited(
            session,
            update_service_provider(
                session,
                business_id=access.business.id,
                provider_id=provider_id,
                data=data,
            ),
            business_id=access.business.id,
            actor_user_id=access.user.id,
            event_type="scheduling.provider_updated",
            entity_type="service_provider",
            summary="Updated scheduling provider settings.",
        ),
    )


@router.get("/appointment-types", response_model=list[AppointmentTypeResponse])
async def read_appointment_types(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    active: Annotated[bool | None, Query()] = None,
) -> list[AppointmentType]:
    return await _read(
        response,
        list_appointment_types(session, business_id=access.business.id, active=active),
    )


@router.post(
    "/appointment-types",
    response_model=AppointmentTypeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_type(
    data: AppointmentTypeCreate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> AppointmentType:
    async def operation() -> AppointmentType:
        appointment_type = await create_appointment_type(
            session, business_id=access.business.id, data=data
        )
        if isinstance(session, AsyncSession):
            await schedule_competitor_discovery(
                session,
                business_id=access.business.id,
                trigger_type="brain_change",
            )
        return appointment_type

    return await _mutate(
        response,
        session,
        operation(),
    )


@router.get(
    "/appointment-types/{appointment_type_id}",
    response_model=AppointmentTypeResponse,
)
async def read_appointment_type(
    appointment_type_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> AppointmentType:
    return await _read(
        response,
        get_appointment_type(
            session,
            business_id=access.business.id,
            appointment_type_id=appointment_type_id,
        ),
    )


@router.patch(
    "/appointment-types/{appointment_type_id}",
    response_model=AppointmentTypeResponse,
)
async def patch_appointment_type(
    appointment_type_id: UUID,
    data: AppointmentTypeUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> AppointmentType:
    async def operation() -> AppointmentType:
        appointment_type = await update_appointment_type(
            session,
            business_id=access.business.id,
            appointment_type_id=appointment_type_id,
            data=data,
        )
        if isinstance(session, AsyncSession):
            await schedule_competitor_discovery(
                session,
                business_id=access.business.id,
                trigger_type="brain_change",
            )
        return appointment_type

    return await _mutate(
        response,
        session,
        operation(),
    )


@router.get("/customers", response_model=list[CustomerResponse])
async def read_customers(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> list[Customer]:
    return await _read(response, list_customers(session, business_id=access.business.id))


@router.post(
    "/customers", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED
)
async def create_scheduling_customer(
    data: CustomerCreate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> Customer:
    return await _mutate(
        response,
        session,
        create_customer(session, business_id=access.business.id, data=data),
    )


@router.get(
    "/providers/{provider_id}/appointment-types",
    response_model=list[ProviderAppointmentTypeResponse],
)
async def read_provider_appointment_types(
    provider_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> list[ProviderAppointmentType]:
    return await _read(
        response,
        list_provider_appointment_types(
            session, business_id=access.business.id, provider_id=provider_id
        ),
    )


@router.put(
    "/providers/{provider_id}/appointment-types/{appointment_type_id}",
    response_model=ProviderAppointmentTypeResponse,
)
async def assign_type_to_provider(
    provider_id: UUID,
    appointment_type_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ProviderAppointmentType:
    return await _mutate(
        response,
        session,
        assign_provider_appointment_type(
            session,
            business_id=access.business.id,
            provider_id=provider_id,
            appointment_type_id=appointment_type_id,
        ),
    )


@router.delete(
    "/providers/{provider_id}/appointment-types/{appointment_type_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unassign_type_from_provider(
    provider_id: UUID,
    appointment_type_id: UUID,
    access: BusinessAccessDependency,
    session: SessionDependency,
) -> Response:
    await _mutate(
        None,
        session,
        unassign_provider_appointment_type(
            session,
            business_id=access.business.id,
            provider_id=provider_id,
            appointment_type_id=appointment_type_id,
        ),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_PRIVATE_HEADERS)


@router.get(
    "/providers/{provider_id}/availability-rules",
    response_model=list[AvailabilityRuleResponse],
)
async def read_rules(
    provider_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> list[ProviderAvailabilityRule]:
    return await _read(
        response,
        list_availability_rules(
            session, business_id=access.business.id, provider_id=provider_id
        ),
    )


@router.post(
    "/providers/{provider_id}/availability-rules",
    response_model=AvailabilityRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_rule(
    provider_id: UUID,
    data: AvailabilityRuleCreate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ProviderAvailabilityRule:
    return await _mutate(
        response,
        session,
        create_availability_rule(
            session,
            business_id=access.business.id,
            provider_id=provider_id,
            data=data,
        ),
    )


@router.patch("/availability-rules/{rule_id}", response_model=AvailabilityRuleResponse)
async def patch_rule(
    rule_id: UUID,
    data: AvailabilityRuleUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ProviderAvailabilityRule:
    return await _mutate(
        response,
        session,
        update_availability_rule(
            session, business_id=access.business.id, rule_id=rule_id, data=data
        ),
    )


@router.delete("/availability-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: UUID,
    access: BusinessAccessDependency,
    session: SessionDependency,
) -> Response:
    await _mutate(
        None,
        session,
        delete_availability_rule(
            session, business_id=access.business.id, rule_id=rule_id
        ),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_PRIVATE_HEADERS)


@router.get(
    "/providers/{provider_id}/availability-exceptions",
    response_model=list[AvailabilityExceptionResponse],
)
async def read_exceptions(
    provider_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> list[ProviderAvailabilityException]:
    return await _read(
        response,
        list_availability_exceptions(
            session, business_id=access.business.id, provider_id=provider_id
        ),
    )


@router.post(
    "/providers/{provider_id}/availability-exceptions",
    response_model=AvailabilityExceptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_exception(
    provider_id: UUID,
    data: AvailabilityExceptionCreate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ProviderAvailabilityException:
    return await _mutate(
        response,
        session,
        create_availability_exception(
            session,
            business_id=access.business.id,
            provider_id=provider_id,
            data=data,
        ),
    )


@router.patch(
    "/availability-exceptions/{exception_id}",
    response_model=AvailabilityExceptionResponse,
)
async def patch_exception(
    exception_id: UUID,
    data: AvailabilityExceptionUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ProviderAvailabilityException:
    return await _mutate(
        response,
        session,
        update_availability_exception(
            session,
            business_id=access.business.id,
            exception_id=exception_id,
            data=data,
        ),
    )


@router.delete(
    "/availability-exceptions/{exception_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_exception(
    exception_id: UUID,
    access: BusinessAccessDependency,
    session: SessionDependency,
) -> Response:
    await _mutate(
        None,
        session,
        delete_availability_exception(
            session, business_id=access.business.id, exception_id=exception_id
        ),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_PRIVATE_HEADERS)


@router.post("/availability/search", response_model=AvailabilityResponse)
async def search_availability(
    request: AvailabilitySearchRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> AvailabilityResponse:
    slots = await _read(
        response,
        find_available_slots(
            session,
            business_id=access.business.id,
            **request.model_dump(),
        ),
    )
    return AvailabilityResponse(slots=slots)


@router.post("/availability/next", response_model=AvailabilityResponse)
async def search_next_availability(
    request: NextAvailabilitySearchRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> AvailabilityResponse:
    slots = await _read(
        response,
        find_next_available_slots(
            session,
            business_id=access.business.id,
            **request.model_dump(),
        ),
    )
    return AvailabilityResponse(slots=slots)


@router.get("/appointments", response_model=list[AppointmentResponse])
async def read_appointments(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    window_start: Annotated[datetime | None, Query()] = None,
    window_end: Annotated[datetime | None, Query()] = None,
    provider_id: Annotated[UUID | None, Query()] = None,
    appointment_status: Annotated[AppointmentStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> list[Appointment]:
    return await _read(
        response,
        list_appointments(
            session,
            business_id=access.business.id,
            window_start=window_start,
            window_end=window_end,
            provider_id=provider_id,
            status=appointment_status,
            limit=limit,
            offset=offset,
        ),
    )


@router.post(
    "/appointments",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_appointment(
    data: AppointmentCreate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> Appointment:
    return await _mutate(
        response,
        session,
        _audited(
            session,
            book_appointment(
                session,
                business_id=access.business.id,
                created_by_user_id=access.user.id,
                **data.model_dump(),
            ),
            business_id=access.business.id,
            actor_user_id=access.user.id,
            event_type="scheduling.appointment_created",
            entity_type="appointment",
            summary="Created an appointment.",
            notification=("Appointment created", "A new appointment was added to the schedule."),
        ),
    )


@router.get("/appointments/{appointment_id}", response_model=AppointmentResponse)
async def read_appointment(
    appointment_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> Appointment:
    return await _read(
        response,
        get_appointment(
            session, business_id=access.business.id, appointment_id=appointment_id
        ),
    )


@router.post(
    "/appointments/{appointment_id}/reschedule",
    response_model=AppointmentResponse,
)
async def reschedule_existing_appointment(
    appointment_id: UUID,
    data: AppointmentRescheduleRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> Appointment:
    return await _mutate(
        response,
        session,
        _audited(
            session,
            reschedule_appointment(
                session,
                business_id=access.business.id,
                appointment_id=appointment_id,
                starts_at=data.starts_at,
            ),
            business_id=access.business.id,
            actor_user_id=access.user.id,
            event_type="scheduling.appointment_rescheduled",
            entity_type="appointment",
            summary="Rescheduled an appointment.",
            notification=("Appointment rescheduled", "An appointment time was changed."),
        ),
    )


@router.post(
    "/appointments/{appointment_id}/cancel",
    response_model=AppointmentResponse,
)
async def cancel_existing_appointment(
    appointment_id: UUID,
    data: AppointmentCancelRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> Appointment:
    return await _mutate(
        response,
        session,
        _audited(
            session,
            cancel_appointment(
                session,
                business_id=access.business.id,
                appointment_id=appointment_id,
                reason_code=data.reason_code,
            ),
            business_id=access.business.id,
            actor_user_id=access.user.id,
            event_type="scheduling.appointment_canceled",
            entity_type="appointment",
            summary="Canceled an appointment.",
            notification=("Appointment canceled", "An appointment was canceled."),
        ),
    )


async def _audited(
    session: AsyncSession,
    operation: Awaitable,
    *,
    business_id: UUID,
    actor_user_id: UUID,
    event_type: str,
    entity_type: str,
    summary: str,
    notification: tuple[str, str] | None = None,
):
    if isinstance(session, AsyncSession):
        await require_feature(session, business_id=business_id, key="scheduling")
    value = await operation
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=value.id,
        summary=summary,
    )
    if notification is not None:
        title, message = notification
        session.add(
            Notification(
                business_id=business_id,
                recipient_user_id=actor_user_id,
                category="scheduling",
                title=title,
                message=message,
                priority="medium",
                related_entity_type="appointment",
                related_entity_id=value.id,
            )
        )
    return value


async def _read(response: Response, operation: Awaitable):
    try:
        value = await operation
    except SchedulingNotFoundError:
        raise _not_found() from None
    except SchedulingValidationError:
        raise _invalid() from None
    except (SchedulingConflictError, SchedulingStateError):
        raise _conflict() from None
    except SchedulingPersistenceError:
        raise _unavailable() from None
    _set_private(response)
    return value


async def _mutate(
    response: Response | None, session: AsyncSession, operation: Awaitable
):
    try:
        value = await operation
        await materialize_response_before_commit(session, value)
        await session.commit()
    except SchedulingNotFoundError:
        await _rollback(session)
        raise _not_found() from None
    except SchedulingValidationError:
        await _rollback(session)
        raise _invalid() from None
    except (SchedulingConflictError, SchedulingStateError):
        await _rollback(session)
        raise _conflict() from None
    except (
        SchedulingPersistenceError,
        AutomationIntelligencePersistenceError,
        SQLAlchemyError,
    ):
        await _rollback(session)
        raise _unavailable() from None
    if response is not None:
        _set_private(response)
    return value


async def _rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except SQLAlchemyError:
        return


def _set_private(response: Response) -> None:
    for name, value in _PRIVATE_HEADERS.items():
        response.headers[name] = value


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Scheduling resource not found.", headers=_PRIVATE_HEADERS)


def _invalid() -> HTTPException:
    return HTTPException(status_code=422, detail="Invalid scheduling request.", headers=_PRIVATE_HEADERS)


def _conflict() -> HTTPException:
    return HTTPException(status_code=409, detail="Scheduling request conflicts with current availability.", headers=_PRIVATE_HEADERS)


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Scheduling is temporarily unavailable.", headers=_PRIVATE_HEADERS)


_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
