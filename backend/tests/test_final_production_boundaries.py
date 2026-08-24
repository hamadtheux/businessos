from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.api.v1.approvals import _safe_action_context  # noqa: E402
from app.exceptions.integration import (  # noqa: E402
    IntegrationCredentialUnavailableError,
)
from app.integrations.action_adapters import (  # noqa: E402
    ConnectorActionAdapterRegistry,
    ConnectorActionResult,
)
from app.integrations.action_boundary import ConnectorDispatchContext  # noqa: E402
from app.integrations.credentials import (  # noqa: E402
    AwsSecretsManagerIntegrationCredentialStore,
    CredentialMaterial,
)
from app.models.ai_action import AIAction  # noqa: E402
from app.schemas.ai_action_payload import SendEmailPayload  # noqa: E402
from app.services.action_dispatcher import dispatch_action_execution_job  # noqa: E402


BUSINESS_ID = UUID("fa000000-0000-4000-8000-000000000001")


class CredentialVaultBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_aws_secret_is_tenant_bound_rotatable_and_recoverably_revoked(self) -> None:
        client = _FakeSecretsManager()
        store = AwsSecretsManagerIntegrationCredentialStore(
            region_name="test-region-1",
            prefix="aibos/test",
            kms_key_id="alias/aibos-test",
            client=client,
        )
        original = CredentialMaterial(
            values={"access_token": "access-secret", "refresh_token": "refresh-secret"}
        )
        reference = await store.store(
            business_id=BUSINESS_ID,
            connector_type="gmail",
            purpose="oauth_credentials",
            material=original,
        )
        self.assertNotIn("access-secret", reference)
        self.assertEqual(
            (await store.retrieve(
                reference,
                business_id=BUSINESS_ID,
                connector_type="gmail",
                purpose="oauth_credentials",
            )).values,
            original.values,
        )
        with self.assertRaises(IntegrationCredentialUnavailableError) as raised:
            await store.retrieve(
                reference,
                business_id=uuid4(),
                connector_type="gmail",
                purpose="oauth_credentials",
            )
        self.assertNotIn("access-secret", str(raised.exception))

        rotated = CredentialMaterial(values={"access_token": "rotated-secret"})
        await store.rotate(
            reference,
            business_id=BUSINESS_ID,
            connector_type="gmail",
            purpose="oauth_credentials",
            material=rotated,
        )
        self.assertEqual(
            (await store.retrieve(
                reference,
                business_id=BUSINESS_ID,
                connector_type="gmail",
                purpose="oauth_credentials",
            )).values,
            rotated.values,
        )
        await store.revoke(
            reference,
            business_id=BUSINESS_ID,
            connector_type="gmail",
            purpose="oauth_credentials",
        )
        self.assertEqual(client.deleted[reference], 7)


class DispatcherBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_runs_only_after_claim_and_preflight_transactions_close(self) -> None:
        attempt_id = uuid4()
        sessions = [
            _FakeSession(SimpleNamespace(id=attempt_id, status="queued", action_type="send_email")),
            _FakeSession(),
            _FakeSession(),
        ]
        adapter = _FakeEmailAdapter(sessions[:2])
        registry = ConnectorActionAdapterRegistry({"gmail": adapter})
        context = _dispatch_context(attempt_id)
        credentials = _FakeCredentials()
        configuration = SimpleNamespace(
            job_lease_seconds=120,
            connector_dispatch_timeout_seconds=2,
        )

        with (
            patch("app.services.action_dispatcher.AsyncSessionFactory", new=_SessionFactory(sessions)),
            patch("app.services.action_dispatcher.claim_action_execution_attempt", new=AsyncMock()),
            patch(
                "app.services.action_dispatcher.prepare_connector_dispatch_context",
                new=AsyncMock(return_value=context),
            ),
            patch("app.services.action_dispatcher.record_action_execution_success", new=AsyncMock()) as success,
        ):
            result = await dispatch_action_execution_job(
                SimpleNamespace(
                    action_execution_attempt_id=attempt_id,
                    business_id=BUSINESS_ID,
                ),
                adapters=registry,
                credentials=credentials,
                configuration=configuration,
            )

        self.assertTrue(result.succeeded)
        self.assertEqual(adapter.calls, 1)
        self.assertTrue(all(session.exited for session in sessions))
        self.assertTrue(all(session.committed for session in sessions))
        self.assertEqual(credentials.references, ["vault/opaque-reference"])
        self.assertEqual(
            success.await_args.kwargs["external_reference_id"],
            "provider-message-1",
        )

    async def test_unknown_provider_outcome_is_uncertain_and_not_retryable(self) -> None:
        attempt_id = uuid4()
        sessions = [
            _FakeSession(SimpleNamespace(id=attempt_id, status="queued", action_type="send_email")),
            _FakeSession(),
            _FakeSession(),
        ]
        adapter = _FakeEmailAdapter(sessions[:2], error=TimeoutError())
        registry = ConnectorActionAdapterRegistry({"gmail": adapter})
        with (
            patch("app.services.action_dispatcher.AsyncSessionFactory", new=_SessionFactory(sessions)),
            patch("app.services.action_dispatcher.claim_action_execution_attempt", new=AsyncMock()),
            patch(
                "app.services.action_dispatcher.prepare_connector_dispatch_context",
                new=AsyncMock(return_value=_dispatch_context(attempt_id)),
            ),
            patch("app.services.action_dispatcher.record_action_execution_uncertain", new=AsyncMock()) as uncertain,
        ):
            result = await dispatch_action_execution_job(
                SimpleNamespace(
                    action_execution_attempt_id=attempt_id,
                    business_id=BUSINESS_ID,
                ),
                adapters=registry,
                credentials=_FakeCredentials(),
                configuration=SimpleNamespace(
                    job_lease_seconds=120,
                    connector_dispatch_timeout_seconds=2,
                ),
            )
        self.assertTrue(result.succeeded)
        self.assertFalse(result.retryable)
        uncertain.assert_awaited_once()
        self.assertEqual(adapter.calls, 1)


