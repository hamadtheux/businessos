from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.core.config import settings  # noqa: E402
from app.exceptions.integration import (  # noqa: E402
    IntegrationWebhookVerificationError,
)
from app.integrations.contracts import ExternalResource  # noqa: E402
from app.integrations.credentials import CredentialMaterial  # noqa: E402
from app.integrations import action_adapters as _action_adapters  # noqa: E402,F401
from app.integrations.oauth_adapters import (  # noqa: E402
    ConfiguredOAuthConnector,
    _normalize_meta_messaging_events,
)
from app.integrations.provider_action_adapters import (  # noqa: E402
    ProviderConnectorActionAdapter,
)
from app.models.integration import IntegrationConnection, IntegrationWebhookEvent  # noqa: E402
from app.schemas.ai_action_payload import SendCustomerMessagePayload  # noqa: E402
from app.services import integrations as service  # noqa: E402


BUSINESS_A = UUID("d1000000-0000-4000-8000-000000000001")
BUSINESS_B = UUID("d2000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


def _message_payload(*, page_id: str = "page-a", sender_id: str = "psid-a", mid: str = "mid-a"):
    return {
        "object": "page",
        "entry": [{
            "id": page_id,
            "time": 1788350400000,
            "messaging": [{
                "sender": {"id": sender_id},
                "recipient": {"id": page_id},
                "timestamp": 1788350400000,
                "message": {"mid": mid, "text": "Do you have face wash?"},
            }],
        }],
    }


def _connection(business_id: UUID, page_id: str) -> IntegrationConnection:
    resource = {
        "resource_type": "facebook_page",
        "external_reference": page_id,
        "display_name": f"Page {page_id}",
    }
    return IntegrationConnection(
        id=uuid4(),
        business_id=business_id,
        connector_type="facebook",
        display_name="Meta Pages & Messenger",
        status="connected",
        authentication_state="authorized",
        health="healthy",
        credential_reference="opaque-reference",
        external_account_reference="system-user",
        external_account_display_name="System User",
        selected_resources=[resource],
        authorized_resources=[resource],
        scopes_granted=["pages_messaging", "pages_manage_metadata"],
        connected_by_user_id=None,
        connected_at=NOW,
        last_health_check_at=NOW,
        last_successful_sync_at=None,
        failure_code=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _event(connection: IntegrationConnection, suffix: str) -> IntegrationWebhookEvent:
    return IntegrationWebhookEvent(
        id=uuid4(),
        business_id=connection.business_id,
        integration_connection_id=connection.id,
        connector_type="facebook",
        external_event_id=f"mid-{suffix}",
        event_type="message_received",
        status="received",
        normalized_payload={},
        received_at=NOW,
        processed_at=None,
        failure_code=None,
        created_at=NOW,
    )


class _Verifier:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid

    def verify(self, *, body: bytes, headers) -> bool:
        return self.valid


class _Scalars:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return self.values


class _RoutingSession:
    def __init__(self, connections: list[IntegrationConnection]) -> None:
        self.connections = connections
        self.statements = []

    async def scalars(self, statement):
        self.statements.append(statement)
        return _Scalars(self.connections)


class _ScriptedHttp:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def request_json(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"Unexpected provider request: {method} {url}")
        return self.responses.pop(0)


class MetaMessengerNormalizationTests(unittest.TestCase):
    def test_real_messenger_text_becomes_provider_identity_without_guessed_contact(self) -> None:
        events = _normalize_meta_messaging_events(_message_payload(), connector_type="facebook")

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.external_event_id, "mid-a")
        self.assertEqual(event.event_type, "message_received")
        self.assertEqual(event.safe_payload, {
            "external_conversation_reference": "psid-a",
            "external_message_reference": "mid-a",
            "sender_external_reference": "psid-a",
            "external_resource_reference": "page-a",
            "content": "Do you have face wash?",
        })
        self.assertNotIn("sender_email", event.safe_payload)
        self.assertNotIn("sender_phone", event.safe_payload)

    def test_delivery_and_read_evidence_are_distinct_and_bounded(self) -> None:
        payload = {
            "object": "page",
            "entry": [{
                "id": "page-a",
                "messaging": [
                    {
                        "sender": {"id": "psid-a"},
                        "recipient": {"id": "page-a"},
                        "timestamp": 1788350400000,
                        "delivery": {"mids": ["mid-a"]},
                    },
                    {
                        "sender": {"id": "psid-a"},
                        "recipient": {"id": "page-a"},
                        "timestamp": 1788350460000,
                        "read": {"watermark": 1788350460000},
                    },
                ],
            }],
        }
        events = _normalize_meta_messaging_events(payload, connector_type="facebook")
        self.assertEqual([item.safe_payload["delivery_status"] for item in events], ["delivered", "read"])
        self.assertNotEqual(events[0].external_event_id, events[1].external_event_id)

    def test_read_event_identity_is_scoped_to_customer(self) -> None:
        def payload(sender_id: str):
            return {
                "object": "page",
                "entry": [{
                    "id": "page-a",
                    "messaging": [{
                        "sender": {"id": sender_id},
                        "recipient": {"id": "page-a"},
                        "timestamp": 1788350460000,
                        "read": {"watermark": 1788350460000},
                    }],
                }],
            }

        first = _normalize_meta_messaging_events(
            payload("psid-a"),
            connector_type="facebook",
        )[0]
        second = _normalize_meta_messaging_events(
            payload("psid-b"),
            connector_type="facebook",
        )[0]

        self.assertNotEqual(
            first.external_event_id,
            second.external_event_id,
        )

    def test_inbound_message_recipient_must_match_webhook_page(self) -> None:
        payload = _message_payload()
        payload["entry"][0]["messaging"][0]["recipient"]["id"] = "another-page"

        with self.assertRaises(Exception):
            _normalize_meta_messaging_events(
                payload,
                connector_type="facebook",
            )


class MetaSharedWebhookRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_pages_route_to_exact_connections_and_businesses(self) -> None:
        connection_a = _connection(BUSINESS_A, "page-a")
        connection_b = _connection(BUSINESS_B, "page-b")
        event_a = _event(connection_a, "a")
        event_b = _event(connection_b, "b")
        payload = {
            "object": "page",
            "entry": [
                _message_payload(page_id="page-a", mid="mid-a")["entry"][0],
                _message_payload(page_id="page-b", sender_id="psid-b", mid="mid-b")["entry"][0],
            ],
        }
        ingest = AsyncMock(side_effect=[event_a, event_b])

        with patch("app.services.integrations._ingest_webhook_for_connection", new=ingest):
            events = await service.ingest_shared_meta_webhook(
                _RoutingSession([connection_a, connection_b]),  # type: ignore[arg-type]
                body=b"signed-body",
                headers={"x-hub-signature-256": "sha256=test"},
                payload=payload,
                verifier=_Verifier(),
            )

        self.assertEqual(events, (event_a, event_b))
        routed = {
            (
                call.kwargs["connection"].id,
                call.kwargs["payload"]["entry"][0]["id"],
            )
            for call in ingest.await_args_list
        }
        self.assertEqual(
            routed,
            {(connection_a.id, "page-a"), (connection_b.id, "page-b")},
        )
        self.assertEqual({event.business_id for event in events}, {BUSINESS_A, BUSINESS_B})

    async def test_unknown_signed_page_is_acknowledged_without_dispatch(self) -> None:
        ingest = AsyncMock()
        with patch(
            "app.services.integrations._ingest_webhook_for_connection",
            new=ingest,
        ):
            events = await service.ingest_shared_meta_webhook(
                _RoutingSession([]),  # type: ignore[arg-type]
                body=b"signed-body",
                headers={},
                payload=_message_payload(page_id="unknown-page"),
                verifier=_Verifier(),
            )

        self.assertEqual(events, ())
        ingest.assert_not_awaited()

    async def test_duplicate_page_ownership_is_ambiguous_and_never_dispatched(self) -> None:
        ingest = AsyncMock()
        with patch("app.services.integrations._ingest_webhook_for_connection", new=ingest):
            with self.assertRaises(IntegrationWebhookVerificationError):
                await service.ingest_shared_meta_webhook(
                    _RoutingSession([
                        _connection(BUSINESS_A, "shared-page"),
                        _connection(BUSINESS_B, "shared-page"),
                    ]),  # type: ignore[arg-type]
                    body=b"signed-body",
                    headers={},
                    payload=_message_payload(page_id="shared-page"),
                    verifier=_Verifier(),
                )
        ingest.assert_not_awaited()

    async def test_invalid_signature_is_rejected_before_database_routing(self) -> None:
        session = _RoutingSession([])
        with self.assertRaises(IntegrationWebhookVerificationError):
            await service.ingest_shared_meta_webhook(
                session,  # type: ignore[arg-type]
                body=b"tampered",
                headers={},
                payload=_message_payload(),
                verifier=_Verifier(False),
            )
        self.assertEqual(session.statements, [])


class MetaSubscriptionAndSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_facebook_authorization_subscribes_page_messaging_fields(self) -> None:
        http = _ScriptedHttp([{"access_token": "page-token"}, {"success": True}])
        connector = ConfiguredOAuthConnector(
            connector_type="facebook",
            provider="meta",
            client_id="app-id",
            client_secret="app-secret",
            configuration=settings.model_copy(update={"meta_graph_api_version": "v26.0"}),
            http=http,  # type: ignore[arg-type]
        )
        await connector.subscribe_resources(
            CredentialMaterial(values={"access_token": "system-token"}),
            [ExternalResource("facebook_page", "page-a", "Page A")],
        )

        self.assertEqual([item["method"] for item in http.calls], ["GET", "POST"])
        self.assertTrue(str(http.calls[1]["url"]).endswith("/page-a/subscribed_apps"))
        self.assertEqual(
            http.calls[1]["data"]["subscribed_fields"],
            "messages,messaging_postbacks,message_deliveries,message_reads",
        )

    async def test_provider_send_uses_selected_page_and_returns_real_message_id(self) -> None:
        http = _ScriptedHttp([{"access_token": "page-token"}, {"message_id": "mid-outbound"}])
        adapter = ProviderConnectorActionAdapter(
            connector_type="facebook",
            configuration=settings.model_copy(update={"meta_graph_api_version": "v26.0"}),
            http=http,  # type: ignore[arg-type]
        )
        result = await adapter.execute(
            credentials=CredentialMaterial(values={"access_token": "system-token"}),
            action_type="send_customer_message",
            payload=SendCustomerMessagePayload(
                customer_ref="identity-id",
                conversation_ref="conversation-id",
                channel_resource_ref="page-a",
                message="We have three options available.",
            ),
            selected_resources=({
                "resource_type": "facebook_page",
                "external_reference": "page-a",
            },),
            delivery_target="psid-a",
            idempotency_key="message:one",
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.external_reference_id, "mid-outbound")
        self.assertTrue(
            str(http.calls[1]["url"]).endswith("/page-a/messages")
        )
        self.assertEqual(
            http.calls[1]["json_body"]["recipient"],
            {"id": "psid-a"},
        )
        self.assertEqual(
            http.calls[1]["json_body"]["messaging_type"],
            "RESPONSE",
        )


if __name__ == "__main__":
    unittest.main()
