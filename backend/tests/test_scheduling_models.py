from __future__ import annotations

import os
import unittest
from datetime import UTC, date, datetime, time
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import ExcludeConstraint

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.models.appointment import Appointment  # noqa: E402
from app.models.appointment_type import AppointmentType  # noqa: E402
from app.models.customer import Customer  # noqa: E402
from app.models.provider_appointment_type import ProviderAppointmentType  # noqa: E402
from app.models.provider_availability_exception import (  # noqa: E402
    ProviderAvailabilityException,
)
from app.models.provider_availability_rule import ProviderAvailabilityRule  # noqa: E402
from app.models.service_provider import ServiceProvider  # noqa: E402
from app.schemas.scheduling import (  # noqa: E402
    AppointmentCreate,
    AppointmentTypeCreate,
    AvailabilityExceptionCreate,
    AvailabilityRuleCreate,
    ServiceProviderCreate,
)


class SchedulingModelTests(unittest.TestCase):
    def test_expected_tables_and_bounded_columns_exist(self) -> None:
        expected = {
            ServiceProvider: "service_providers",
            AppointmentType: "appointment_types",
            ProviderAppointmentType: "provider_appointment_types",
            ProviderAvailabilityRule: "provider_availability_rules",
            ProviderAvailabilityException: "provider_availability_exceptions",
            Customer: "customers",
            Appointment: "appointments",
        }
        for model, table_name in expected.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(model.__tablename__, table_name)
                self.assertIn("business_id", model.__table__.columns)
                self.assertIn("created_at", model.__table__.columns)
                self.assertIn("updated_at", model.__table__.columns)

    def test_tenant_pairing_uses_composite_foreign_keys(self) -> None:
        expected = {
            ProviderAppointmentType: 2,
            ProviderAvailabilityRule: 1,
            ProviderAvailabilityException: 1,
            Appointment: 3,
        }
        for model, minimum in expected.items():
            composites = [
                constraint
                for constraint in model.__table__.constraints
                if isinstance(constraint, ForeignKeyConstraint)
                and len(constraint.column_keys) == 2
            ]
            with self.subTest(model=model.__name__):
                self.assertGreaterEqual(len(composites), minimum)

    def test_appointment_has_postgresql_overlap_exclusion(self) -> None:
        exclusions = [
            constraint
            for constraint in Appointment.__table__.constraints
            if isinstance(constraint, ExcludeConstraint)
        ]
        self.assertEqual(len(exclusions), 1)
        self.assertEqual(exclusions[0].name, "ex_appointments_provider_time_overlap")
        self.assertIn("confirmed", str(exclusions[0].where))

    def test_models_exclude_clinical_and_connector_data(self) -> None:
        forbidden = {
            "diagnosis",
            "clinical_notes",
            "medical_history",
            "prescription",
            "whatsapp_transcript",
            "access_token",
            "api_key",
        }
        for model in (ServiceProvider, Customer, Appointment):
            with self.subTest(model=model.__name__):
                self.assertTrue(forbidden.isdisjoint(model.__table__.columns.keys()))

    def test_database_check_constraints_cover_core_bounds(self) -> None:
        names = {
            constraint.name
            for constraint in AppointmentType.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        self.assertIn("ck_appointment_types_valid_duration", names)
        self.assertIn("ck_appointment_types_valid_buffer_before", names)
        self.assertIn("ck_appointment_types_valid_future_horizon", names)


class SchedulingSchemaTests(unittest.TestCase):
    def test_provider_requires_valid_iana_timezone_and_safe_type(self) -> None:
        valid = ServiceProviderCreate(
            display_name=" Dr. Ali ",
            provider_type="doctor",
            timezone="Asia/Karachi",
        )
        self.assertEqual(valid.display_name, "Dr. Ali")
        for timezone in ("Not/A_Zone", "", " " * 3):
            with self.subTest(timezone=timezone), self.assertRaises(ValidationError):
                ServiceProviderCreate(
                    display_name="Provider", provider_type="doctor", timezone=timezone
                )
        with self.assertRaises(ValidationError):
            ServiceProviderCreate(
                display_name="Provider", provider_type="Doctor!", timezone="UTC"
            )

    def test_appointment_type_duration_buffers_and_policy_are_bounded(self) -> None:
        AppointmentTypeCreate(name="Consultation", duration_minutes=30)
        invalid = (
            {"duration_minutes": 0},
            {"duration_minutes": 1441},
            {"duration_minutes": 30, "buffer_before_minutes": 721},
            {"duration_minutes": 30, "buffer_after_minutes": -1},
            {"duration_minutes": 30, "maximum_future_days": 731},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                AppointmentTypeCreate(name="Consultation", **changes)

    def test_recurring_rule_rejects_invalid_ranges_and_aware_times(self) -> None:
        AvailabilityRuleCreate(
            weekday=0, start_local_time=time(9), end_local_time=time(17)
        )
        invalid = (
            {"weekday": 7, "start_local_time": time(9), "end_local_time": time(17)},
            {"weekday": 0, "start_local_time": time(17), "end_local_time": time(9)},
            {
                "weekday": 0,
                "start_local_time": time(9),
                "end_local_time": time(17),
                "valid_from": date(2026, 8, 2),
                "valid_until": date(2026, 8, 1),
            },
            {
                "weekday": 0,
                "start_local_time": time(9, tzinfo=UTC),
                "end_local_time": time(17, tzinfo=UTC),
            },
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                AvailabilityRuleCreate(**values)

    def test_exception_requires_whole_day_or_bounded_window(self) -> None:
        AvailabilityExceptionCreate(
            exception_date=date(2026, 8, 24),
            exception_kind="unavailable",
            whole_day=True,
        )
        AvailabilityExceptionCreate(
            exception_date=date(2026, 8, 24),
            exception_kind="available_override",
            start_local_time=time(10),
            end_local_time=time(12),
        )
        with self.assertRaises(ValidationError):
            AvailabilityExceptionCreate(
                exception_date=date(2026, 8, 24),
                exception_kind="unavailable",
            )
        with self.assertRaises(ValidationError):
            AvailabilityExceptionCreate(
                exception_date=date(2026, 8, 24),
                exception_kind="unavailable",
                whole_day=True,
                start_local_time=time(10),
            )

    def test_booking_rejects_naive_datetime_and_client_end_time(self) -> None:
        payload = {
            "provider_id": str(uuid4()),
            "appointment_type_id": str(uuid4()),
            "starts_at": datetime(2026, 8, 24, 10),
        }
        with self.assertRaises(ValidationError):
            AppointmentCreate.model_validate(payload)
        payload["starts_at"] = datetime(2026, 8, 24, 10, tzinfo=UTC)
        payload["ends_at"] = datetime(2026, 8, 24, 11, tzinfo=UTC)
        with self.assertRaises(ValidationError):
            AppointmentCreate.model_validate(payload)
