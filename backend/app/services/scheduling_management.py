from __future__ import annotations

from typing import Final, TypeVar
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.scheduling import (
    SchedulingConflictError,
    SchedulingNotFoundError,
    SchedulingPersistenceError,
    SchedulingValidationError,
)
from app.models.appointment_type import AppointmentType
from app.models.customer import Customer
from app.models.provider_appointment_type import ProviderAppointmentType
from app.models.provider_availability_exception import ProviderAvailabilityException
from app.models.provider_availability_rule import ProviderAvailabilityRule
from app.models.service_provider import ServiceProvider
from app.schemas.scheduling import (
    AppointmentTypeCreate,
    AppointmentTypeUpdate,
    AvailabilityExceptionCreate,
    AvailabilityExceptionUpdate,
    AvailabilityRuleCreate,
    AvailabilityRuleUpdate,
    CustomerCreate,
    ServiceProviderCreate,
    ServiceProviderUpdate,
)


_PERSISTENCE_MESSAGE: Final = "Scheduling data is temporarily unavailable"
T = TypeVar("T")


async def list_service_providers(
    session: AsyncSession, *, business_id: UUID, active: bool | None = None
) -> list[ServiceProvider]:
    statement = select(ServiceProvider).where(ServiceProvider.business_id == business_id)
    if active is not None:
        statement = statement.where(ServiceProvider.active == active)
    return await _list(session, statement.order_by(ServiceProvider.display_name, ServiceProvider.id), ServiceProvider)


