from __future__ import annotations

import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from datetime import UTC, datetime
from uuid import UUID, uuid4

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.core.config import Settings  # noqa: E402
from app.main import app  # noqa: E402
from app.integrations.action_adapters import (  # noqa: E402
    ConnectorActionAdapterRegistry,
    ConnectorActionResult,
)
from app.models.integration import IntegrationConnection  # noqa: E402
from app.services.activation_readiness import (  # noqa: E402
    ActivationReadinessFacts,
    _write_ready_connection,
    activation_readiness,
    build_activation_readiness,
)


NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)
BUSINESS_ID = UUID("8b000000-0000-4000-8000-000000000001")


class Phase8BActivationReadinessTests(unittest.TestCase):
    def test_activation_route_is_authenticated_and_typed(self) -> None:
        operation = app.openapi()["paths"][
            "/api/v1/businesses/{business_id}/activation-readiness"
        ]["get"]

        self.assertTrue(operation["security"])
        schema = operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(
            schema["$ref"],
            "#/components/schemas/ActivationReadinessResponse",
        )

    def test_all_required_evidence_can_reach_ready_without_a_fake_score(self) -> None:
        response = build_activation_readiness(_facts(), generated_at=NOW)

        self.assertTrue(response.activation_ready)
        self.assertEqual(response.overall_status, "ready")
        self.assertEqual(response.ready_required_checks, response.required_checks)
        self.assertNotIn("percent", response.model_dump_json())
        optional = {item.id: item for item in response.checks if not item.required}
        self.assertEqual(optional["commerce"].state, "not_applicable")
        self.assertEqual(optional["website_widget"].state, "not_applicable")

    def test_production_and_live_provider_evidence_fail_closed_independently(self) -> None:
        response = build_activation_readiness(
            replace(
                _facts(),
                environment="staging",
                activation_gate_enabled=False,
                openai_configured=False,
                credential_store_configured=False,
                communication_authenticated=0,
                communication_healthy=0,
                communication_write_ready=0,
                communication_write_ready_providers=(),
                worker_heartbeat_fresh=False,
            ),
            generated_at=NOW,
        )
        states = {item.id: item.state for item in response.checks}

        self.assertFalse(response.activation_ready)
        self.assertEqual(states["production_environment"], "action_needed")
        self.assertEqual(states["ai_runtime"], "action_needed")
        self.assertEqual(states["provider_authentication"], "action_needed")
        self.assertEqual(states["provider_write"], "action_needed")
        self.assertEqual(states["worker"], "action_needed")
        serialized = response.model_dump_json()
        for forbidden in ("credential_reference", "access_token", "api_key"):
            self.assertNotIn(forbidden, serialized)

    def test_commerce_and_enabled_widget_become_required_only_when_applicable(self) -> None:
        response = build_activation_readiness(
            replace(
                _facts(),
                commerce_applicable=True,
                active_catalog_items=0,
                commerce_healthy_connections=0,
                chatbot_enabled=True,
                chatbot_allowed_domains=0,
            ),
            generated_at=NOW,
        )
        checks = {item.id: item for item in response.checks}

        self.assertTrue(checks["catalog"].required)
        self.assertEqual(checks["catalog"].state, "action_needed")
        self.assertTrue(checks["commerce"].required)
        self.assertTrue(checks["website_widget"].required)
        self.assertEqual(checks["website_widget"].state, "action_needed")

    def test_write_readiness_requires_scope_resource_adapter_and_kill_switch(self) -> None:
        connection = IntegrationConnection(
            id=uuid4(),
            business_id=BUSINESS_ID,
            connector_type="gmail",
            display_name="Controlled mailbox",
            status="connected",
            authentication_state="authorized",
            health="healthy",
            credential_reference="opaque/server/reference",
            selected_resources=[
                {
                    "resource_type": "mailbox",
                    "external_reference": "me",
                    "display_name": "Controlled mailbox",
                }
            ],
            scopes_granted=[
                "openid",
                "email",
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send",
            ],
            last_health_check_at=NOW,
        )
        adapter = _EmailAdapter()
        adapters = ConnectorActionAdapterRegistry({"gmail": adapter})
        enabled = _write_settings(enabled=True)

        self.assertTrue(
            _write_ready_connection(
                connection,
                configuration=enabled,
                action_adapters=adapters,
            )
        )
        connection.scopes_granted = ["openid"]
        self.assertFalse(
            _write_ready_connection(
                connection,
                configuration=enabled,
                action_adapters=adapters,
            )
        )
        connection.scopes_granted = [
            "https://www.googleapis.com/auth/gmail.send"
        ]
        connection.selected_resources = []
        self.assertFalse(
            _write_ready_connection(
                connection,
                configuration=enabled,
                action_adapters=adapters,
            )
        )
        connection.selected_resources = [
            {
                "resource_type": "mailbox",
                "external_reference": "me",
                "display_name": "Controlled mailbox",
            }
        ]
        self.assertFalse(
            _write_ready_connection(
                connection,
                configuration=_write_settings(enabled=False),
                action_adapters=adapters,
            )
        )


