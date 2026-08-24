from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx
from fastapi import HTTPException

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.api.dependencies.ai_agent import get_ai_agent_provider  # noqa: E402
from app.api.dependencies.business import BusinessAccessContext, get_business_access  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.ai_workforce import AICommand  # noqa: E402
from app.schemas.ai_workforce import CommandResponse  # noqa: E402
from app.services.ai_workforce import route_command  # noqa: E402


BUSINESS_A = UUID("91000000-0000-4000-8000-000000000001")
BUSINESS_B = UUID("92000000-0000-4000-8000-000000000002")
USER_ID = UUID("93000000-0000-4000-8000-000000000003")


class _Session:
    def __init__(self) -> None:
        self.rollback_calls = 0

    async def rollback(self) -> None:
        self.rollback_calls += 1


class _Provider:
    provider_name = "fake"
    model = "test-model"

    async def generate(self, request):
        raise AssertionError("Provider should not be called by API contract tests")


class AIWorkforceApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original_overrides = app.dependency_overrides.copy()
        self.session = _Session()
        self.provider = _Provider()

        async def session_override():
            yield self.session

        async def access_override(business_id: UUID):
            if business_id != BUSINESS_A:
                raise HTTPException(404, "Business not found.")
            return BusinessAccessContext(
                user=SimpleNamespace(id=USER_ID),
                business=SimpleNamespace(id=business_id, status="active"),
                membership=SimpleNamespace(business_id=business_id, status="active"),
            )

        app.dependency_overrides[get_db_session] = session_override
        app.dependency_overrides[get_business_access] = access_override
        app.dependency_overrides[get_ai_agent_provider] = lambda: self.provider
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_overrides)

    async def test_authentication_is_required(self) -> None:
        del app.dependency_overrides[get_business_access]
        response = await self.client.get(
            f"/api/v1/businesses/{BUSINESS_A}/commands/suggestions"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_cross_business_access_fails_before_service(self) -> None:
        with patch(
            "app.api.v1.ai_workforce.suggested_commands", new=AsyncMock(return_value=[])
        ) as service:
            response = await self.client.get(
                f"/api/v1/businesses/{BUSINESS_B}/commands/suggestions"
            )
        self.assertEqual(response.status_code, 404)
        service.assert_not_awaited()

    async def test_read_service_receives_authorized_business_only(self) -> None:
        with patch(
            "app.api.v1.ai_workforce.suggested_commands", new=AsyncMock(return_value=[])
        ) as service:
            response = await self.client.get(
                f"/api/v1/businesses/{BUSINESS_A}/commands/suggestions"
            )
        self.assertEqual(response.status_code, 200)
        service.assert_awaited_once_with(self.session, business_id=BUSINESS_A)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_command_submission_uses_server_route_and_cannot_inject_tools(self) -> None:
        now = datetime.now(UTC)
        command = AICommand(
            id=UUID("94000000-0000-4000-8000-000000000004"),
            business_id=BUSINESS_A,
            requested_by_user_id=USER_ID,
            command_text="Show leads needing follow-up",
            resolved_role="sales",
            intent="lead_follow_up",
            status="completed",
            route_metadata={},
            execution_id=None,
            summary="Pipeline reviewed.",
            failure_code=None,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        safe_response = CommandResponse(
            id=command.id, business_id=BUSINESS_A, requested_by_user_id=USER_ID,
            command=command.command_text, status="completed",
            route=route_command(command.command_text), execution_id=None,
            summary=command.summary, failure_code=None, executions=[], proposed_actions=[],
            created_at=now, completed_at=now,
        )
        execute_mock = AsyncMock(return_value=command)
        response_mock = AsyncMock(return_value=safe_response)
        with patch("app.api.v1.ai_workforce.execute_command", new=execute_mock), patch(
            "app.api.v1.ai_workforce.command_response", new=response_mock
        ):
            response = await self.client.post(
                f"/api/v1/businesses/{BUSINESS_A}/commands",
                json={"command": command.command_text, "trigger_source": "command_center", "context_references": []},
            )
        self.assertEqual(response.status_code, 201)
        kwargs = execute_mock.await_args.kwargs
        self.assertEqual(kwargs["business_id"], BUSINESS_A)
        self.assertEqual(kwargs["user_id"], USER_ID)
        self.assertIs(kwargs["provider"], self.provider)
        self.assertFalse(hasattr(kwargs["request"], "capabilities"))

        rejected = await self.client.post(
            f"/api/v1/businesses/{BUSINESS_A}/commands",
            json={"command": "Analyze sales", "capabilities": ["arbitrary_sql"]},
        )
        self.assertEqual(rejected.status_code, 422)


if __name__ == "__main__":
    unittest.main()
