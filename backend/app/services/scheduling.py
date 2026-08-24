from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, and_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scheduling import (
    APPOINTMENT_CANCELLATION_REASON_CODES,
    APPOINTMENT_SOURCES,
)
from app.exceptions.scheduling import (
    SchedulingConflictError,
    SchedulingNotFoundError,
    SchedulingPersistenceError,
    SchedulingStateError,
    SchedulingValidationError,
)
from app.models.appointment import Appointment
from app.models.appointment_type import AppointmentType
from app.models.business_membership import BusinessMembership
from app.models.customer import Customer
from app.models.provider_appointment_type import ProviderAppointmentType
from app.models.provider_availability_exception import ProviderAvailabilityException
from app.models.provider_availability_rule import ProviderAvailabilityRule
from app.models.service_provider import ServiceProvider
from app.schemas.scheduling import AvailabilitySlot
from app.services.scheduling_management import (
    get_appointment_type,
    get_service_provider,
)
from app.services.automation_events import record_automation_event


MAX_AVAILABILITY_WINDOW_DAYS: Final = 31
MAX_NEXT_SEARCH_DAYS: Final = 90
MAX_AVAILABILITY_RESULTS: Final = 50
MAX_ELIGIBLE_PROVIDERS: Final = 200
MAX_RULES_PER_PROVIDER: Final = 500
MAX_EXCEPTIONS_PER_PROVIDER_SEARCH: Final = 500
MAX_CONFLICTS_PER_PROVIDER_SEARCH: Final = 2_000
_MAX_BUFFER_MINUTES: Final = 720
_PERSISTENCE_MESSAGE: Final = "Scheduling data is temporarily unavailable"


async def find_available_slots(
    session: AsyncSession,
    *,
    business_id: UUID,
    appointment_type_id: UUID,
    window_start: datetime,
    window_end: datetime,
    provider_id: UUID | None = None,
    desired_results: int = 10,
    now: datetime | None = None,
) -> list[AvailabilitySlot]:
    start = _require_aware_utc(window_start, "window_start")
    end = _require_aware_utc(window_end, "window_end")
    if end <= start or end - start > timedelta(days=MAX_AVAILABILITY_WINDOW_DAYS):
        raise SchedulingValidationError("Invalid availability search window")
    limit = _validate_limit(desired_results)
    evaluated_at = _require_aware_utc(now or datetime.now(UTC), "now")
    appointment_type = await _require_active_appointment_type(
        session,
        business_id=business_id,
        appointment_type_id=appointment_type_id,
    )
    providers = await _eligible_providers(
        session,
        business_id=business_id,
        appointment_type_id=appointment_type_id,
        provider_id=provider_id,
    )
    return await _find_slots_for_providers(
        session,
        business_id=business_id,
        appointment_type=appointment_type,
        providers=providers,
        window_start=start,
        window_end=end,
        desired_results=limit,
        now=evaluated_at,
    )


async def find_next_available_slots(
    session: AsyncSession,
    *,
    business_id: UUID,
    appointment_type_id: UUID,
    starts_after: datetime,
    provider_id: UUID | None = None,
    desired_results: int = 3,
    search_days: int = 30,
    now: datetime | None = None,
) -> list[AvailabilitySlot]:
    cursor = _require_aware_utc(starts_after, "starts_after")
    limit = _validate_limit(desired_results)
    if isinstance(search_days, bool) or not 1 <= search_days <= MAX_NEXT_SEARCH_DAYS:
        raise SchedulingValidationError("Invalid next-availability search horizon")
    evaluated_at = _require_aware_utc(now or datetime.now(UTC), "now")
    search_end = cursor + timedelta(days=search_days)
    slots: list[AvailabilitySlot] = []
    while cursor < search_end and len(slots) < limit:
        chunk_end = min(cursor + timedelta(days=MAX_AVAILABILITY_WINDOW_DAYS), search_end)
        chunk = await find_available_slots(
            session,
            business_id=business_id,
            appointment_type_id=appointment_type_id,
            provider_id=provider_id,
            window_start=cursor,
            window_end=chunk_end,
            desired_results=limit - len(slots),
            now=evaluated_at,
        )
        slots.extend(chunk)
        cursor = chunk_end
    return slots