class Phase8BProviderWriteAcceptanceEvidenceTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_configured_write_without_acceptance_evidence_fails_closed(
        self,
    ) -> None:
        connection = _gmail_connection()
        session = _ReadinessSession(
            connection=connection,
            accepted_connection_ids=[],
        )

        response = await activation_readiness(
            session,
            business=_readiness_business(),
            configuration=_write_settings(enabled=True),
            action_adapters=ConnectorActionAdapterRegistry(
                {"gmail": _EmailAdapter()}
            ),
            now=NOW,
        )

        checks = {item.id: item for item in response.checks}

        # Authentication, health, scopes, resources, write switch, and adapter
        # support are deliberately all valid here.
        self.assertEqual(
            checks["provider_authentication"].state,
            "ready",
        )
        self.assertEqual(
            checks["provider_health"].state,
            "ready",
        )

        # But configuration alone must never masquerade as WRITE_ACCEPTED.
        self.assertEqual(
            checks["provider_write"].state,
            "action_needed",
        )
        self.assertEqual(
            checks["provider_write"].evidence["write_ready"],
            0,
        )

    async def test_exact_connection_acceptance_evidence_makes_write_ready(
        self,
    ) -> None:
        connection = _gmail_connection()
        session = _ReadinessSession(
            connection=connection,
            accepted_connection_ids=[connection.id],
        )

        response = await activation_readiness(
            session,
            business=_readiness_business(),
            configuration=_write_settings(enabled=True),
            action_adapters=ConnectorActionAdapterRegistry(
                {"gmail": _EmailAdapter()}
            ),
            now=NOW,
        )

        check = {
            item.id: item for item in response.checks
        }["provider_write"]

        self.assertEqual(check.state, "ready")
        self.assertEqual(check.evidence["write_ready"], 1)
        self.assertEqual(check.evidence["providers"], "gmail")

    async def test_unrelated_acceptance_evidence_does_not_unlock_connection(
        self,
    ) -> None:
        connection = _gmail_connection()
        session = _ReadinessSession(
            connection=connection,
            accepted_connection_ids=[uuid4()],
        )

        response = await activation_readiness(
            session,
            business=_readiness_business(),
            configuration=_write_settings(enabled=True),
            action_adapters=ConnectorActionAdapterRegistry(
                {"gmail": _EmailAdapter()}
            ),
            now=NOW,
        )

        check = {
            item.id: item for item in response.checks
        }["provider_write"]

        self.assertEqual(check.state, "action_needed")
        self.assertEqual(check.evidence["write_ready"], 0)


