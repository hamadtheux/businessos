from __future__ import annotations

import os
import unittest
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.scheduling import SchedulingValidationError  # noqa: E402
from app.models.appointment import Appointment  # noqa: E402
from app.models.appointment_type import AppointmentType  # noqa: E402
from app.models.provider_availability_exception import (  # noqa: E402
    ProviderAvailabilityException,
)
from app.models.provider_availability_rule import ProviderAvailabilityRule  # noqa: E402
from app.models.service_provider import ServiceProvider  # noqa: E402
from app.schemas.scheduling import AvailabilitySlot  # noqa: E402
from app.services.scheduling import (  # noqa: E402
    _availability_windows_for_date,
    _generate_candidates,
    _localize_unique,
    find_available_slots,
    find_next_available_slots,
)


BUSINESS_ID = UUID("21000000-0000-0000-0000-000000000001")
DAY = date(2026, 8, 24)  # Monday
NOW = datetime(2026, 8, 23, 0, tzinfo=UTC)


class AvailabilityWindowTests(unittest.TestCase):
    def test_empty_schedule_has_no_windows(self) -> None:
        self.assertEqual(
            _availability_windows_for_date(day=DAY, rules=[], exceptions=[]), []
        )

    def test_multiple_weekly_windows_are_preserved_and_merged(self) -> None:
        rules = [
            _rule(start=time(9), end=time(12)),
            _rule(start=time(11), end=time(13)),
            _rule(start=time(14), end=time(18)),
        ]
        windows = _availability_windows_for_date(
            day=DAY, rules=rules, exceptions=[]
        )
        self.assertEqual(
            windows,
            [
                (datetime.combine(DAY, time(9)), datetime.combine(DAY, time(13))),
                (datetime.combine(DAY, time(14)), datetime.combine(DAY, time(18))),
            ],
        )

    def test_whole_day_unavailable_closes_date(self) -> None:
        windows = _availability_windows_for_date(
            day=DAY,
            rules=[_rule(start=time(9), end=time(17))],
            exceptions=[_exception(kind="unavailable", whole_day=True)],
        )
        self.assertEqual(windows, [])

    def test_partial_unavailable_window_creates_break(self) -> None:
        windows = _availability_windows_for_date(
            day=DAY,
            rules=[_rule(start=time(9), end=time(17))],
            exceptions=[
                _exception(kind="unavailable", start=time(12), end=time(13))
            ],
        )
        self.assertEqual(
            windows,
            [
                (datetime.combine(DAY, time(9)), datetime.combine(DAY, time(12))),
                (datetime.combine(DAY, time(13)), datetime.combine(DAY, time(17))),
            ],
        )

    def test_available_override_replaces_recurring_hours(self) -> None:
        windows = _availability_windows_for_date(
            day=DAY,
            rules=[_rule(start=time(9), end=time(17))],
            exceptions=[
                _exception(
                    kind="available_override", start=time(18), end=time(20)
                )
            ],
        )
        self.assertEqual(
            windows,
            [(datetime.combine(DAY, time(18)), datetime.combine(DAY, time(20)))],
        )