async def book_appointment(
    session: AsyncSession,
    *,
    business_id: UUID,
    provider_id: UUID,
    appointment_type_id: UUID,
    starts_at: datetime,
    source: str,
    customer_id: UUID | None = None,
    created_by_user_id: UUID | None = None,
    now: datetime | None = None,
) -> Appointment:
    """Lock, revalidate, create, and flush. The caller owns the transaction commit."""
    requested_start = _require_aware_utc(starts_at, "starts_at")
    evaluated_at = _require_aware_utc(now or datetime.now(UTC), "now")
    if source not in APPOINTMENT_SOURCES:
        raise SchedulingValidationError("Invalid appointment source")
    provider = await get_service_provider(
        session,
        business_id=business_id,
        provider_id=provider_id,
        for_update=True,
    )
    appointment_type = await _require_active_appointment_type(
        session,
        business_id=business_id,
        appointment_type_id=appointment_type_id,
        for_update=True,
    )
    _require_active_provider(provider)
    await _require_assignment(
        session,
        business_id=business_id,
        provider_id=provider_id,
        appointment_type_id=appointment_type_id,
    )
    await _require_customer(
        session, business_id=business_id, customer_id=customer_id
    )
    await _require_actor_membership(
        session, business_id=business_id, user_id=created_by_user_id
    )
    await _require_exact_slot_available(
        session,
        business_id=business_id,
        provider=provider,
        appointment_type=appointment_type,
        starts_at=requested_start,
        now=evaluated_at,
    )
    ends_at = requested_start + timedelta(minutes=appointment_type.duration_minutes)
    appointment = Appointment(
        business_id=business_id,
        provider_id=provider_id,
        appointment_type_id=appointment_type_id,
        customer_id=customer_id,
        starts_at=requested_start,
        ends_at=ends_at,
        status="confirmed",
        source=source,
        created_by_user_id=created_by_user_id,
        cancellation_reason_code=None,
    )
    session.add(appointment)
    await _flush_appointment(session)
    record_automation_event(
        session, business_id=business_id, event_type="appointment_created",
        entity_type="appointment", entity_id=appointment.id,
        payload={"status": appointment.status, "provider_id": str(provider_id),
                 "appointment_type_id": str(appointment_type_id), "starts_at": appointment.starts_at.isoformat()},
    )
    return appointment


async def reschedule_appointment(
    session: AsyncSession,
    *,
    business_id: UUID,
    appointment_id: UUID,
    starts_at: datetime,
    now: datetime | None = None,
) -> Appointment:
    """Atomically move a confirmed appointment after locked availability revalidation."""
    requested_start = _require_aware_utc(starts_at, "starts_at")
    evaluated_at = _require_aware_utc(now or datetime.now(UTC), "now")
    appointment, provider, appointment_type = await _lock_appointment_context(
        session,
        business_id=business_id,
        appointment_id=appointment_id,
        require_active_type=True,
    )
    if appointment.status != "confirmed":
        raise SchedulingStateError("Only confirmed appointments can be rescheduled")
    if appointment.starts_at - evaluated_at < timedelta(
        minutes=appointment_type.reschedule_cutoff_minutes
    ):
        raise SchedulingStateError("Appointment reschedule cutoff has passed")
    _require_active_provider(provider)
    await _require_assignment(
        session,
        business_id=business_id,
        provider_id=provider.id,
        appointment_type_id=appointment_type.id,
    )
    await _require_exact_slot_available(
        session,
        business_id=business_id,
        provider=provider,
        appointment_type=appointment_type,
        starts_at=requested_start,
        now=evaluated_at,
        exclude_appointment_id=appointment.id,
    )
    appointment.starts_at = requested_start
    appointment.ends_at = requested_start + timedelta(
        minutes=appointment_type.duration_minutes
    )
    await _flush_appointment(session, refresh=appointment)
    record_automation_event(
        session, business_id=business_id, event_type="appointment_rescheduled",
        entity_type="appointment", entity_id=appointment.id,
        payload={"status": appointment.status, "provider_id": str(appointment.provider_id),
                 "appointment_type_id": str(appointment.appointment_type_id), "starts_at": appointment.starts_at.isoformat()},
    )
    return appointment