class _ReadinessSession:
    """Minimal async session double for activation-readiness evidence tests."""

    def __init__(
        self,
        *,
        connection: IntegrationConnection,
        accepted_connection_ids: list[UUID],
    ) -> None:
        self.connection = connection
        self.accepted_connection_ids = accepted_connection_ids
        self.execute_calls = 0
        self.scalars_calls = 0

    async def execute(self, _statement):
        self.execute_calls += 1

        if self.execute_calls == 1:
            return _OneMappingResult(
                {
                    "active_knowledge_entries": 2,
                    "active_catalog_items": 0,
                    "enabled_ai_agents": 1,
                    "active_workflows": 1,
                    "branding_records": 1,
                }
            )

        if self.execute_calls == 2:
            return _RowsResult(
                [
                    ("worker", NOW),
                    ("scheduler", NOW),
                ]
            )

        raise AssertionError(
            f"Unexpected execute call {self.execute_calls}"
        )

    async def scalars(self, _statement):
        self.scalars_calls += 1

        if self.scalars_calls == 1:
            return _RowsResult([self.connection])

        if self.scalars_calls == 2:
            return _RowsResult(self.accepted_connection_ids)

        raise AssertionError(
            f"Unexpected scalars call {self.scalars_calls}"
        )

    async def scalar(self, _statement):
        # Website chatbot is disabled/not configured in this focused fixture.
        return None


class _OneMappingResult:
    def __init__(self, values: dict[str, int]) -> None:
        self.values = values

    def one(self):
        return SimpleNamespace(_mapping=self.values)


class _RowsResult:
    def __init__(self, values) -> None:
        self.values = list(values)

    def all(self):
        return list(self.values)


def _readiness_business():
    return SimpleNamespace(
        id=BUSINESS_ID,
        business_type="real_estate",
        status="active",
        name="Phase 8B Acceptance Business",
        description="Production activation readiness test business.",
        brand_voice="Professional and concise.",
    )


def _gmail_connection() -> IntegrationConnection:
    return IntegrationConnection(
        id=uuid4(),
        business_id=BUSINESS_ID,
        connector_type="gmail",
        display_name="Controlled mailbox",
        status="connected",
        authentication_state="authorized",
        health="healthy",
        credential_reference="opaque/server/reference",
        selected_resources=[
            {
                "resource_type": "mailbox",
                "external_reference": "me",
                "display_name": "Controlled mailbox",
            }
        ],
        scopes_granted=[
            "openid",
            "email",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ],
        connected_at=NOW,
        last_health_check_at=NOW,
    )


class _EmailAdapter:
    connector_type = "gmail"
    supported_action_types = frozenset({"send_email"})

    async def execute(self, **_values) -> ConnectorActionResult:
        return ConnectorActionResult(
            succeeded=True,
            external_reference_id="controlled-message",
        )


def _write_settings(*, enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://database.invalid/test",
        auth_secret_key="phase8b-write-readiness-secret-with-more-than-32-bytes",
        integration_credential_backend="aws_secrets_manager",
        integration_secret_region="us-east-1",
        external_connector_writes_enabled=enabled,
        external_connector_write_mode="enabled" if enabled else "disabled",
    )


def _facts() -> ActivationReadinessFacts:
    return ActivationReadinessFacts(
        environment="production",
        activation_gate_enabled=True,
        database_available=True,
        business_active=True,
        profile_ready=True,
        branding_ready=True,
        active_knowledge_entries=2,
        active_catalog_items=0,
        enabled_ai_agents=1,
        active_workflows=1,
        openai_configured=True,
        credential_store_configured=True,
        communication_connections=1,
        communication_authenticated=1,
        communication_healthy=1,
        communication_write_ready=1,
        communication_write_ready_providers=("gmail",),
        commerce_applicable=False,
        commerce_healthy_connections=0,
        worker_last_heartbeat_at=NOW,
        scheduler_last_heartbeat_at=NOW,
        worker_heartbeat_fresh=True,
        scheduler_heartbeat_fresh=True,
        approvals_fail_closed=True,
        chatbot_enabled=False,
        chatbot_allowed_domains=0,
    )


if __name__ == "__main__":
    unittest.main()
