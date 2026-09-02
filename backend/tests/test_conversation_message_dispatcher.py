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

from app.integrations.action_adapters import (
    ConnectorActionAdapterRegistry,
    ConnectorActionResult,
    ConnectorRejectedError,
)
from app.integrations.credentials import CredentialMaterial
from app.schemas.ai_action_payload import SendEmailPayload
from app.services.conversation_message_dispatcher import (
    ConversationMessageDispatchContext,
    _record_submitted,
    dispatch_conversation_message_job,
)


BUSINESS_ID = UUID("fb000000-0000-4000-8000-000000000001")
USER_ID = UUID("fb000000-0000-4000-8000-000000000002")
CONVERSATION_ID = UUID("fb000000-0000-4000-8000-000000000003")
CONNECTION_ID = UUID("fb000000-0000-4000-8000-000000000004")


class _FakeSession:
    def __init__(self, values):
        self.values = list(values)
        self.committed = False
        self.exited = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.exited = True

    async def scalar(self, _statement):
        return self.values.pop(0) if self.values else None

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    def add(self, _value):
        return None


class _SessionFactory:
    def __init__(self, sessions):
        self.sessions = list(sessions)

    def __call__(self):
        if not self.sessions:
            raise AssertionError("unexpected database session")
        return self.sessions.pop(0)


class _FakeCredentials:
    def __init__(self):
        self.calls = 0

    async def retrieve(self, reference, **binding):
        self.calls += 1
        assert reference == "vault/manual-message"
        assert binding["business_id"] == BUSINESS_ID
        assert binding["connector_type"] == "gmail"
        assert binding["purpose"] == "oauth_credentials"
        return CredentialMaterial(
            values={"access_token": "test-secret"}
        )


class _FakeAdapter:
    connector_type = "gmail"
    supported_action_types = frozenset({"send_email"})

    def __init__(self, claim_session, *, error=None):
        self.claim_session = claim_session
        self.error = error
        self.calls = 0
        self.expected_message_id = None

    async def execute(self, **values):
        self.calls += 1

        if not self.claim_session.committed:
            raise AssertionError("provider called before dispatching commit")

        if not self.claim_session.exited:
            raise AssertionError("provider called before DB session closed")

        assert values["idempotency_key"] == (
            f"manual-message:{self.expected_message_id}"
        )

        if self.error is not None:
            raise self.error

        return ConnectorActionResult(
            succeeded=True,
            external_reference_id="provider-message-1",
        )


def _message(status):
    return SimpleNamespace(
        id=uuid4(),
        business_id=BUSINESS_ID,
        conversation_id=CONVERSATION_ID,
        delivery_status=status,
        direction="outbound",
        sender_type="user",
        sender_user_id=USER_ID,
        client_request_id=uuid4(),
        content="Human authorized reply",
        external_reference=None,
    )


def _job(message_id):
    return SimpleNamespace(
        id=uuid4(),
        business_id=BUSINESS_ID,
        conversation_message_id=message_id,
        job_type="dispatch_conversation_message",
    )


def _context(message_id):
    return ConversationMessageDispatchContext(
        business_id=BUSINESS_ID,
        message_id=message_id,
        conversation_id=CONVERSATION_ID,
        connection_id=CONNECTION_ID,
        connector_type="gmail",
        credential_reference="vault/manual-message",
        action_type="send_email",
        payload=SendEmailPayload(
            recipient_ref="customer-1",
            subject="Follow-up",
            body="Human authorized reply",
            conversation_ref=str(CONVERSATION_ID),
        ),
        selected_resources=(
            {
                "resource_type": "mailbox",
                "external_reference": "me",
            },
        ),
        delivery_target="customer@example.test",
    )


class ConversationMessageDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_runs_only_after_dispatching_commit(self):
        message = _message("queued")
        conversation = SimpleNamespace(id=CONVERSATION_ID)
        session = _FakeSession([message, conversation, message])

        adapter = _FakeAdapter(session)
        adapter.expected_message_id = message.id
        registry = ConnectorActionAdapterRegistry({"gmail": adapter})
        credentials = _FakeCredentials()
        submitted = AsyncMock()

        with (
            patch(
                "app.services.conversation_message_dispatcher.AsyncSessionFactory",
                new=_SessionFactory([session]),
            ),
            patch(
                "app.services.conversation_message_dispatcher._prepare_dispatch_context",
                new=AsyncMock(return_value=_context(message.id)),
            ),
            patch(
                "app.services.conversation_message_dispatcher._record_submitted",
                new=submitted,
            ),
            patch(
                "app.services.conversation_message_dispatcher.record_audit",
            ),
        ):
            outcome = await dispatch_conversation_message_job(
                _job(message.id),
                adapters=registry,
                credentials=credentials,
                configuration=SimpleNamespace(),
            )

        self.assertTrue(outcome.succeeded)
        self.assertFalse(outcome.retryable)
        self.assertEqual(message.delivery_status, "dispatching")
        self.assertTrue(session.committed)
        self.assertTrue(session.exited)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(credentials.calls, 1)

        submitted.assert_awaited_once_with(
            business_id=BUSINESS_ID,
            message_id=message.id,
            external_reference="provider-message-1",
        )

    async def test_recovered_dispatching_message_never_replays_provider(self):
        message = _message("dispatching")
        session = _FakeSession([message, message])

        adapter = _FakeAdapter(session)
        registry = ConnectorActionAdapterRegistry({"gmail": adapter})
        credentials = _FakeCredentials()

        with (
            patch(
                "app.services.conversation_message_dispatcher.AsyncSessionFactory",
                new=_SessionFactory([session]),
            ),
            patch(
                "app.services.conversation_message_dispatcher.record_audit",
            ),
        ):
            outcome = await dispatch_conversation_message_job(
                _job(message.id),
                adapters=registry,
                credentials=credentials,
                configuration=SimpleNamespace(),
            )

        self.assertTrue(outcome.succeeded)
        self.assertEqual(message.delivery_status, "uncertain")
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(credentials.calls, 0)

    async def test_provider_rejection_is_definite_failure(self):
        message = _message("queued")
        conversation = SimpleNamespace(id=CONVERSATION_ID)
        session = _FakeSession([message, conversation, message])

        adapter = _FakeAdapter(
            session,
            error=ConnectorRejectedError("provider_rejected"),
        )
        adapter.expected_message_id = message.id

        failure = AsyncMock()
        uncertain = AsyncMock()

        with (
            patch(
                "app.services.conversation_message_dispatcher.AsyncSessionFactory",
                new=_SessionFactory([session]),
            ),
            patch(
                "app.services.conversation_message_dispatcher._prepare_dispatch_context",
                new=AsyncMock(return_value=_context(message.id)),
            ),
            patch(
                "app.services.conversation_message_dispatcher._record_definite_failure",
                new=failure,
            ),
            patch(
                "app.services.conversation_message_dispatcher._record_uncertain",
                new=uncertain,
            ),
            patch(
                "app.services.conversation_message_dispatcher.record_audit",
            ),
        ):
            outcome = await dispatch_conversation_message_job(
                _job(message.id),
                adapters=ConnectorActionAdapterRegistry({"gmail": adapter}),
                credentials=_FakeCredentials(),
                configuration=SimpleNamespace(),
            )

        self.assertTrue(outcome.succeeded)
        self.assertEqual(adapter.calls, 1)

        failure.assert_awaited_once_with(
            business_id=BUSINESS_ID,
            message_id=message.id,
            reason="provider_rejected",
        )
        uncertain.assert_not_awaited()

    async def test_unknown_provider_outcome_becomes_uncertain(self):
        message = _message("queued")
        conversation = SimpleNamespace(id=CONVERSATION_ID)
        session = _FakeSession([message, conversation, message])

        adapter = _FakeAdapter(
            session,
            error=TimeoutError("response lost"),
        )
        adapter.expected_message_id = message.id

        uncertain = AsyncMock()

        with (
            patch(
                "app.services.conversation_message_dispatcher.AsyncSessionFactory",
                new=_SessionFactory([session]),
            ),
            patch(
                "app.services.conversation_message_dispatcher._prepare_dispatch_context",
                new=AsyncMock(return_value=_context(message.id)),
            ),
            patch(
                "app.services.conversation_message_dispatcher._record_uncertain",
                new=uncertain,
            ),
            patch(
                "app.services.conversation_message_dispatcher.record_audit",
            ),
        ):
            outcome = await dispatch_conversation_message_job(
                _job(message.id),
                adapters=ConnectorActionAdapterRegistry({"gmail": adapter}),
                credentials=_FakeCredentials(),
                configuration=SimpleNamespace(),
            )

        self.assertTrue(outcome.succeeded)
        self.assertFalse(outcome.retryable)
        self.assertEqual(adapter.calls, 1)

        uncertain.assert_awaited_once_with(
            business_id=BUSINESS_ID,
            message_id=message.id,
            reason="provider_outcome_unknown",
        )

    async def test_read_state_keeps_rank_and_stores_provider_reference(self):
        message = _message("read")
        session = _FakeSession([message])

        with (
            patch(
                "app.services.conversation_message_dispatcher.AsyncSessionFactory",
                new=_SessionFactory([session]),
            ),
            patch(
                "app.services.conversation_message_dispatcher.record_audit",
            ),
            patch(
                "app.services.conversation_message_dispatcher.record_automation_event",
            ),
        ):
            await _record_submitted(
                business_id=BUSINESS_ID,
                message_id=message.id,
                external_reference="provider-read-1",
            )

        self.assertEqual(message.external_reference, "provider-read-1")
        self.assertEqual(message.delivery_status, "read")
        self.assertTrue(session.committed)


if __name__ == "__main__":
    unittest.main()