class SlotGenerationTests(unittest.TestCase):
    def test_duration_interval_and_timezone_are_authoritative(self) -> None:
        slots = _slots(
            windows=[
                (datetime.combine(DAY, time(9)), datetime.combine(DAY, time(11)))
            ],
            appointment_type=_appointment_type(duration=30, interval=30),
        )
        self.assertEqual(
            [slot.starts_at for slot in slots],
            [
                datetime(2026, 8, 24, 4, 0, tzinfo=UTC),
                datetime(2026, 8, 24, 4, 30, tzinfo=UTC),
                datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
                datetime(2026, 8, 24, 5, 30, tzinfo=UTC),
            ],
        )
        self.assertTrue(
            all(slot.ends_at - slot.starts_at == timedelta(minutes=30) for slot in slots)
        )
        self.assertTrue(all(slot.timezone == "Asia/Karachi" for slot in slots))

    def test_buffers_must_fit_availability_and_conflicts(self) -> None:
        appointment_type = _appointment_type(
            duration=30, interval=30, before=15, after=15
        )
        existing_type = _appointment_type(duration=30, before=10, after=10)
        existing = Appointment(
            id=uuid4(),
            business_id=BUSINESS_ID,
            provider_id=_provider().id,
            appointment_type_id=existing_type.id,
            starts_at=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
            ends_at=datetime(2026, 8, 24, 5, 30, tzinfo=UTC),
            status="confirmed",
            source="manual",
        )
        slots = _slots(
            windows=[
                (datetime.combine(DAY, time(9)), datetime.combine(DAY, time(12)))
            ],
            appointment_type=appointment_type,
            conflicts=[(existing, existing_type)],
        )
        starts = [slot.starts_at for slot in slots]
        self.assertNotIn(datetime(2026, 8, 24, 5, 0, tzinfo=UTC), starts)
        self.assertNotIn(datetime(2026, 8, 24, 5, 30, tzinfo=UTC), starts)
        self.assertEqual(starts[0], datetime(2026, 8, 24, 6, 15, tzinfo=UTC))

    def test_minimum_notice_same_day_and_horizon_are_enforced(self) -> None:
        provider = _provider(timezone="UTC")
        appointment_type = _appointment_type(duration=30, interval=30)
        appointment_type.minimum_notice_minutes = 120
        appointment_type.maximum_future_days = 2
        appointment_type.allow_same_day = False
        windows = [
            (datetime.combine(DAY, time.min), datetime.combine(DAY + timedelta(days=1), time.min))
        ]
        slots = _generate_candidates(
            provider=provider,
            appointment_type=appointment_type,
            day=DAY,
            timezone=ZoneInfo("UTC"),
            local_windows=windows,
            conflicts=[],
            search_start=datetime.combine(DAY, time.min, UTC),
            search_end=datetime.combine(DAY + timedelta(days=1), time.min, UTC),
            now=datetime.combine(DAY, time(8), UTC),
            required_start=None,
        )
        self.assertEqual(slots, [])

    def test_ambiguous_and_nonexistent_dst_wall_times_are_rejected(self) -> None:
        timezone = ZoneInfo("America/New_York")
        self.assertIsNone(_localize_unique(datetime(2026, 3, 8, 2, 30), timezone))
        self.assertIsNone(_localize_unique(datetime(2026, 11, 1, 1, 30), timezone))
        self.assertIsNotNone(_localize_unique(datetime(2026, 11, 1, 3, 0), timezone))


class AvailabilityServiceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_specific_and_any_provider_search_are_structured(self) -> None:
        appointment_type = _appointment_type()
        provider = _provider()
        expected = _slot(provider, appointment_type)
        with (
            patch(
                "app.services.scheduling._require_active_appointment_type",
                new=AsyncMock(return_value=appointment_type),
            ),
            patch(
                "app.services.scheduling._eligible_providers",
                new=AsyncMock(return_value=[provider]),
            ) as eligible,
            patch(
                "app.services.scheduling._find_slots_for_providers",
                new=AsyncMock(return_value=[expected]),
            ),
        ):
            for provider_id in (provider.id, None):
                with self.subTest(provider_id=provider_id):
                    result = await find_available_slots(
                        object(),
                        business_id=BUSINESS_ID,
                        appointment_type_id=appointment_type.id,
                        provider_id=provider_id,
                        window_start=NOW,
                        window_end=NOW + timedelta(days=1),
                        now=NOW,
                    )
                    self.assertEqual(result, [expected])
            self.assertEqual(eligible.await_args_list[0].kwargs["provider_id"], provider.id)
            self.assertIsNone(eligible.await_args_list[1].kwargs["provider_id"])

    async def test_next_search_is_bounded_and_advances_in_safe_chunks(self) -> None:
        provider = _provider()
        appointment_type = _appointment_type()
        expected = _slot(provider, appointment_type)
        with patch(
            "app.services.scheduling.find_available_slots",
            new=AsyncMock(side_effect=[[], [], [expected]]),
        ) as search:
            result = await find_next_available_slots(
                object(),
                business_id=BUSINESS_ID,
                appointment_type_id=appointment_type.id,
                starts_after=NOW,
                desired_results=1,
                search_days=90,
                now=NOW,
            )
        self.assertEqual(result, [expected])
        self.assertEqual(search.await_count, 3)
        self.assertTrue(
            all(
                call.kwargs["window_end"] - call.kwargs["window_start"]
                <= timedelta(days=31)
                for call in search.await_args_list
            )
        )
        with self.assertRaises(SchedulingValidationError):
            await find_next_available_slots(
                object(),
                business_id=BUSINESS_ID,
                appointment_type_id=appointment_type.id,
                starts_after=NOW,
                search_days=91,
            )