async def get_service_provider(
    session: AsyncSession, *, business_id: UUID, provider_id: UUID, for_update: bool = False
) -> ServiceProvider:
    statement = select(ServiceProvider).where(
        ServiceProvider.business_id == business_id,
        ServiceProvider.id == provider_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return await _one(session, statement, ServiceProvider, "Service provider not found")


async def create_service_provider(
    session: AsyncSession, *, business_id: UUID, data: ServiceProviderCreate
) -> ServiceProvider:
    provider = ServiceProvider(business_id=business_id, **data.model_dump())
    session.add(provider)
    await _flush(session)
    return provider


async def update_service_provider(
    session: AsyncSession, *, business_id: UUID, provider_id: UUID, data: ServiceProviderUpdate
) -> ServiceProvider:
    provider = await get_service_provider(
        session, business_id=business_id, provider_id=provider_id, for_update=True
    )
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(provider, field, value)
    await _flush(session, refresh=provider)
    return provider


async def list_appointment_types(
    session: AsyncSession, *, business_id: UUID, active: bool | None = None
) -> list[AppointmentType]:
    statement = select(AppointmentType).where(AppointmentType.business_id == business_id)
    if active is not None:
        statement = statement.where(AppointmentType.active == active)
    return await _list(session, statement.order_by(AppointmentType.name, AppointmentType.id), AppointmentType)


async def get_appointment_type(
    session: AsyncSession, *, business_id: UUID, appointment_type_id: UUID, for_update: bool = False
) -> AppointmentType:
    statement = select(AppointmentType).where(
        AppointmentType.business_id == business_id,
        AppointmentType.id == appointment_type_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return await _one(session, statement, AppointmentType, "Appointment type not found")


async def create_appointment_type(
    session: AsyncSession, *, business_id: UUID, data: AppointmentTypeCreate
) -> AppointmentType:
    appointment_type = AppointmentType(business_id=business_id, **data.model_dump())
    session.add(appointment_type)
    await _flush(session)
    return appointment_type


async def update_appointment_type(
    session: AsyncSession,
    *,
    business_id: UUID,
    appointment_type_id: UUID,
    data: AppointmentTypeUpdate,
) -> AppointmentType:
    appointment_type = await get_appointment_type(
        session,
        business_id=business_id,
        appointment_type_id=appointment_type_id,
        for_update=True,
    )
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(appointment_type, field, value)
    await _flush(session, refresh=appointment_type)
    return appointment_type


async def list_customers(session: AsyncSession, *, business_id: UUID) -> list[Customer]:
    statement = select(Customer).where(Customer.business_id == business_id).order_by(Customer.display_name, Customer.id)
    return await _list(session, statement, Customer)


async def create_customer(
    session: AsyncSession, *, business_id: UUID, data: CustomerCreate
) -> Customer:
    customer = Customer(business_id=business_id, **data.model_dump())
    session.add(customer)
    await _flush(session)
    return customer


async def assign_provider_appointment_type(
    session: AsyncSession,
    *,
    business_id: UUID,
    provider_id: UUID,
    appointment_type_id: UUID,
) -> ProviderAppointmentType:
    await get_service_provider(session, business_id=business_id, provider_id=provider_id)
    await get_appointment_type(
        session, business_id=business_id, appointment_type_id=appointment_type_id
    )
    existing = await _optional(
        session,
        select(ProviderAppointmentType).where(
            ProviderAppointmentType.business_id == business_id,
            ProviderAppointmentType.provider_id == provider_id,
            ProviderAppointmentType.appointment_type_id == appointment_type_id,
        ),
        ProviderAppointmentType,
    )
    if existing is not None:
        return existing
    assignment = ProviderAppointmentType(
        business_id=business_id,
        provider_id=provider_id,
        appointment_type_id=appointment_type_id,
    )
    session.add(assignment)
    await _flush(session, conflict="Provider already supports this appointment type")
    return assignment


async def list_provider_appointment_types(
    session: AsyncSession, *, business_id: UUID, provider_id: UUID
) -> list[ProviderAppointmentType]:
    await get_service_provider(session, business_id=business_id, provider_id=provider_id)
    statement = select(ProviderAppointmentType).where(
        ProviderAppointmentType.business_id == business_id,
        ProviderAppointmentType.provider_id == provider_id,
    ).order_by(ProviderAppointmentType.created_at, ProviderAppointmentType.id)
    return await _list(session, statement, ProviderAppointmentType)


async def unassign_provider_appointment_type(
    session: AsyncSession,
    *,
    business_id: UUID,
    provider_id: UUID,
    appointment_type_id: UUID,
) -> None:
    assignment = await _one(
        session,
        select(ProviderAppointmentType).where(
            ProviderAppointmentType.business_id == business_id,
            ProviderAppointmentType.provider_id == provider_id,
            ProviderAppointmentType.appointment_type_id == appointment_type_id,
        ).with_for_update(),
        ProviderAppointmentType,
        "Provider appointment type assignment not found",
    )
    try:
        await session.delete(assignment)
        await session.flush()
    except SQLAlchemyError:
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE) from None


async def create_availability_rule(
    session: AsyncSession,
    *,
    business_id: UUID,
    provider_id: UUID,
    data: AvailabilityRuleCreate,
) -> ProviderAvailabilityRule:
    await get_service_provider(session, business_id=business_id, provider_id=provider_id)
    rule = ProviderAvailabilityRule(
        business_id=business_id, provider_id=provider_id, **data.model_dump()
    )
    session.add(rule)
    await _flush(session)
    return rule


async def list_availability_rules(
    session: AsyncSession, *, business_id: UUID, provider_id: UUID
) -> list[ProviderAvailabilityRule]:
    await get_service_provider(session, business_id=business_id, provider_id=provider_id)
    statement = select(ProviderAvailabilityRule).where(
        ProviderAvailabilityRule.business_id == business_id,
        ProviderAvailabilityRule.provider_id == provider_id,
    ).order_by(
        ProviderAvailabilityRule.weekday,
        ProviderAvailabilityRule.start_local_time,
        ProviderAvailabilityRule.id,
    )
    return await _list(session, statement, ProviderAvailabilityRule)


async def update_availability_rule(
    session: AsyncSession,
    *,
    business_id: UUID,
    rule_id: UUID,
    data: AvailabilityRuleUpdate,
) -> ProviderAvailabilityRule:
    rule = await _one(
        session,
        select(ProviderAvailabilityRule).where(
            ProviderAvailabilityRule.business_id == business_id,
            ProviderAvailabilityRule.id == rule_id,
        ).with_for_update(),
        ProviderAvailabilityRule,
        "Availability rule not found",
    )
    values = {
        "weekday": rule.weekday,
        "start_local_time": rule.start_local_time,
        "end_local_time": rule.end_local_time,
        "valid_from": rule.valid_from,
        "valid_until": rule.valid_until,
        "active": rule.active,
        **data.model_dump(exclude_unset=True),
    }
    try:
        validated = AvailabilityRuleCreate.model_validate(values)
    except ValidationError:
        raise SchedulingValidationError("Invalid availability rule") from None
    for field, value in validated.model_dump().items():
        setattr(rule, field, value)
    await _flush(session, refresh=rule)
    return rule


async def delete_availability_rule(
    session: AsyncSession, *, business_id: UUID, rule_id: UUID
) -> None:
    await _delete_owned(
        session,
        select(ProviderAvailabilityRule).where(
            ProviderAvailabilityRule.business_id == business_id,
            ProviderAvailabilityRule.id == rule_id,
        ).with_for_update(),
        ProviderAvailabilityRule,
        "Availability rule not found",
    )


async def create_availability_exception(
    session: AsyncSession,
    *,
    business_id: UUID,
    provider_id: UUID,
    data: AvailabilityExceptionCreate,
) -> ProviderAvailabilityException:
    await get_service_provider(session, business_id=business_id, provider_id=provider_id)
    exception = ProviderAvailabilityException(
        business_id=business_id, provider_id=provider_id, **data.model_dump()
    )
    session.add(exception)
    await _flush(session)
    return exception


async def list_availability_exceptions(
    session: AsyncSession, *, business_id: UUID, provider_id: UUID
) -> list[ProviderAvailabilityException]:
    await get_service_provider(session, business_id=business_id, provider_id=provider_id)
    statement = select(ProviderAvailabilityException).where(
        ProviderAvailabilityException.business_id == business_id,
        ProviderAvailabilityException.provider_id == provider_id,
    ).order_by(
        ProviderAvailabilityException.exception_date,
        ProviderAvailabilityException.start_local_time,
        ProviderAvailabilityException.id,
    )
    return await _list(session, statement, ProviderAvailabilityException)


async def update_availability_exception(
    session: AsyncSession,
    *,
    business_id: UUID,
    exception_id: UUID,
    data: AvailabilityExceptionUpdate,
) -> ProviderAvailabilityException:
    exception = await _one(
        session,
        select(ProviderAvailabilityException).where(
            ProviderAvailabilityException.business_id == business_id,
            ProviderAvailabilityException.id == exception_id,
        ).with_for_update(),
        ProviderAvailabilityException,
        "Availability exception not found",
    )
    values = {
        "exception_date": exception.exception_date,
        "exception_kind": exception.exception_kind,
        "whole_day": exception.whole_day,
        "start_local_time": exception.start_local_time,
        "end_local_time": exception.end_local_time,
        "active": exception.active,
        **data.model_dump(exclude_unset=True),
    }
    try:
        validated = AvailabilityExceptionCreate.model_validate(values)
    except ValidationError:
        raise SchedulingValidationError("Invalid availability exception") from None
    for field, value in validated.model_dump().items():
        setattr(exception, field, value)
    await _flush(session, refresh=exception)
    return exception


async def delete_availability_exception(
    session: AsyncSession, *, business_id: UUID, exception_id: UUID
) -> None:
    await _delete_owned(
        session,
        select(ProviderAvailabilityException).where(
            ProviderAvailabilityException.business_id == business_id,
            ProviderAvailabilityException.id == exception_id,
        ).with_for_update(),
        ProviderAvailabilityException,
        "Availability exception not found",
    )


async def _one(
    session: AsyncSession, statement: Select, model: type[T], not_found: str
) -> T:
    value = await _optional(session, statement, model)
    if value is None:
        raise SchedulingNotFoundError(not_found)
    return value


async def _optional(session: AsyncSession, statement: Select, model: type[T]) -> T | None:
    try:
        value = await session.scalar(statement)
    except SQLAlchemyError:
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE) from None
    if value is not None and not isinstance(value, model):
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE)
    return value


async def _list(session: AsyncSession, statement: Select, model: type[T]) -> list[T]:
    try:
        result = await session.scalars(statement)
        values = list(result.all())
    except SQLAlchemyError:
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE) from None
    if not all(isinstance(value, model) for value in values):
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE)
    return values


async def _flush(
    session: AsyncSession,
    *,
    refresh: object | None = None,
    conflict: str = "Scheduling data conflicts with an existing record",
) -> None:
    try:
        await session.flush()
        if refresh is not None:
            await session.refresh(refresh, attribute_names=["updated_at"])
    except IntegrityError:
        raise SchedulingConflictError(conflict) from None
    except SQLAlchemyError:
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE) from None


async def _delete_owned(
    session: AsyncSession,
    statement: Select,
    model: type[T],
    not_found: str,
) -> None:
    value = await _one(session, statement, model, not_found)
    try:
        await session.delete(value)
        await session.flush()
    except SQLAlchemyError:
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE) from None