class ApprovalReviewBoundaryTests(unittest.TestCase):
    def test_review_context_is_useful_but_omits_message_body(self) -> None:
        action = AIAction(
            id=uuid4(),
            business_id=BUSINESS_ID,
            execution_id=uuid4(),
            proposal_index=0,
            action_type="send_email",
            description="Send the approved follow-up",
            risk_level="medium",
            status="pending_approval",
            proposed_requires_approval=True,
            action_payload={
                "recipient_ref": "customer-record-1",
                "subject": "Your requested follow-up",
                "body": "private message body must not enter the summary",
            },
            policy_decision="require_approval",
            policy_reason_code="external_communication",
        )
        value = _safe_action_context(action)
        self.assertEqual(value["provider_channel"], "Gmail or Microsoft Outlook")
        self.assertEqual(value["audience_or_recipient"], "customer-record-1")
        self.assertEqual(value["affected_entity"], "Customer or lead record")
        self.assertEqual(value["payload_summary"], {"subject": "Your requested follow-up"})
        self.assertNotIn("private message body", repr(value))


class _FakeSecretsManager:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: dict[str, int] = {}

    def create_secret(self, **values):
        self.values[values["Name"]] = values["SecretString"]

    def get_secret_value(self, *, SecretId: str):
        return {"SecretString": self.values[SecretId]}

    def put_secret_value(self, *, SecretId: str, SecretString: str):
        self.values[SecretId] = SecretString

    def delete_secret(self, *, SecretId: str, RecoveryWindowInDays: int):
        self.deleted[SecretId] = RecoveryWindowInDays


class _FakeSession:
    def __init__(self, scalar_value=None) -> None:
        self.scalar_value = scalar_value
        self.committed = False
        self.exited = False
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.exited = True

    async def scalar(self, *_args):
        value, self.scalar_value = self.scalar_value, None
        return value

    async def commit(self):
        self.committed = True

    def add(self, value):
        self.added.append(value)


class _SessionFactory:
    def __init__(self, sessions: list[_FakeSession]) -> None:
        self.sessions = list(sessions)

    def __call__(self):
        return self.sessions.pop(0)


class _FakeCredentials:
    def __init__(self) -> None:
        self.references: list[str] = []

    async def retrieve(self, reference: str, **_binding):
        self.references.append(reference)
        return CredentialMaterial(values={"access_token": "server-only-secret"})


class _FakeEmailAdapter:
    connector_type = "gmail"
    supported_action_types = frozenset({"send_email"})

    def __init__(self, pre_provider_sessions: list[_FakeSession], error=None) -> None:
        self.pre_provider_sessions = pre_provider_sessions
        self.error = error
        self.calls = 0

    async def execute(self, **values):
        self.calls += 1
        if not all(session.committed and session.exited for session in self.pre_provider_sessions):
            raise AssertionError("provider called before database transaction closed")
        self.assert_stable_input(values)
        if self.error is not None:
            raise self.error
        return ConnectorActionResult(
            succeeded=True,
            external_reference_id="provider-message-1",
        )

    @staticmethod
    def assert_stable_input(values) -> None:
        if values["idempotency_key"] != "ai-action:stable-attempt":
            raise AssertionError("stable idempotency key was not forwarded")


def _dispatch_context(attempt_id) -> ConnectorDispatchContext:
    return ConnectorDispatchContext(
        business_id=BUSINESS_ID,
        action_id=uuid4(),
        approval_id=uuid4(),
        attempt_id=attempt_id,
        connection_id=uuid4(),
        action_type="send_email",
        connector_type="gmail",
        idempotency_key="ai-action:stable-attempt",
        credential_reference="vault/opaque-reference",
        selected_resources=({"resource_type": "mailbox", "external_reference": "me"},),
        payload=SendEmailPayload(
            recipient_ref="customer-record-1",
            subject="Follow-up",
            body="Approved body",
        ),
        delivery_target="customer@example.test",
    )
