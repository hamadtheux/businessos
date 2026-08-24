from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.api.dependencies.business import (  # noqa: E402
    BusinessAccessContext,
    get_business_access,
)
from app.db.session import get_db_session  # noqa: E402
from app.exceptions.scheduling import (  # noqa: E402
    SchedulingConflictError,
    SchedulingNotFoundError,
)
from app.main import app  # noqa: E402
from app.models.appointment import Appointment  # noqa: E402
from app.models.provider_availability_rule import ProviderAvailabilityRule  # noqa: E402
from app.models.service_provider import ServiceProvider  # noqa: E402
from app.schemas.scheduling import AvailabilitySlot  # noqa: E402


BUSINESS_ID = UUID("41000000-0000-0000-0000-000000000001")
OTHER_BUSINESS_ID = UUID("42000000-0000-0000-0000-000000000002")
USER_ID = UUID("43000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 8, 23, 8, tzinfo=UTC)


class SchedulingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = _FakeSession()
        self.original_overrides = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        async def override_access(business_id: UUID) -> BusinessAccessContext:
            if business_id != BUSINESS_ID:
                raise HTTPException(status_code=404, detail="Business not found.")
            return BusinessAccessContext(
                user=SimpleNamespace(id=USER_ID),
                business=SimpleNamespace(id=business_id, status="active"),
                membership=SimpleNamespace(
                    business_id=business_id,
                    user_id=USER_ID,
                    status="active",
                ),
            )

        self.override_access = override_access
        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_business_access] = override_access
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_overrides)

    def test_openapi_exposes_only_authenticated_business_scoped_management_routes(self) -> None:
        schema = app.openapi()
        root = "/api/v1/businesses/{business_id}/scheduling"
        required = (
            f"{root}/providers",
            f"{root}/appointment-types",
            f"{root}/providers/{{provider_id}}/appointment-types/{{appointment_type_id}}",
            f"{root}/providers/{{provider_id}}/availability-rules",
            f"{root}/providers/{{provider_id}}/availability-exceptions",
            f"{root}/availability/search",
            f"{root}/availability/next",
            f"{root}/appointments",
            f"{root}/appointments/{{appointment_id}}/reschedule",
            f"{root}/appointments/{{appointment_id}}/cancel",
        )
        for path in required:
            with self.subTest(path=path):
                self.assertIn(path, schema["paths"])
                for operation in schema["paths"][path].values():
                    self.assertTrue(operation["security"])
        self.assertNotIn("/api/v1/scheduling/public", schema["paths"])

    async def test_authentication_and_business_membership_are_required(self) -> None:
        del app.dependency_overrides[get_business_access]
        response = await self.client.get(self._url("providers"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        self._assert_private(response)

        app.dependency_overrides[get_business_access] = self.override_access
        response = await self.client.get(
            self._url("providers", business_id=OTHER_BUSINESS_ID)
        )
        self.assertEqual(response.status_code, 404)
        self._assert_private(response)

    async def test_provider_create_uses_authorized_tenant_and_commits(self) -> None:
        provider = _provider()
        with patch(
            "app.api.v1.scheduling.create_service_provider",
            new=AsyncMock(return_value=provider),
        ) as service:
            response = await self.client.post(
                self._url("providers"),
                json={
                    "display_name": "Dr. Ali",
                    "provider_type": "doctor",
                    "timezone": "Asia/Karachi",
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["business_id"], str(BUSINESS_ID))
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(self.session.commit_calls, 1)
        self._assert_private(response)

    async def test_provider_validation_rejects_invalid_timezone_before_service(self) -> None:
        with patch(
            "app.api.v1.scheduling.create_service_provider", new=AsyncMock()
        ) as service:
            response = await self.client.post(
                self._url("providers"),
                json={
                    "display_name": "Provider",
                    "provider_type": "doctor",
                    "timezone": "Invalid/Timezone",
                },
            )
        self.assertEqual(response.status_code, 422)
        service.assert_not_awaited()
        self._assert_private(response)

    async def test_availability_search_passes_structured_tenant_scoped_input(self) -> None:
        provider = _provider()
        type_id = uuid4()
        slot = AvailabilitySlot(
            provider_id=provider.id,
            provider_display_name=provider.display_name,
            appointment_type_id=type_id,
            starts_at=NOW + timedelta(days=1),
            ends_at=NOW + timedelta(days=1, minutes=30),
            timezone=provider.timezone,
        )
        with patch(
            "app.api.v1.scheduling.find_available_slots",
            new=AsyncMock(return_value=[slot]),
        ) as service:
            response = await self.client.post(
                self._url("availability/search"),
                json={
                    "appointment_type_id": str(type_id),
                    "window_start": NOW.isoformat(),
                    "window_end": (NOW + timedelta(days=2)).isoformat(),
                    "desired_results": 3,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["slots"][0]["provider_id"], str(provider.id))
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertIsNone(service.await_args.kwargs["provider_id"])
        self._assert_private(response)

    async def test_booking_uses_authenticated_actor_and_server_service_contract(self) -> None:
        appointment = _appointment()
        with patch(
            "app.api.v1.scheduling.book_appointment",
            new=AsyncMock(return_value=appointment),
        ) as service:
            response = await self.client.post(
                self._url("appointments"),
                json={
                    "provider_id": str(appointment.provider_id),
                    "appointment_type_id": str(appointment.appointment_type_id),
                    "starts_at": appointment.starts_at.isoformat(),
                    "source": "manual",
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(service.await_args.kwargs["created_by_user_id"], USER_ID)
        self.assertNotIn("ends_at", service.await_args.kwargs)
        self.assertEqual(self.session.commit_calls, 1)
        self._assert_private(response)

    async def test_booking_cannot_spoof_tenant_actor_or_end_time(self) -> None:
        with patch("app.api.v1.scheduling.book_appointment", new=AsyncMock()) as service:
            response = await self.client.post(
                self._url("appointments"),
                json={
                    "provider_id": str(uuid4()),
                    "appointment_type_id": str(uuid4()),
                    "starts_at": (NOW + timedelta(days=1)).isoformat(),
                    "source": "manual",
                    "business_id": str(OTHER_BUSINESS_ID),
                    "created_by_user_id": str(uuid4()),
                    "ends_at": (NOW + timedelta(days=1, hours=4)).isoformat(),
                },
            )
        self.assertEqual(response.status_code, 422)
        service.assert_not_awaited()
        self._assert_private(response)

    async def test_conflict_and_cross_tenant_resources_return_safe_errors(self) -> None:
        with patch(
            "app.api.v1.scheduling.book_appointment",
            new=AsyncMock(side_effect=SchedulingConflictError("private detail")),
        ):
            response = await self.client.post(
                self._url("appointments"),
                json={
                    "provider_id": str(uuid4()),
                    "appointment_type_id": str(uuid4()),
                    "starts_at": (NOW + timedelta(days=1)).isoformat(),
                },
            )
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("private detail", response.text)
        self.assertEqual(self.session.rollback_calls, 1)

        with patch(
            "app.api.v1.scheduling.get_appointment",
            new=AsyncMock(side_effect=SchedulingNotFoundError("private tenant")),
        ):
            response = await self.client.get(self._url(f"appointments/{uuid4()}"))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("private tenant", response.text)
        self._assert_private(response)

    async def test_rule_management_and_cancellation_commit_at_api_boundary(self) -> None:
        rule = ProviderAvailabilityRule(
            id=uuid4(),
            business_id=BUSINESS_ID,
            provider_id=_provider().id,
            weekday=0,
            start_local_time=datetime.min.time().replace(hour=9),
            end_local_time=datetime.min.time().replace(hour=17),
            valid_from=None,
            valid_until=None,
            active=True,
            created_at=NOW,
            updated_at=NOW,
        )
        with patch(
            "app.api.v1.scheduling.create_availability_rule",
            new=AsyncMock(return_value=rule),
        ):
            response = await self.client.post(
                self._url(f"providers/{rule.provider_id}/availability-rules"),
                json={
                    "weekday": 0,
                    "start_local_time": "09:00:00",
                    "end_local_time": "17:00:00",
                },
            )
        self.assertEqual(response.status_code, 201)

        appointment = _appointment(status="canceled")
        appointment.cancellation_reason_code = "customer_request"
        with patch(
            "app.api.v1.scheduling.cancel_appointment",
            new=AsyncMock(return_value=appointment),
        ) as service:
            response = await self.client.post(
                self._url(f"appointments/{appointment.id}/cancel"),
                json={"reason_code": "customer_request"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(self.session.commit_calls, 2)
        self._assert_private(response)

    @staticmethod
    def _url(path: str, *, business_id: UUID = BUSINESS_ID) -> str:
        return f"/api/v1/businesses/{business_id}/scheduling/{path}"

    def _assert_private(self, response: httpx.Response) -> None:
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")


class _FakeSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        self.added = []

    def add(self, value) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


def _provider() -> ServiceProvider:
    return ServiceProvider(
        id=uuid4(),
        business_id=BUSINESS_ID,
        display_name="Dr. Ali",
        provider_type="doctor",
        title=None,
        specialty="Cardiology",
        active=True,
        timezone="Asia/Karachi",
        location_reference=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _appointment(*, status: str = "confirmed") -> Appointment:
    starts_at = NOW + timedelta(days=1)
    return Appointment(
        id=uuid4(),
        business_id=BUSINESS_ID,
        provider_id=uuid4(),
        appointment_type_id=uuid4(),
        customer_id=None,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=30),
        status=status,
        source="manual",
        created_by_user_id=USER_ID,
        cancellation_reason_code=None,
        created_at=NOW,
        updated_at=NOW,
    )