async def cancel_appointment(
    session: AsyncSession,
    *,
    business_id: UUID,
    appointment_id: UUID,
    reason_code: str,
    now: datetime | None = None,
) -> Appointment:
    evaluated_at = _require_aware_utc(now or datetime.now(UTC), "now")
    normalized_reason = reason_code.strip() if isinstance(reason_code, str) else ""
    if normalized_reason not in APPOINTMENT_CANCELLATION_REASON_CODES:
        raise SchedulingValidationError("Invalid appointment cancellation reason")
    appointment, _provider, appointment_type = await _lock_appointment_context(
        session,
        business_id=business_id,
        appointment_id=appointment_id,
        require_active_type=False,
    )
    if appointment.status == "canceled":
        if appointment.cancellation_reason_code == normalized_reason:
            return appointment
        raise SchedulingStateError("Appointment is already canceled")
    if appointment.status != "confirmed":
        raise SchedulingStateError("Only confirmed appointments can be canceled")
    if appointment.starts_at - evaluated_at < timedelta(
        minutes=appointment_type.cancellation_cutoff_minutes
    ):
        raise SchedulingStateError("Appointment cancellation cutoff has passed")
    appointment.status = "canceled"
    appointment.cancellation_reason_code = normalized_reason
    await _flush_appointment(session, refresh=appointment)
    record_automation_event(
        session, business_id=business_id, event_type="appointment_canceled",
        entity_type="appointment", entity_id=appointment.id,
        payload={"status": appointment.status, "provider_id": str(appointment.provider_id),
                 "appointment_type_id": str(appointment.appointment_type_id), "starts_at": appointment.starts_at.isoformat()},
    )
    return appointment