def _provider(*, timezone: str = "Asia/Karachi") -> ServiceProvider:
    return ServiceProvider(
        id=UUID("22000000-0000-0000-0000-000000000002"),
        business_id=BUSINESS_ID,
        display_name="Dr. Ali",
        provider_type="doctor",
        active=True,
        timezone=timezone,
        location_reference="main-branch",
    )


def _appointment_type(
    *, duration: int = 30, interval: int = 30, before: int = 0, after: int = 0
) -> AppointmentType:
    return AppointmentType(
        id=uuid4(),
        business_id=BUSINESS_ID,
        name="Consultation",
        duration_minutes=duration,
        buffer_before_minutes=before,
        buffer_after_minutes=after,
        slot_interval_minutes=interval,
        active=True,
        minimum_notice_minutes=0,
        maximum_future_days=365,
        allow_same_day=True,
        cancellation_cutoff_minutes=0,
        reschedule_cutoff_minutes=0,
    )


def _rule(*, start: time, end: time) -> ProviderAvailabilityRule:
    return ProviderAvailabilityRule(
        id=uuid4(),
        business_id=BUSINESS_ID,
        provider_id=_provider().id,
        weekday=DAY.weekday(),
        start_local_time=start,
        end_local_time=end,
        active=True,
    )


def _exception(
    *, kind: str, whole_day: bool = False, start: time | None = None, end: time | None = None
) -> ProviderAvailabilityException:
    return ProviderAvailabilityException(
        id=uuid4(),
        business_id=BUSINESS_ID,
        provider_id=_provider().id,
        exception_date=DAY,
        exception_kind=kind,
        whole_day=whole_day,
        start_local_time=start,
        end_local_time=end,
        active=True,
    )


def _slots(
    *,
    windows: list[tuple[datetime, datetime]],
    appointment_type: AppointmentType,
    conflicts: list[tuple[Appointment, AppointmentType]] | None = None,
):
    provider = _provider()
    return _generate_candidates(
        provider=provider,
        appointment_type=appointment_type,
        day=DAY,
        timezone=ZoneInfo(provider.timezone),
        local_windows=windows,
        conflicts=conflicts or [],
        search_start=datetime(2026, 8, 23, tzinfo=UTC),
        search_end=datetime(2026, 8, 25, tzinfo=UTC),
        now=NOW,
        required_start=None,
    )


def _slot(provider: ServiceProvider, appointment_type: AppointmentType) -> AvailabilitySlot:
    return AvailabilitySlot(
        provider_id=provider.id,
        provider_display_name=provider.display_name,
        appointment_type_id=appointment_type.id,
        starts_at=NOW + timedelta(days=1),
        ends_at=NOW + timedelta(days=1, minutes=30),
        timezone=provider.timezone,
        location_reference=provider.location_reference,
    )
