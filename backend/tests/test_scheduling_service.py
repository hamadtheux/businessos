from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.scheduling import (  # noqa: E402
    SchedulingConflictError,
    SchedulingNotFoundError,
    SchedulingStateError,
    SchedulingValidationError,
)
from app.models.appointment import Appointment  # noqa: E402
from app.models.appointment_type import AppointmentType  # noqa: E402
from app.models.customer import Customer  # noqa: E402
from app.models.service_provider import ServiceProvider  # noqa: E402
from app.services.scheduling import (  # noqa: E402
    _require_active_appointment_type,
    _require_customer,
    book_appointment,
    cancel_appointment,
    get_appointment,
    reschedule_appointment,
)


BUSINESS_ID = UUID("31000000-0000-0000-0000-000000000001")
OTHER_BUSINESS_ID = UUID("32000000-0000-0000-0000-000000000002")
USER_ID = UUID("33000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 8, 23, 8, tzinfo=UTC)
START = datetime(2026, 8, 24, 5, tzinfo=UTC)


class BookingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_booking_locks_provider_calculates_end_and_never_commits(self) -> None:
        session = _FakeSession()
        provider = _provider()
        appointment_type = _type()
        with _booking_dependencies(provider, appointment_type) as dependencies:
            appointment = await book_appointment(
                session,
                business_id=BUSINESS_ID,
                provider_id=provider.id,
                appointment_type_id=appointment_type.id,
                customer_id=None,
                starts_at=START,
                source="manual",
                created_by_user_id=USER_ID,
                now=NOW,
            )
        self.assertEqual(appointment.ends_at, START + timedelta(minutes=30))
        self.assertEqual(appointment.status, "confirmed")
        self.assertEqual(appointment.business_id, BUSINESS_ID)
        self.assertEqual(session.flush_calls, 1)
        self.assertEqual(session.commit_calls, 0)
        self.assertIs(session.added, appointment)
        self.assertTrue(dependencies[0].await_args.kwargs["for_update"])

    async def test_client_cannot_supply_or_influence_end_time(self) -> None:
        session = _FakeSession()
        appointment_type = _type(duration=45)
        with _booking_dependencies(_provider(), appointment_type):
            appointment = await book_appointment(
                session,
                business_id=BUSINESS_ID,
                provider_id=_provider().id,
                appointment_type_id=appointment_type.id,
                starts_at=START,
                source="api",
                now=NOW,
            )
        self.assertEqual(appointment.ends_at, START + timedelta(minutes=45))

    async def test_unsupported_provider_service_and_unavailable_time_are_rejected(self) -> None:
        provider = _provider()
        appointment_type = _type()
        with _booking_dependencies(
            provider,
            appointment_type,
            assignment_error=SchedulingConflictError("unsupported"),
        ):
            with self.assertRaises(SchedulingConflictError):
                await book_appointment(
                    _FakeSession(),
                    business_id=BUSINESS_ID,
                    provider_id=provider.id,
                    appointment_type_id=appointment_type.id,
                    starts_at=START,
                    source="manual",
                    now=NOW,
                )
        with _booking_dependencies(
            provider,
            appointment_type,
            slot_error=SchedulingConflictError("outside schedule"),
        ):
            with self.assertRaises(SchedulingConflictError):
                await book_appointment(
                    _FakeSession(),
                    business_id=BUSINESS_ID,
                    provider_id=provider.id,
                    appointment_type_id=appointment_type.id,
                    starts_at=START,
                    source="manual",
                    now=NOW,
                )

    async def test_inactive_provider_and_invalid_source_are_rejected(self) -> None:
        provider = _provider()
        provider.active = False
        with _booking_dependencies(provider, _type()):
            with self.assertRaises(SchedulingStateError):
                await book_appointment(
                    _FakeSession(),
                    business_id=BUSINESS_ID,
                    provider_id=provider.id,
                    appointment_type_id=_type().id,
                    starts_at=START,
                    source="manual",
                    now=NOW,
                )
        with self.assertRaises(SchedulingValidationError):
            await book_appointment(
                _FakeSession(),
                business_id=BUSINESS_ID,
                provider_id=provider.id,
                appointment_type_id=_type().id,
                starts_at=START,
                source="raw_connector",
                now=NOW,
            )

    async def test_cross_tenant_provider_and_customer_are_denied(self) -> None:
        with patch(
            "app.services.scheduling.get_service_provider",
            new=AsyncMock(side_effect=SchedulingNotFoundError("not found")),
        ):
            with self.assertRaises(SchedulingNotFoundError):
                await book_appointment(
                    _FakeSession(),
                    business_id=OTHER_BUSINESS_ID,
                    provider_id=_provider().id,
                    appointment_type_id=_type().id,
                    starts_at=START,
                    source="manual",
                    now=NOW,
                )

        customer = Customer(
            id=uuid4(), business_id=OTHER_BUSINESS_ID, display_name="Other", active=True
        )
        session = _ScalarSession(customer)
        with self.assertRaises(SchedulingNotFoundError):
            await _require_customer(
                session, business_id=BUSINESS_ID, customer_id=customer.id
            )
        self.assertIn("customers.business_id", str(session.statement))

    async def test_naive_requested_start_is_rejected(self) -> None:
        with self.assertRaises(SchedulingValidationError):
            await book_appointment(
                _FakeSession(),
                business_id=BUSINESS_ID,
                provider_id=uuid4(),
                appointment_type_id=uuid4(),
                starts_at=datetime(2026, 8, 24, 10),
                source="manual",
            )

    async def test_inactive_appointment_type_is_rejected(self) -> None:
        appointment_type = _type()
        appointment_type.active = False
        with patch(
            "app.services.scheduling.get_appointment_type",
            new=AsyncMock(return_value=appointment_type),
        ):
            with self.assertRaises(SchedulingStateError):
                await _require_active_appointment_type(
                    _FakeSession(),
                    business_id=BUSINESS_ID,
                    appointment_type_id=appointment_type.id,
                )


class RescheduleAndCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_reschedule_is_atomic_and_excludes_old_slot(self) -> None:
        session = _FakeSession()
        appointment = _appointment()
        provider = _provider()
        appointment_type = _type()
        new_start = START + timedelta(hours=1)
        with (
            patch(
                "app.services.scheduling.get_appointment",
                new=AsyncMock(return_value=appointment),
            ),
            patch(
                "app.services.scheduling._require_active_appointment_type",
                new=AsyncMock(return_value=appointment_type),
            ),
            patch(
                "app.services.scheduling.get_service_provider",
                new=AsyncMock(return_value=provider),
            ) as get_provider,
            patch("app.services.scheduling._require_assignment", new=AsyncMock()),
            patch(
                "app.services.scheduling._require_exact_slot_available",
                new=AsyncMock(),
            ) as availability,
        ):
            result = await reschedule_appointment(
                session,
                business_id=BUSINESS_ID,
                appointment_id=appointment.id,
                starts_at=new_start,
                now=NOW,
            )
        self.assertIs(result, appointment)
        self.assertEqual(appointment.starts_at, new_start)
        self.assertEqual(appointment.ends_at, new_start + timedelta(minutes=30))
        self.assertEqual(
            availability.await_args.kwargs["exclude_appointment_id"], appointment.id
        )
        self.assertTrue(get_provider.await_args.kwargs["for_update"])
        self.assertEqual(session.commit_calls, 0)

    async def test_failed_reschedule_leaves_old_time_unchanged(self) -> None:
        appointment = _appointment()
        old_start, old_end = appointment.starts_at, appointment.ends_at
        with (
            patch(
                "app.services.scheduling.get_appointment",
                new=AsyncMock(return_value=appointment),
            ),
            patch(
                "app.services.scheduling._require_active_appointment_type",
                new=AsyncMock(return_value=_type()),
            ),
            patch(
                "app.services.scheduling.get_service_provider",
                new=AsyncMock(return_value=_provider()),
            ),
            patch("app.services.scheduling._require_assignment", new=AsyncMock()),
            patch(
                "app.services.scheduling._require_exact_slot_available",
                new=AsyncMock(side_effect=SchedulingConflictError("busy")),
            ),
        ):
            with self.assertRaises(SchedulingConflictError):
                await reschedule_appointment(
                    _FakeSession(),
                    business_id=BUSINESS_ID,
                    appointment_id=appointment.id,
                    starts_at=START + timedelta(hours=1),
                    now=NOW,
                )
        self.assertEqual((appointment.starts_at, appointment.ends_at), (old_start, old_end))

    async def test_cancel_frees_slot_by_terminal_status_and_never_commits(self) -> None:
        session = _FakeSession()
        appointment = _appointment()
        with (
            patch(
                "app.services.scheduling.get_appointment",
                new=AsyncMock(return_value=appointment),
            ),
            patch(
                "app.services.scheduling.get_appointment_type",
                new=AsyncMock(return_value=_type()),
            ),
            patch(
                "app.services.scheduling.get_service_provider",
                new=AsyncMock(return_value=_provider()),
            ),
        ):
            result = await cancel_appointment(
                session,
                business_id=BUSINESS_ID,
                appointment_id=appointment.id,
                reason_code="customer_request",
                now=NOW,
            )
        self.assertIs(result, appointment)
        self.assertEqual(appointment.status, "canceled")
        self.assertEqual(appointment.cancellation_reason_code, "customer_request")
        self.assertEqual(session.flush_calls, 1)
        self.assertEqual(session.commit_calls, 0)

    async def test_cancel_rejects_invalid_state_reason_and_cutoff(self) -> None:
        with self.assertRaises(SchedulingValidationError):
            await cancel_appointment(
                _FakeSession(),
                business_id=BUSINESS_ID,
                appointment_id=uuid4(),
                reason_code="raw private explanation",
                now=NOW,
            )

        appointment = _appointment()
        appointment.status = "completed"
        with (
            patch(
                "app.services.scheduling.get_appointment",
                new=AsyncMock(return_value=appointment),
            ),
            patch(
                "app.services.scheduling.get_service_provider",
                new=AsyncMock(return_value=_provider()),
            ),
            patch(
                "app.services.scheduling.get_appointment_type",
                new=AsyncMock(return_value=_type()),
            ),
        ):
            with self.assertRaises(SchedulingStateError):
                await cancel_appointment(
                    _FakeSession(),
                    business_id=BUSINESS_ID,
                    appointment_id=appointment.id,
                    reason_code="other",
                    now=NOW,
                )

        appointment = _appointment(starts_at=NOW + timedelta(minutes=30))
        appointment_type = _type()
        appointment_type.cancellation_cutoff_minutes = 60
        with (
            patch(
                "app.services.scheduling.get_appointment",
                new=AsyncMock(return_value=appointment),
            ),
            patch(
                "app.services.scheduling.get_appointment_type",
                new=AsyncMock(return_value=appointment_type),
            ),
            patch(
                "app.services.scheduling.get_service_provider",
                new=AsyncMock(return_value=_provider()),
            ),
        ):
            with self.assertRaises(SchedulingStateError):
                await cancel_appointment(
                    _FakeSession(),
                    business_id=BUSINESS_ID,
                    appointment_id=appointment.id,
                    reason_code="other",
                    now=NOW,
                )

    async def test_get_appointment_is_tenant_scoped(self) -> None:
        appointment = _appointment()
        session = _ScalarSession(appointment)
        result = await get_appointment(
            session, business_id=BUSINESS_ID, appointment_id=appointment.id
        )
        self.assertIs(result, appointment)
        sql = str(session.statement)
        self.assertIn("appointments.business_id", sql)
        self.assertIn("appointments.id", sql)


def _booking_dependencies(
    provider: ServiceProvider,
    appointment_type: AppointmentType,
    *,
    assignment_error: Exception | None = None,
    slot_error: Exception | None = None,
):
    assignment = AsyncMock(side_effect=assignment_error)
    slot = AsyncMock(side_effect=slot_error)
    patches = (
        patch(
            "app.services.scheduling.get_service_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "app.services.scheduling._require_active_appointment_type",
            new=AsyncMock(return_value=appointment_type),
        ),
        patch("app.services.scheduling._require_assignment", new=assignment),
        patch("app.services.scheduling._require_customer", new=AsyncMock()),
        patch("app.services.scheduling._require_actor_membership", new=AsyncMock()),
        patch("app.services.scheduling._require_exact_slot_available", new=slot),
    )
    return _PatchGroup(patches)


class _PatchGroup:
    def __init__(self, patches) -> None:
        self.patches = patches
        self.values = []

    def __enter__(self):
        self.values = [item.__enter__() for item in self.patches]
        return self.values

    def __exit__(self, *args):
        for item in reversed(self.patches):
            item.__exit__(*args)


class _FakeSession:
    def __init__(self) -> None:
        self.added = None
        self.flush_calls = 0
        self.refresh_calls = 0
        self.commit_calls = 0

    def add(self, value) -> None:
        self.added = value

    async def flush(self) -> None:
        self.flush_calls += 1

    async def refresh(self, value, *, attribute_names) -> None:
        self.refresh_calls += 1


class _ScalarSession:
    def __init__(self, value) -> None:
        self.value = value
        self.statement = None

    async def scalar(self, statement):
        self.statement = statement
        conditions = str(statement)
        if "customers" in conditions and self.value.business_id != BUSINESS_ID:
            return None
        return self.value


def _provider() -> ServiceProvider:
    return ServiceProvider(
        id=UUID("34000000-0000-0000-0000-000000000004"),
        business_id=BUSINESS_ID,
        display_name="Provider",
        provider_type="consultant",
        active=True,
        timezone="UTC",
    )


def _type(*, duration: int = 30) -> AppointmentType:
    return AppointmentType(
        id=UUID("35000000-0000-0000-0000-000000000005"),
        business_id=BUSINESS_ID,
        name="Consultation",
        duration_minutes=duration,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        slot_interval_minutes=30,
        active=True,
        minimum_notice_minutes=0,
        maximum_future_days=365,
        allow_same_day=True,
        cancellation_cutoff_minutes=0,
        reschedule_cutoff_minutes=0,
    )


def _appointment(*, starts_at: datetime = START) -> Appointment:
    return Appointment(
        id=uuid4(),
        business_id=BUSINESS_ID,
        provider_id=_provider().id,
        appointment_type_id=_type().id,
        customer_id=None,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=30),
        status="confirmed",
        source="manual",
        created_by_user_id=USER_ID,
        cancellation_reason_code=None,
    )