async def get_appointment(
    session: AsyncSession,
    *,
    business_id: UUID,
    appointment_id: UUID,
    for_update: bool = False,
) -> Appointment:
    statement = select(Appointment).where(
        Appointment.business_id == business_id,
        Appointment.id == appointment_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return await _one(session, statement, Appointment, "Appointment not found")


async def list_appointments(
    session: AsyncSession,
    *,
    business_id: UUID,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    provider_id: UUID | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Appointment]:
    if isinstance(limit, bool) or not 1 <= limit <= 500:
        raise SchedulingValidationError("Invalid appointment page size")
    if isinstance(offset, bool) or not 0 <= offset <= 1_000_000:
        raise SchedulingValidationError("Invalid appointment page offset")
    statement = select(Appointment).where(Appointment.business_id == business_id)
    if window_start is not None:
        statement = statement.where(
            Appointment.ends_at > _require_aware_utc(window_start, "window_start")
        )
    if window_end is not None:
        statement = statement.where(
            Appointment.starts_at < _require_aware_utc(window_end, "window_end")
        )
    if provider_id is not None:
        statement = statement.where(Appointment.provider_id == provider_id)
    if status is not None:
        statement = statement.where(Appointment.status == status)
    statement = statement.order_by(Appointment.starts_at, Appointment.id).offset(offset).limit(limit)
    return await _list(session, statement, Appointment)


async def _find_slots_for_providers(
    session: AsyncSession,
    *,
    business_id: UUID,
    appointment_type: AppointmentType,
    providers: list[ServiceProvider],
    window_start: datetime,
    window_end: datetime,
    desired_results: int,
    now: datetime,
    required_start: datetime | None = None,
    exclude_appointment_id: UUID | None = None,
) -> list[AvailabilitySlot]:
    slots: list[AvailabilitySlot] = []
    for provider in providers:
        try:
            timezone = ZoneInfo(provider.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise SchedulingValidationError("Service provider timezone is invalid") from None
        local_start_date = window_start.astimezone(timezone).date()
        local_end_date = window_end.astimezone(timezone).date()
        rules = await _load_rules(
            session, business_id=business_id, provider_id=provider.id
        )
        exceptions = await _load_exceptions(
            session,
            business_id=business_id,
            provider_id=provider.id,
            start_date=local_start_date,
            end_date=local_end_date,
        )
        conflicts = await _load_conflicts(
            session,
            business_id=business_id,
            provider_id=provider.id,
            window_start=window_start,
            window_end=window_end,
            exclude_appointment_id=exclude_appointment_id,
        )
        current_date = local_start_date
        while current_date <= local_end_date:
            local_windows = _availability_windows_for_date(
                day=current_date,
                rules=rules,
                exceptions=exceptions,
            )
            candidates = _generate_candidates(
                provider=provider,
                appointment_type=appointment_type,
                day=current_date,
                timezone=timezone,
                local_windows=local_windows,
                conflicts=conflicts,
                search_start=window_start,
                search_end=window_end,
                now=now,
                required_start=required_start,
            )
            slots.extend(candidates)
            current_date += timedelta(days=1)

    unique = {
        (slot.starts_at, slot.provider_id, slot.appointment_type_id): slot
        for slot in slots
    }
    ordered = sorted(
        unique.values(),
        key=lambda slot: (
            slot.starts_at,
            slot.provider_display_name.casefold(),
            str(slot.provider_id),
        ),
    )
    return ordered[:desired_results]


def _availability_windows_for_date(
    *,
    day: date,
    rules: Iterable[ProviderAvailabilityRule],
    exceptions: Iterable[ProviderAvailabilityException],
) -> list[tuple[datetime, datetime]]:
    day_start = datetime.combine(day, time.min)
    day_end = datetime.combine(day + timedelta(days=1), time.min)
    applicable_rules = [
        rule
        for rule in rules
        if rule.active
        and rule.weekday == day.weekday()
        and (rule.valid_from is None or rule.valid_from <= day)
        and (rule.valid_until is None or rule.valid_until >= day)
    ]
    day_exceptions = [
        item for item in exceptions if item.active and item.exception_date == day
    ]
    overrides = [
        item for item in day_exceptions if item.exception_kind == "available_override"
    ]
    if overrides:
        windows = [
            (day_start, day_end)
            if item.whole_day
            else (
                datetime.combine(day, item.start_local_time),
                datetime.combine(day, item.end_local_time),
            )
            for item in overrides
        ]
    else:
        windows = [
            (
                datetime.combine(day, rule.start_local_time),
                datetime.combine(day, rule.end_local_time),
            )
            for rule in applicable_rules
        ]
    windows = _merge_windows(windows)
    unavailable = [
        item for item in day_exceptions if item.exception_kind == "unavailable"
    ]
    if any(item.whole_day for item in unavailable):
        return []
    for item in unavailable:
        cut = (
            datetime.combine(day, item.start_local_time),
            datetime.combine(day, item.end_local_time),
        )
        windows = _subtract_window(windows, cut)
    return windows


def _generate_candidates(
    *,
    provider: ServiceProvider,
    appointment_type: AppointmentType,
    day: date,
    timezone: ZoneInfo,
    local_windows: list[tuple[datetime, datetime]],
    conflicts: list[tuple[Appointment, AppointmentType]],
    search_start: datetime,
    search_end: datetime,
    now: datetime,
    required_start: datetime | None,
) -> list[AvailabilitySlot]:
    del day
    duration = timedelta(minutes=appointment_type.duration_minutes)
    before = timedelta(minutes=appointment_type.buffer_before_minutes)
    after = timedelta(minutes=appointment_type.buffer_after_minutes)
    interval = timedelta(minutes=appointment_type.slot_interval_minutes)
    result: list[AvailabilitySlot] = []
    for window_start, window_end in local_windows:
        candidate_local = window_start + before
        while candidate_local + duration + after <= window_end:
            candidate_aware = _localize_unique(candidate_local, timezone)
            candidate_local_end = candidate_local + duration
            candidate_end_aware = _localize_unique(candidate_local_end, timezone)
            buffer_start_aware = _localize_unique(candidate_local - before, timezone)
            buffer_end_aware = _localize_unique(candidate_local_end + after, timezone)
            if None not in (
                candidate_aware,
                candidate_end_aware,
                buffer_start_aware,
                buffer_end_aware,
            ):
                start_utc = candidate_aware.astimezone(UTC)
                end_utc = candidate_end_aware.astimezone(UTC)
                occupied_start = buffer_start_aware.astimezone(UTC)
                occupied_end = buffer_end_aware.astimezone(UTC)
                if (
                    end_utc - start_utc == duration
                    and start_utc >= search_start
                    and end_utc <= search_end
                    and (required_start is None or start_utc == required_start)
                    and _policy_allows(
                        start_utc=start_utc,
                        provider_timezone=timezone,
                        appointment_type=appointment_type,
                        now=now,
                    )
                    and not _has_conflict(
                        occupied_start=occupied_start,
                        occupied_end=occupied_end,
                        conflicts=conflicts,
                    )
                ):
                    result.append(
                        AvailabilitySlot(
                            provider_id=provider.id,
                            provider_display_name=provider.display_name,
                            appointment_type_id=appointment_type.id,
                            starts_at=start_utc,
                            ends_at=end_utc,
                            timezone=provider.timezone,
                            location_reference=provider.location_reference,
                        )
                    )
            candidate_local += interval
    return result


def _localize_unique(value: datetime, timezone: ZoneInfo) -> datetime | None:
    """Return a local wall time only when it maps to exactly one real instant."""
    if value.tzinfo is not None:
        raise SchedulingValidationError("Expected local wall-clock datetime")
    first = value.replace(tzinfo=timezone, fold=0)
    second = value.replace(tzinfo=timezone, fold=1)
    first_valid = first.astimezone(UTC).astimezone(timezone).replace(tzinfo=None) == value
    second_valid = second.astimezone(UTC).astimezone(timezone).replace(tzinfo=None) == value
    if not first_valid and not second_valid:
        return None
    if first_valid and second_valid and first.utcoffset() != second.utcoffset():
        return None
    return first if first_valid else second


def _policy_allows(
    *,
    start_utc: datetime,
    provider_timezone: ZoneInfo,
    appointment_type: AppointmentType,
    now: datetime,
) -> bool:
    if start_utc < now + timedelta(minutes=appointment_type.minimum_notice_minutes):
        return False
    if start_utc > now + timedelta(days=appointment_type.maximum_future_days):
        return False
    if (
        not appointment_type.allow_same_day
        and start_utc.astimezone(provider_timezone).date()
        == now.astimezone(provider_timezone).date()
    ):
        return False
    return True


def _has_conflict(
    *,
    occupied_start: datetime,
    occupied_end: datetime,
    conflicts: Iterable[tuple[Appointment, AppointmentType]],
) -> bool:
    for appointment, existing_type in conflicts:
        existing_start = appointment.starts_at - timedelta(
            minutes=existing_type.buffer_before_minutes
        )
        existing_end = appointment.ends_at + timedelta(
            minutes=existing_type.buffer_after_minutes
        )
        if occupied_start < existing_end and occupied_end > existing_start:
            return True
    return False


def _merge_windows(
    windows: Iterable[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    ordered = sorted(windows)
    merged: list[tuple[datetime, datetime]] = []
    for start, end in ordered:
        if start >= end:
            raise SchedulingValidationError("Invalid availability window")
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _subtract_window(
    windows: Iterable[tuple[datetime, datetime]],
    cut: tuple[datetime, datetime],
) -> list[tuple[datetime, datetime]]:
    cut_start, cut_end = cut
    result: list[tuple[datetime, datetime]] = []
    for start, end in windows:
        if cut_end <= start or cut_start >= end:
            result.append((start, end))
            continue
        if cut_start > start:
            result.append((start, min(cut_start, end)))
        if cut_end < end:
            result.append((max(cut_end, start), end))
    return result


async def _require_exact_slot_available(
    session: AsyncSession,
    *,
    business_id: UUID,
    provider: ServiceProvider,
    appointment_type: AppointmentType,
    starts_at: datetime,
    now: datetime,
    exclude_appointment_id: UUID | None = None,
) -> None:
    try:
        timezone = ZoneInfo(provider.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise SchedulingValidationError("Service provider timezone is invalid") from None
    local_day = starts_at.astimezone(timezone).date()
    local_start = _localize_unique(datetime.combine(local_day, time.min), timezone)
    local_end = _localize_unique(
        datetime.combine(local_day + timedelta(days=1), time.min), timezone
    )
    if local_start is None or local_end is None:
        raise SchedulingValidationError("Provider timezone date is ambiguous")
    slots = await _find_slots_for_providers(
        session,
        business_id=business_id,
        appointment_type=appointment_type,
        providers=[provider],
        window_start=local_start.astimezone(UTC),
        window_end=local_end.astimezone(UTC),
        desired_results=1,
        now=now,
        required_start=starts_at,
        exclude_appointment_id=exclude_appointment_id,
    )
    if not slots:
        raise SchedulingConflictError("Requested appointment time is unavailable")


async def _eligible_providers(
    session: AsyncSession,
    *,
    business_id: UUID,
    appointment_type_id: UUID,
    provider_id: UUID | None,
) -> list[ServiceProvider]:
    if provider_id is not None:
        provider = await get_service_provider(
            session, business_id=business_id, provider_id=provider_id
        )
        _require_active_provider(provider)
        await _require_assignment(
            session,
            business_id=business_id,
            provider_id=provider_id,
            appointment_type_id=appointment_type_id,
        )
        return [provider]
    statement = (
        select(ServiceProvider)
        .join(
            ProviderAppointmentType,
            and_(
                ProviderAppointmentType.provider_id == ServiceProvider.id,
                ProviderAppointmentType.business_id == ServiceProvider.business_id,
            ),
        )
        .where(
            ServiceProvider.business_id == business_id,
            ServiceProvider.active.is_(True),
            ProviderAppointmentType.business_id == business_id,
            ProviderAppointmentType.appointment_type_id == appointment_type_id,
        )
        .order_by(ServiceProvider.display_name, ServiceProvider.id)
    )
    providers = await _list(
        session, statement.limit(MAX_ELIGIBLE_PROVIDERS + 1), ServiceProvider
    )
    if len(providers) > MAX_ELIGIBLE_PROVIDERS:
        raise SchedulingValidationError("Availability search has too many providers")
    return providers


async def _require_active_appointment_type(
    session: AsyncSession,
    *,
    business_id: UUID,
    appointment_type_id: UUID,
    for_update: bool = False,
) -> AppointmentType:
    appointment_type = await get_appointment_type(
        session,
        business_id=business_id,
        appointment_type_id=appointment_type_id,
        for_update=for_update,
    )
    if not appointment_type.active:
        raise SchedulingStateError("Appointment type is inactive")
    return appointment_type


async def _lock_appointment_context(
    session: AsyncSession,
    *,
    business_id: UUID,
    appointment_id: UUID,
    require_active_type: bool,
) -> tuple[Appointment, ServiceProvider, AppointmentType]:
    """Use one lock order for all booking mutations: provider, type, appointment."""
    reference = await get_appointment(
        session,
        business_id=business_id,
        appointment_id=appointment_id,
    )
    provider = await get_service_provider(
        session,
        business_id=business_id,
        provider_id=reference.provider_id,
        for_update=True,
    )
    if require_active_type:
        appointment_type = await _require_active_appointment_type(
            session,
            business_id=business_id,
            appointment_type_id=reference.appointment_type_id,
            for_update=True,
        )
    else:
        appointment_type = await get_appointment_type(
            session,
            business_id=business_id,
            appointment_type_id=reference.appointment_type_id,
            for_update=True,
        )
    appointment = await get_appointment(
        session,
        business_id=business_id,
        appointment_id=appointment_id,
        for_update=True,
    )
    if (
        appointment.provider_id != provider.id
        or appointment.appointment_type_id != appointment_type.id
        or appointment.business_id != business_id
    ):
        raise SchedulingConflictError("Appointment ownership changed during locking")
    return appointment, provider, appointment_type


def _require_active_provider(provider: ServiceProvider) -> None:
    if not provider.active:
        raise SchedulingStateError("Service provider is inactive")


async def _require_assignment(
    session: AsyncSession,
    *,
    business_id: UUID,
    provider_id: UUID,
    appointment_type_id: UUID,
) -> None:
    statement = select(ProviderAppointmentType.id).where(
        ProviderAppointmentType.business_id == business_id,
        ProviderAppointmentType.provider_id == provider_id,
        ProviderAppointmentType.appointment_type_id == appointment_type_id,
    )
    try:
        assignment_id = await session.scalar(statement)
    except SQLAlchemyError:
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE) from None
    if assignment_id is None:
        raise SchedulingConflictError("Provider does not support this appointment type")


async def _require_customer(
    session: AsyncSession, *, business_id: UUID, customer_id: UUID | None
) -> None:
    if customer_id is None:
        return
    statement = select(Customer).where(
        Customer.business_id == business_id,
        Customer.id == customer_id,
    )
    customer = await _optional(session, statement, Customer)
    if customer is None:
        raise SchedulingNotFoundError("Customer not found")
    if not customer.active:
        raise SchedulingStateError("Customer is inactive")


async def _require_actor_membership(
    session: AsyncSession, *, business_id: UUID, user_id: UUID | None
) -> None:
    if user_id is None:
        return
    statement = select(BusinessMembership.id).where(
        BusinessMembership.business_id == business_id,
        BusinessMembership.user_id == user_id,
        BusinessMembership.status == "active",
    )
    try:
        membership_id = await session.scalar(statement)
    except SQLAlchemyError:
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE) from None
    if membership_id is None:
        raise SchedulingNotFoundError("Appointment creator is not a business member")


async def _load_rules(
    session: AsyncSession, *, business_id: UUID, provider_id: UUID
) -> list[ProviderAvailabilityRule]:
    statement = select(ProviderAvailabilityRule).where(
        ProviderAvailabilityRule.business_id == business_id,
        ProviderAvailabilityRule.provider_id == provider_id,
        ProviderAvailabilityRule.active.is_(True),
    )
    rules = await _list(
        session,
        statement.limit(MAX_RULES_PER_PROVIDER + 1),
        ProviderAvailabilityRule,
    )
    if len(rules) > MAX_RULES_PER_PROVIDER:
        raise SchedulingValidationError("Provider availability configuration is too large")
    return rules


async def _load_exceptions(
    session: AsyncSession,
    *,
    business_id: UUID,
    provider_id: UUID,
    start_date: date,
    end_date: date,
) -> list[ProviderAvailabilityException]:
    statement = select(ProviderAvailabilityException).where(
        ProviderAvailabilityException.business_id == business_id,
        ProviderAvailabilityException.provider_id == provider_id,
        ProviderAvailabilityException.active.is_(True),
        ProviderAvailabilityException.exception_date >= start_date,
        ProviderAvailabilityException.exception_date <= end_date,
    )
    exceptions = await _list(
        session,
        statement.limit(MAX_EXCEPTIONS_PER_PROVIDER_SEARCH + 1),
        ProviderAvailabilityException,
    )
    if len(exceptions) > MAX_EXCEPTIONS_PER_PROVIDER_SEARCH:
        raise SchedulingValidationError("Provider exception configuration is too large")
    return exceptions


async def _load_conflicts(
    session: AsyncSession,
    *,
    business_id: UUID,
    provider_id: UUID,
    window_start: datetime,
    window_end: datetime,
    exclude_appointment_id: UUID | None,
) -> list[tuple[Appointment, AppointmentType]]:
    margin = timedelta(minutes=_MAX_BUFFER_MINUTES)
    statement = (
        select(Appointment, AppointmentType)
        .join(
            AppointmentType,
            and_(
                AppointmentType.id == Appointment.appointment_type_id,
                AppointmentType.business_id == Appointment.business_id,
            ),
        )
        .where(
            Appointment.business_id == business_id,
            Appointment.provider_id == provider_id,
            Appointment.status == "confirmed",
            Appointment.starts_at < window_end + margin,
            Appointment.ends_at > window_start - margin,
        )
    )
    if exclude_appointment_id is not None:
        statement = statement.where(Appointment.id != exclude_appointment_id)
    statement = statement.limit(MAX_CONFLICTS_PER_PROVIDER_SEARCH + 1)
    try:
        rows = list((await session.execute(statement)).all())
    except SQLAlchemyError:
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE) from None
    if not all(
        len(row) == 2
        and isinstance(row[0], Appointment)
        and isinstance(row[1], AppointmentType)
        and row[0].business_id == business_id
        and row[1].business_id == business_id
        for row in rows
    ):
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE)
    if len(rows) > MAX_CONFLICTS_PER_PROVIDER_SEARCH:
        raise SchedulingValidationError("Appointment conflict set is too large")
    return [(row[0], row[1]) for row in rows]


async def _one(
    session: AsyncSession, statement: Select, model: type, not_found: str
):
    value = await _optional(session, statement, model)
    if value is None:
        raise SchedulingNotFoundError(not_found)
    return value


async def _optional(session: AsyncSession, statement: Select, model: type):
    try:
        value = await session.scalar(statement)
    except SQLAlchemyError:
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE) from None
    if value is not None and not isinstance(value, model):
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE)
    return value


async def _list(session: AsyncSession, statement: Select, model: type) -> list:
    try:
        result = await session.scalars(statement)
        values = list(result.all())
    except SQLAlchemyError:
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE) from None
    if not all(isinstance(value, model) for value in values):
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE)
    return values


async def _flush_appointment(
    session: AsyncSession, *, refresh: Appointment | None = None
) -> None:
    try:
        await session.flush()
        if refresh is not None:
            await session.refresh(refresh, attribute_names=["updated_at"])
    except IntegrityError:
        raise SchedulingConflictError("Appointment time is no longer available") from None
    except SQLAlchemyError:
        raise SchedulingPersistenceError(_PERSISTENCE_MESSAGE) from None


def _require_aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SchedulingValidationError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_AVAILABILITY_RESULTS:
        raise SchedulingValidationError("Invalid availability result limit")
    return value
