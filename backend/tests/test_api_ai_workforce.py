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
from app.exceptions.ai_workforce import (  # noqa: E402
    AIWorkforceConflictError,
    AIWorkforceNotFoundError,
    AIWorkforcePersistenceError,
)
from app.services.automation_copilot import OpportunityAnalysisOutcome  # noqa: E402
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
                membership=SimpleNamespace(
                    business_id=business_id,
                    status="active",
                    role="owner",
                ),
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

    async def test_opportunity_analysis_uses_authorized_scope_and_server_provider(self) -> None:
        opportunity_id = UUID("95000000-0000-4000-8000-000000000005")
        execution_id = UUID("96000000-0000-4000-8000-000000000006")
        action_id = UUID("97000000-0000-4000-8000-000000000007")
        approval_id = UUID("98000000-0000-4000-8000-000000000008")
        outcome = OpportunityAnalysisOutcome(
            execution=SimpleNamespace(
                id=execution_id,
                business_id=BUSINESS_A,
                opportunity_id=opportunity_id,
                role="business_manager",
                status="needs_approval",
                output_summary="Observed evidence reviewed.",
                recommendations=["Review retained revenue by channel."],
                failure_code=None,
                provider_name="must-not-be-returned",
                provider_request_id="must-not-be-returned",
            ),
            actions=(SimpleNamespace(
                id=action_id,
                business_id=BUSINESS_A,
                execution_id=execution_id,
                action_type="update_crm",
                description="Record an internal review note.",
                risk_level="low",
                status="pending_approval",
                policy_decision="require_approval",
                proposed_requires_approval=True,
            ),),
            approvals=(SimpleNamespace(
                id=approval_id,
                business_id=BUSINESS_A,
                action_id=action_id,
                status="pending",
                reason_code="human_approval_required",
            ),),
            created=True,
        )
        with patch(
            "app.api.v1.ai_workforce.analyze_business_opportunity",
            new=AsyncMock(return_value=outcome),
        ) as service:
            response = await self.client.post(
                self._analysis_url(opportunity_id),
                json={"analysis_request_key": "dashboard:delivery-1"},
            )
        self.assertEqual(response.status_code, 200)
        kwargs = service.await_args.kwargs
        self.assertEqual(kwargs["business_id"], BUSINESS_A)
        self.assertEqual(kwargs["opportunity_id"], opportunity_id)
        self.assertEqual(kwargs["requested_by_user_id"], USER_ID)
        self.assertEqual(kwargs["analysis_request_key"], "dashboard:delivery-1")
        self.assertEqual(kwargs["trigger_type"], "api")
        self.assertIs(kwargs["provider"], self.provider)
        body = response.json()
        self.assertTrue(body["created"])
        self.assertEqual(body["status"], "needs_approval")
        self.assertEqual(body["proposed_actions"][0]["approval"]["status"], "pending")
        self.assertNotIn("provider_name", body)
        self.assertNotIn("provider_request_id", body)
        self.assertNotIn("analysis_request_key", body)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_opportunity_analysis_request_rejects_secrets_and_invalid_keys(self) -> None:
        opportunity_id = UUID("95000000-0000-4000-8000-000000000005")
        with patch(
            "app.api.v1.ai_workforce.analyze_business_opportunity",
            new=AsyncMock(),
        ) as service:
            for payload in (
                {},
                {"analysis_request_key": "contains spaces"},
                {
                    "analysis_request_key": "valid-key",
                    "provider_api_key": "secret",
                },
                {
                    "analysis_request_key": "valid-key",
                    "provider": "arbitrary-provider",
                },
            ):
                with self.subTest(payload=payload):
                    response = await self.client.post(
                        self._analysis_url(opportunity_id),
                        json=payload,
                    )
                    self.assertEqual(response.status_code, 422)
        service.assert_not_awaited()

    async def test_opportunity_analysis_requires_owner_or_admin_role(self) -> None:
        async def member_access(business_id: UUID):
            return BusinessAccessContext(
                user=SimpleNamespace(id=USER_ID),
                business=SimpleNamespace(id=business_id, status="active"),
                membership=SimpleNamespace(
                    business_id=business_id,
                    status="active",
                    role="member",
                ),
            )

        app.dependency_overrides[get_business_access] = member_access
        with patch(
            "app.api.v1.ai_workforce.analyze_business_opportunity",
            new=AsyncMock(),
        ) as service:
            response = await self.client.post(
                self._analysis_url(UUID("95000000-0000-4000-8000-000000000005")),
                json={"analysis_request_key": "member-request"},
            )
        self.assertEqual(response.status_code, 403)
        service.assert_not_awaited()

    async def test_opportunity_analysis_cross_tenant_fails_before_service(self) -> None:
        with patch(
            "app.api.v1.ai_workforce.analyze_business_opportunity",
            new=AsyncMock(),
        ) as service:
            response = await self.client.post(
                self._analysis_url(UUID("95000000-0000-4000-8000-000000000005"), BUSINESS_B),
                json={"analysis_request_key": "cross-tenant-request"},
            )
        self.assertEqual(response.status_code, 404)
        service.assert_not_awaited()

    async def test_opportunity_analysis_requires_authentication(self) -> None:
        del app.dependency_overrides[get_business_access]
        with patch(
            "app.api.v1.ai_workforce.analyze_business_opportunity",
            new=AsyncMock(),
        ) as service:
            response = await self.client.post(
                self._analysis_url(UUID("95000000-0000-4000-8000-000000000005")),
                json={"analysis_request_key": "unauthenticated-request"},
            )
        self.assertEqual(response.status_code, 401)
        service.assert_not_awaited()
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_opportunity_analysis_returns_existing_running_or_failed_truthfully(self) -> None:
        opportunity_id = UUID("95000000-0000-4000-8000-000000000005")
        for status_value, failure_code in (
            ("running", None),
            ("failed", "provider_unavailable"),
        ):
            with self.subTest(status=status_value):
                outcome = OpportunityAnalysisOutcome(
                    execution=SimpleNamespace(
                        id=UUID("96000000-0000-4000-8000-000000000006"),
                        business_id=BUSINESS_A,
                        opportunity_id=opportunity_id,
                        role="business_manager",
                        status=status_value,
                        output_summary=None,
                        recommendations=[],
                        failure_code=failure_code,
                    ),
                    actions=(),
                    approvals=(),
                    created=False,
                    failure_code=failure_code,
                )
                with patch(
                    "app.api.v1.ai_workforce.analyze_business_opportunity",
                    new=AsyncMock(return_value=outcome),
                ):
                    response = await self.client.post(
                        self._analysis_url(opportunity_id),
                        json={"analysis_request_key": f"existing-{status_value}"},
                    )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()["created"])
                self.assertEqual(response.json()["status"], status_value)
                self.assertEqual(response.json()["failure_code"], failure_code)

    async def test_opportunity_analysis_errors_are_sanitized(self) -> None:
        opportunity_id = UUID("95000000-0000-4000-8000-000000000005")
        cases = (
            (AIWorkforceNotFoundError("private tenant detail"), 404),
            (AIWorkforceConflictError("private lifecycle detail"), 409),
            (AIWorkforcePersistenceError("private database detail"), 503),
        )
        for error, expected_status in cases:
            with self.subTest(error=type(error).__name__), patch(
                "app.api.v1.ai_workforce.analyze_business_opportunity",
                new=AsyncMock(side_effect=error),
            ):
                response = await self.client.post(
                    self._analysis_url(opportunity_id),
                    json={"analysis_request_key": "safe-error-key"},
                )
                self.assertEqual(response.status_code, expected_status)
                self.assertNotIn("private", response.text)

    def test_opportunity_analysis_openapi_is_authenticated_and_has_no_provider_fields(self) -> None:
        path = "/api/v1/businesses/{business_id}/opportunities/{opportunity_id}/analyze"
        operation = app.openapi()["paths"][path]["post"]
        self.assertTrue(operation["security"])
        request_schema_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        schema_name = request_schema_ref.rsplit("/", 1)[-1]
        properties = app.openapi()["components"]["schemas"][schema_name]["properties"]
        self.assertEqual(set(properties), {"analysis_request_key"})

    @staticmethod
    def _analysis_url(opportunity_id: UUID, business_id: UUID = BUSINESS_A) -> str:
        return (
            f"/api/v1/businesses/{business_id}/opportunities/"
            f"{opportunity_id}/analyze"
        )


if __name__ == "__main__":
    unittest.main()
