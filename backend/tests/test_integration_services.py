from __future__ import annotations

import hashlib
import os
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.core.config import settings  # noqa: E402
from app.exceptions.integration import IntegrationStateError, IntegrationValidationError, IntegrationWebhookVerificationError  # noqa: E402
from app.integrations.adapters import ConnectorAdapterRegistry  # noqa: E402
from app.integrations.contracts import (  # noqa: E402
    AuthorizationExchange,
    AuthorizationRequest,
    ConnectionHealthResult,
    CredentialRefreshResult,
    ExternalIdentity,
    ExternalResource,
    NormalizedAdPerformance,
    NormalizedIntegrationEvent,
)
from app.integrations.credentials import CredentialMaterial, InMemoryIntegrationCredentialStore  # noqa: E402
from app.models.conversation import Conversation, ConversationMessage  # noqa: E402
from app.models.customer import Customer  # noqa: E402
from app.models.integration import IntegrationConnection, IntegrationEntityLink, IntegrationOAuthState, IntegrationWebhookEvent  # noqa: E402
from app.models.marketing import MarketingPerformance  # noqa: E402
from app.schemas.integration import ResourceSelectionRequest  # noqa: E402
from app.services import integrations as service  # noqa: E402


BUSINESS_ID = UUID("c1000000-0000-4000-8000-000000000001")
USER_ID = UUID("c2000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


class IntegrationOAuthServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorization_start_uses_random_hashed_state_and_pkce_reference(self) -> None:
        adapter = _FakeConnector()
        adapters = ConnectorAdapterRegistry({"gmail": adapter})
        credential_store = InMemoryIntegrationCredentialStore()
        configuration = settings.model_copy(update={
            "integration_oauth_callback_url": "https://api.example.test/api/v1/integrations/oauth/gmail/callback",
        })
        states: list[str] = []
        for _ in range(2):
            session = _Session([0, None])
            result = await service.begin_authorization(
                session,  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                user_id=USER_ID,
                connector_type="gmail",
                redirect_target="/integrations",
                adapters=adapters,
                credentials=credential_store,
                configuration=configuration,
            )
            state = adapter.authorization_requests[-1].state
            oauth_state = next(item for item in session.added if isinstance(item, IntegrationOAuthState))
            connection = next(item for item in session.added if isinstance(item, IntegrationConnection))
            self.assertEqual(oauth_state.state_hash, hashlib.sha256(state.encode()).hexdigest())
            self.assertNotEqual(oauth_state.state_hash, state)
            self.assertNotIn(state, oauth_state.pkce_verifier_reference)
            self.assertEqual(oauth_state.redirect_target, "/integrations")
            self.assertEqual(connection.status, "pending")
            self.assertEqual(result.connector_type, "gmail")
            states.append(state)
        self.assertNotEqual(states[0], states[1])

    async def test_callback_consumes_state_stores_opaque_reference_and_rejects_replay(self) -> None:
        adapter = _FakeConnector()
        adapters = ConnectorAdapterRegistry({"gmail": adapter})
        credential_store = InMemoryIntegrationCredentialStore()
        raw_state = "provider-returned-state"
        verifier_reference = await credential_store.store(
            business_id=BUSINESS_ID,
            connector_type="gmail",
            purpose="oauth_pkce",
            material=CredentialMaterial(values={"code_verifier": "server-verifier"}),
        )
        oauth_state = IntegrationOAuthState(
            id=uuid4(), business_id=BUSINESS_ID, connector_type="gmail", user_id=USER_ID,
            state_hash=hashlib.sha256(raw_state.encode()).hexdigest(),
            pkce_verifier_reference=verifier_reference, redirect_target="/integrations",
            expires_at=NOW + timedelta(minutes=5), consumed_at=None, created_at=NOW,
        )
        connection = _connection(status="pending", authentication_state="authorization_pending", health="not_checked")
        session = _Session([oauth_state, connection])
        configuration = settings.model_copy(update={
            "integration_oauth_callback_url": "https://api.example.test/api/v1/integrations/oauth/gmail/callback",
        })
        with patch("app.services.integrations.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            result = await service.complete_authorization(
                session,  # type: ignore[arg-type]
                connector_type=None,
                state=raw_state,
                code="one-use-code",
                adapters=adapters,
                credentials=credential_store,
                configuration=configuration,
            )
        self.assertEqual(result.status, "connected")
        self.assertEqual(connection.status, "connected")
        self.assertEqual(connection.connected_by_user_id, USER_ID)
        self.assertTrue(connection.credential_reference.startswith("test-credential:"))
        self.assertIsNotNone(oauth_state.consumed_at)
        with self.assertRaises(IntegrationStateError):
            await service.complete_authorization(
                _Session([oauth_state]),  # type: ignore[arg-type]
                connector_type="gmail", state=raw_state, code="replayed-code",
                adapters=adapters, credentials=credential_store, configuration=configuration,
            )

    async def test_callback_accepts_google_canonical_identity_scope_alias(self) -> None:
        class GoogleCanonicalScopeConnector(_FakeConnector):
            async def exchange_authorization_code(
                self,
                *,
                code: str,
                code_verifier: str,
                redirect_uri: str,
            ) -> AuthorizationExchange:
                return AuthorizationExchange(
                    credentials=CredentialMaterial(
                        values={
                            "access_token": "server-only",
                            "refresh_token": "server-only-refresh",
                        }
                    ),
                    granted_scopes=(
                        "openid",
                        "https://www.googleapis.com/auth/userinfo.email",
                        "https://www.googleapis.com/auth/gmail.readonly",
                    ),
                )

        adapter = GoogleCanonicalScopeConnector()
        adapters = ConnectorAdapterRegistry({"gmail": adapter})
        credential_store = InMemoryIntegrationCredentialStore()

        raw_state = "google-canonical-scope-state"
        verifier_reference = await credential_store.store(
            business_id=BUSINESS_ID,
            connector_type="gmail",
            purpose="oauth_pkce",
            material=CredentialMaterial(
                values={"code_verifier": "server-verifier"}
            ),
        )

        oauth_state = IntegrationOAuthState(
            id=uuid4(),
            business_id=BUSINESS_ID,
            connector_type="gmail",
            user_id=USER_ID,
            state_hash=hashlib.sha256(raw_state.encode()).hexdigest(),
            pkce_verifier_reference=verifier_reference,
            redirect_target="/integrations",
            expires_at=NOW + timedelta(minutes=5),
            consumed_at=None,
            created_at=NOW,
        )

        connection = _connection(
            status="pending",
            authentication_state="authorization_pending",
            health="not_checked",
        )
        session = _Session([oauth_state, connection])

        configuration = settings.model_copy(
            update={
                "integration_oauth_callback_url":
                    "https://api.example.test/api/v1/integrations/oauth/callback",
            }
        )

        with patch("app.services.integrations.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            result = await service.complete_authorization(
                session,  # type: ignore[arg-type]
                connector_type=None,
                state=raw_state,
                code="google-one-use-code",
                adapters=adapters,
                credentials=credential_store,
                configuration=configuration,
            )

        self.assertEqual(result.status, "connected")
        self.assertEqual(connection.status, "connected")
        self.assertEqual(connection.authentication_state, "authorized")

    async def test_callback_rejects_expired_and_wrong_connector_state(self) -> None:
        raw_state = "state"
        expired = IntegrationOAuthState(
            id=uuid4(), business_id=BUSINESS_ID, connector_type="gmail", user_id=USER_ID,
            state_hash=hashlib.sha256(raw_state.encode()).hexdigest(),
            pkce_verifier_reference="test-reference", redirect_target="/integrations",
            expires_at=NOW - timedelta(seconds=1), consumed_at=None, created_at=NOW - timedelta(minutes=5),
        )
        configuration = settings.model_copy(update={"integration_oauth_callback_url": "https://api.example.test/callback"})
        with patch("app.services.integrations.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            with self.assertRaises(IntegrationStateError):
                await service.complete_authorization(
                    _Session([expired]),  # type: ignore[arg-type]
                    connector_type="gmail", state=raw_state, code="code", configuration=configuration,
                )
            active = IntegrationOAuthState(
                id=uuid4(), business_id=BUSINESS_ID, connector_type="gmail", user_id=USER_ID,
                state_hash=expired.state_hash, pkce_verifier_reference="test-reference",
                redirect_target="/integrations", expires_at=NOW + timedelta(minutes=5),
                consumed_at=None, created_at=NOW,
            )
            with self.assertRaises(IntegrationStateError):
                await service.complete_authorization(
                    _Session([active]),  # type: ignore[arg-type]
                    connector_type="facebook", state=raw_state, code="code", configuration=configuration,
                )


class IntegrationLifecycleServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_provider_returned_resource_can_be_selected(self) -> None:
        connection = _connection()
        returned = ExternalResource(
            resource_type="mailbox", external_reference="mailbox-1", display_name="Business mailbox"
        )
        session = _Session()
        with patch("app.services.integrations.list_resources", new=AsyncMock(return_value=[returned])), patch(
            "app.services.integrations._authorized_connection", new=AsyncMock(return_value=connection)
        ):
            selected = await service.select_resource(
                session,  # type: ignore[arg-type]
                business_id=BUSINESS_ID, connection_id=connection.id, actor_user_id=USER_ID,
                data=ResourceSelectionRequest(resource_type="mailbox", external_reference="mailbox-1"),
            )
            self.assertEqual(selected.selected_resources[0]["external_reference"], "mailbox-1")
            with self.assertRaises(IntegrationValidationError):
                await service.select_resource(
                    session,  # type: ignore[arg-type]
                    business_id=BUSINESS_ID, connection_id=connection.id, actor_user_id=USER_ID,
                    data=ResourceSelectionRequest(resource_type="mailbox", external_reference="other-connection-resource"),
                )

    async def test_refresh_result_moves_connection_to_reauthentication_required(self) -> None:
        credential_store = InMemoryIntegrationCredentialStore()
        reference = await credential_store.store(
            business_id=BUSINESS_ID, connector_type="gmail", purpose="oauth_credentials",
            material=CredentialMaterial(values={"refresh": "secret"}),
        )
        connection = _connection(credential_reference=reference)
        adapter = _FakeConnector(refresh_status="reauth_required")
        session = _Session()
        with patch("app.services.integrations._authorized_connection", new=AsyncMock(return_value=connection)):
            result = await service.refresh_connection_credentials(
                session,  # type: ignore[arg-type]
                business_id=BUSINESS_ID, connection_id=connection.id,
                adapters=ConnectorAdapterRegistry({"gmail": adapter}), credentials=credential_store,
            )
        self.assertEqual((result.status, result.health), ("reauth_required", "reauth_required"))
        self.assertTrue(any(getattr(item, "category", None) == "integration_health" for item in session.added))

    async def test_normalized_ads_metrics_use_decimal_and_server_derived_ratios(self) -> None:
        connection = _connection(connector_type="google_ads")
        campaign_id = uuid4()
        link = IntegrationEntityLink(
            id=uuid4(), business_id=BUSINESS_ID, integration_connection_id=connection.id,
            internal_entity_type="campaign", internal_entity_id=campaign_id,
            external_resource_reference="account-1", external_entity_id="campaign-1",
            sync_state="linked", last_internal_change_at=None, last_external_change_at=None,
            last_synced_at=None, created_at=NOW, updated_at=NOW,
        )
        session = _Session([link])
        normalized = NormalizedAdPerformance(
            external_campaign_reference="campaign-1", period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 7), spend=Decimal("100.0000"), impressions=1000,
            clicks=20, conversions=2, revenue=Decimal("300.0000"), reach=800, leads=4,
        )
        with patch("app.services.integrations._authorized_connection", new=AsyncMock(return_value=connection)):
            value = await service.ingest_ad_performance(
                session,  # type: ignore[arg-type]
                business_id=BUSINESS_ID, connection_id=connection.id, campaign_id=campaign_id,
                actor_user_id=USER_ID, channel="google_ads", normalized=normalized,
            )
        self.assertIsInstance(value, MarketingPerformance)
        self.assertEqual(value.data_source, "future_connector")
        self.assertEqual(value.ctr, Decimal("2.000000"))
        self.assertEqual(value.cpc, Decimal("5.000000"))
        self.assertEqual(value.roas, Decimal("3.000000"))


class IntegrationWebhookServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_signature_normalization_tenant_resolution_and_duplicate_dedup(self) -> None:
        connection = _connection(connector_type="facebook")
        adapter = _FakeConnector()
        adapter.connector_type = "facebook"
        adapter.webhook_event = NormalizedIntegrationEvent(
            external_event_id="provider-event-1", event_type="message_received", occurred_at=datetime.now(UTC),
            safe_payload={"sender_email": "USER@EXAMPLE.TEST", "unknown_provider_blob": "drop-me"},
        )
        event = IntegrationWebhookEvent(
            id=uuid4(), business_id=BUSINESS_ID, integration_connection_id=connection.id,
            connector_type="facebook", external_event_id="provider-event-1", event_type="message_received",
            status="received", normalized_payload={"sender_email": "user@example.test", "occurred_at": NOW.isoformat()},
            received_at=NOW, processed_at=None, failure_code=None, created_at=NOW,
        )
        verifier = _Verifier(True)
        first_session = _Session([connection, event.id, event])
        with patch("app.services.integrations.enqueue_job", new=AsyncMock()) as enqueue:
            first = await service.ingest_webhook(
                first_session,  # type: ignore[arg-type]
                connector_type="facebook", connection_id=connection.id, body=b"{}",
                headers={}, payload={"entry": [{"id": "page-1"}]}, verifier=verifier,
                adapters=ConnectorAdapterRegistry({"facebook": adapter}),
            )
        self.assertEqual(first.business_id, BUSINESS_ID)
        self.assertEqual(first.status, "received")
        self.assertNotIn("unknown_provider_blob", first.normalized_payload)
        self.assertIn("occurred_at", first.normalized_payload)
        enqueue.assert_awaited_once()
        duplicate = await service.ingest_webhook(
            _Session([connection, None, first]),  # type: ignore[arg-type]
            connector_type="facebook", connection_id=connection.id, body=b"{}", headers={},
            payload={"entry": [{"id": "page-1"}]}, verifier=verifier,
            adapters=ConnectorAdapterRegistry({"facebook": adapter}),
        )
        self.assertIs(duplicate, first)
        with self.assertRaises(IntegrationWebhookVerificationError):
            await service.ingest_webhook(
                _Session(),  # type: ignore[arg-type]
                connector_type="facebook", connection_id=connection.id, body=b"{}", headers={},
                payload={}, verifier=_Verifier(False), adapters=ConnectorAdapterRegistry({"facebook": adapter}),
            )

    async def test_verified_whatsapp_status_webhook_fans_out_and_replay_is_idempotent(
        self,
    ) -> None:
        from app.integrations.oauth_adapters import (
            _normalize_whatsapp_status_events,
        )

        connection = _connection(connector_type="whatsapp_business")

        wamid = "wamid.HBgMNTU1MjM0NTY3ODkwFQIAERgSQUJDREVGRw=="
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123456789",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "statuses": [
                                    {
                                        "id": wamid,
                                        "status": "sent",
                                        "timestamp": "1787486400",
                                        "recipient_id": "923001234567",
                                    },
                                    {
                                        "id": wamid,
                                        "status": "delivered",
                                        "timestamp": "1787486460",
                                        "recipient_id": "923001234567",
                                    },
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        normalized = _normalize_whatsapp_status_events(payload)
        self.assertEqual(len(normalized), 2)

        first_event = IntegrationWebhookEvent(
            id=uuid4(),
            business_id=BUSINESS_ID,
            integration_connection_id=connection.id,
            connector_type="whatsapp_business",
            external_event_id=normalized[0].external_event_id,
            event_type="message_status_updated",
            status="received",
            normalized_payload={
                **dict(normalized[0].safe_payload),
                "occurred_at": normalized[0].occurred_at.isoformat(),
            },
            received_at=NOW,
            processed_at=None,
            failure_code=None,
            created_at=NOW,
        )
        second_event = IntegrationWebhookEvent(
            id=uuid4(),
            business_id=BUSINESS_ID,
            integration_connection_id=connection.id,
            connector_type="whatsapp_business",
            external_event_id=normalized[1].external_event_id,
            event_type="message_status_updated",
            status="received",
            normalized_payload={
                **dict(normalized[1].safe_payload),
                "occurred_at": normalized[1].occurred_at.isoformat(),
            },
            received_at=NOW,
            processed_at=None,
            failure_code=None,
            created_at=NOW,
        )

        class _WhatsAppStatusAdapter:
            connector_type = "whatsapp_business"

            async def normalize_webhooks(self, received_payload):
                self.received_payload = received_payload
                return _normalize_whatsapp_status_events(received_payload)

        adapter = _WhatsAppStatusAdapter()
        adapters = ConnectorAdapterRegistry(
            {"whatsapp_business": adapter}  # type: ignore[arg-type]
        )
        verifier = _Verifier(True)

        # First verified delivery-status request:
        #
        # connection
        # → insert status 1
        # → reload status 1
        # → insert status 2
        # → reload status 2
        first_session = _Session(
            [
                connection,
                first_event.id,
                first_event,
                second_event.id,
                second_event,
            ]
        )

        with patch(
            "app.services.integrations.enqueue_job",
            new=AsyncMock(),
        ) as enqueue:
            returned = await service.ingest_webhook(
                first_session,  # type: ignore[arg-type]
                connector_type="whatsapp_business",
                connection_id=connection.id,
                body=b'{"verified":"meta-status-batch"}',
                headers={},
                payload=payload,
                verifier=verifier,
                adapters=adapters,
            )

            self.assertIs(returned, first_event)
            self.assertEqual(enqueue.await_count, 2)

            queued = enqueue.await_args_list

            self.assertEqual(
                queued[0].kwargs["job_type"],
                "process_integration_event",
            )
            self.assertEqual(
                queued[0].kwargs["integration_event_id"],
                first_event.id,
            )
            self.assertEqual(
                queued[0].kwargs["idempotency_key"],
                f"integration-event:{first_event.id}",
            )

            self.assertEqual(
                queued[1].kwargs["job_type"],
                "process_integration_event",
            )
            self.assertEqual(
                queued[1].kwargs["integration_event_id"],
                second_event.id,
            )
            self.assertEqual(
                queued[1].kwargs["idempotency_key"],
                f"integration-event:{second_event.id}",
            )

            # Replaying the exact same signed provider evidence resolves both
            # existing durable events and must not enqueue either event again.
            replay_session = _Session(
                [
                    connection,
                    None,
                    first_event,
                    None,
                    second_event,
                ]
            )

            replayed = await service.ingest_webhook(
                replay_session,  # type: ignore[arg-type]
                connector_type="whatsapp_business",
                connection_id=connection.id,
                body=b'{"verified":"meta-status-batch"}',
                headers={},
                payload=payload,
                verifier=verifier,
                adapters=adapters,
            )

            self.assertIs(replayed, first_event)

            # Still exactly the two jobs from the original request.
            self.assertEqual(enqueue.await_count, 2)

        self.assertEqual(
            first_event.normalized_payload["external_message_reference"],
            wamid,
        )
        self.assertEqual(
            second_event.normalized_payload["external_message_reference"],
            wamid,
        )
        self.assertTrue(wamid.endswith("=="))

        self.assertEqual(
            first_event.normalized_payload["delivery_status"],
            "sent",
        )
        self.assertEqual(
            second_event.normalized_payload["delivery_status"],
            "delivered",
        )
        self.assertNotEqual(
            first_event.external_event_id,
            second_event.external_event_id,
        )


    async def test_verified_inbound_message_records_message_and_durable_agent_job(self) -> None:
        connection = _connection(connector_type="whatsapp_business")
        event = IntegrationWebhookEvent(
            id=uuid4(), business_id=BUSINESS_ID, integration_connection_id=connection.id,
            connector_type="whatsapp_business", external_event_id="message-event", event_type="message_received",
            status="received", normalized_payload={
                "external_conversation_reference": "thread-1",
                "external_message_reference": "message-1",
                "sender_phone": "+1 555 000 1234",
                "content": "Can I book an appointment?",
            }, received_at=NOW, processed_at=None, failure_code=None, created_at=NOW,
        )
        session = _Session([None])
        with patch("app.services.integrations._match_customer", new=AsyncMock(return_value=None)), patch(
            "app.services.integrations.enqueue_job", new=AsyncMock(),
        ) as enqueue:
            await service._record_inbound_message(session, connection, event, NOW)  # type: ignore[arg-type]
        conversation = next(item for item in session.added if isinstance(item, Conversation))
        message = next(item for item in session.added if isinstance(item, ConversationMessage))
        self.assertEqual(conversation.business_id, BUSINESS_ID)
        self.assertEqual(conversation.channel, "whatsapp")
        self.assertEqual(message.direction, "inbound")
        self.assertEqual(message.sender_type, "customer")
        enqueue.assert_awaited_once()
        self.assertEqual(enqueue.await_args.kwargs["job_type"], "customer_agent_response")
        self.assertFalse(any(item.__class__.__name__ in {"AIAction", "AIAgentExecution"} for item in session.added))

    async def test_replayed_inbound_message_does_not_duplicate_canonical_message(self) -> None:
        connection = _connection(connector_type="whatsapp_business")
        event = IntegrationWebhookEvent(
            id=uuid4(), business_id=BUSINESS_ID, integration_connection_id=connection.id,
            connector_type="whatsapp_business", external_event_id="message-event", event_type="message_received",
            status="received", normalized_payload={
                "external_conversation_reference": "thread-1",
                "external_message_reference": "message-1",
                "sender_phone": "+15550001234",
                "content": "Can I book an appointment?",
            }, received_at=NOW, processed_at=None, failure_code=None, created_at=NOW,
        )
        conversation = Conversation(
            id=uuid4(), business_id=BUSINESS_ID, customer_id=None, channel="whatsapp",
            external_reference="thread-1", status="open", assigned_user_id=None,
            last_activity_at=NOW, created_at=NOW, updated_at=NOW,
        )
        session = _Session([conversation, uuid4()])
        with patch("app.services.integrations._match_customer", new=AsyncMock(return_value=None)):
            await service._record_inbound_message(session, connection, event, NOW)  # type: ignore[arg-type]
        self.assertFalse(any(isinstance(item, ConversationMessage) for item in session.added))

    async def test_verified_whatsapp_sender_auto_creates_and_links_customer(self) -> None:
        connection = _connection(connector_type="whatsapp_business")
        event = IntegrationWebhookEvent(
            id=uuid4(),
            business_id=BUSINESS_ID,
            integration_connection_id=connection.id,
            connector_type="whatsapp_business",
            external_event_id="whatsapp-customer-event",
            event_type="message_received",
            status="received",
            normalized_payload={
                "external_conversation_reference": "wa-thread-customer-1",
                "external_message_reference": "wa-message-customer-1",
                "sender_display_name": "QA WhatsApp Customer",
                "sender_phone": "+92 300 1234567",
                "content": "I want to buy eggs.",
            },
            received_at=NOW,
            processed_at=None,
            failure_code=None,
            created_at=NOW,
        )

        session = _InboundIdentitySession(
            scalar_results=[None, None],
            identity_matches=[],
        )

        with patch("app.services.integrations.enqueue_job", new=AsyncMock()):
            await service._record_inbound_message(
                session,  # type: ignore[arg-type]
                connection,
                event,
                NOW,
            )

        customer = next(
            item for item in session.added
            if isinstance(item, Customer)
        )
        conversation = next(
            item for item in session.added
            if isinstance(item, Conversation)
        )
        message = next(
            item for item in session.added
            if isinstance(item, ConversationMessage)
        )

        self.assertEqual(customer.business_id, BUSINESS_ID)
        self.assertEqual(customer.display_name, "QA WhatsApp Customer")
        self.assertEqual(customer.phone, "+92 300 1234567")
        self.assertEqual(customer.source, "whatsapp_business")
        self.assertEqual(conversation.customer_id, customer.id)
        self.assertEqual(message.conversation_id, conversation.id)

    async def test_verified_gmail_sender_auto_creates_and_links_customer(self) -> None:
        connection = _connection(connector_type="gmail")
        event = IntegrationWebhookEvent(
            id=uuid4(),
            business_id=BUSINESS_ID,
            integration_connection_id=connection.id,
            connector_type="gmail",
            external_event_id="gmail-customer-event",
            event_type="email_received",
            status="received",
            normalized_payload={
                "external_conversation_reference": "gmail-thread-customer-1",
                "external_message_reference": "gmail-message-customer-1",
                "sender_display_name": "QA Email Customer",
                "sender_email": "QA.Customer@Example.TEST",
                "content": "Please send me your product list.",
            },
            received_at=NOW,
            processed_at=None,
            failure_code=None,
            created_at=NOW,
        )

        session = _InboundIdentitySession(
            scalar_results=[None, None],
            identity_matches=[],
        )

        with patch("app.services.integrations.enqueue_job", new=AsyncMock()):
            await service._record_inbound_message(
                session,  # type: ignore[arg-type]
                connection,
                event,
                NOW,
            )

        customer = next(
            item for item in session.added
            if isinstance(item, Customer)
        )
        conversation = next(
            item for item in session.added
            if isinstance(item, Conversation)
        )

        self.assertEqual(customer.business_id, BUSINESS_ID)
        self.assertEqual(customer.display_name, "QA Email Customer")
        self.assertEqual(customer.email, "qa.customer@example.test")
        self.assertEqual(customer.source, "gmail")
        self.assertEqual(conversation.customer_id, customer.id)

    async def test_social_display_name_without_identity_remains_anonymous(self) -> None:
        connection = _connection(connector_type="instagram")
        event = IntegrationWebhookEvent(
            id=uuid4(),
            business_id=BUSINESS_ID,
            integration_connection_id=connection.id,
            connector_type="instagram",
            external_event_id="instagram-anonymous-event",
            event_type="message_received",
            status="received",
            normalized_payload={
                "external_conversation_reference": "ig-thread-1",
                "external_message_reference": "ig-message-1",
                "sender_display_name": "Ali",
                "content": "What products do you have?",
            },
            received_at=NOW,
            processed_at=None,
            failure_code=None,
            created_at=NOW,
        )

        session = _InboundIdentitySession(
            scalar_results=[None, None],
            identity_matches=[],
        )

        with patch("app.services.integrations.enqueue_job", new=AsyncMock()):
            await service._record_inbound_message(
                session,  # type: ignore[arg-type]
                connection,
                event,
                NOW,
            )

        conversation = next(
            item for item in session.added
            if isinstance(item, Conversation)
        )

        self.assertIsNone(conversation.customer_id)
        self.assertFalse(
            any(isinstance(item, Customer) for item in session.added)
        )

    async def test_ambiguous_verified_sender_is_not_guessed(self) -> None:
        connection = _connection(connector_type="whatsapp_business")

        email_customer = _identity_customer(
            email="qa@example.test",
            phone=None,
        )
        phone_customer = _identity_customer(
            email=None,
            phone="+92 300 1234567",
        )

        event = IntegrationWebhookEvent(
            id=uuid4(),
            business_id=BUSINESS_ID,
            integration_connection_id=connection.id,
            connector_type="whatsapp_business",
            external_event_id="ambiguous-customer-event",
            event_type="message_received",
            status="received",
            normalized_payload={
                "external_conversation_reference": "ambiguous-thread",
                "external_message_reference": "ambiguous-message",
                "sender_display_name": "QA Customer",
                "sender_email": "qa@example.test",
                "sender_phone": "+92 300 1234567",
                "content": "Hello",
            },
            received_at=NOW,
            processed_at=None,
            failure_code=None,
            created_at=NOW,
        )

        session = _InboundIdentitySession(
            scalar_results=[None, None],
            identity_matches=[email_customer, phone_customer],
        )

        with patch("app.services.integrations.enqueue_job", new=AsyncMock()):
            await service._record_inbound_message(
                session,  # type: ignore[arg-type]
                connection,
                event,
                NOW,
            )

        conversation = next(
            item for item in session.added
            if isinstance(item, Conversation)
        )

        self.assertIsNone(conversation.customer_id)
        self.assertFalse(
            any(isinstance(item, Customer) for item in session.added)
        )

    async def test_worker_processing_marks_verified_event_and_emits_automation_event(self) -> None:
        connection = _connection(connector_type="gmail")
        event = IntegrationWebhookEvent(
            id=uuid4(), business_id=BUSINESS_ID, integration_connection_id=connection.id,
            connector_type="gmail", external_event_id="email-event", event_type="email_received",
            status="received", normalized_payload={
                "occurred_at": NOW.isoformat(),
                "external_conversation_reference": "gmail-thread-worker",
                "external_message_reference": "gmail-message-worker",
                "sender_email": "customer@example.test",
                "content": "Can you help with my order?",
            },
            received_at=NOW, processed_at=None, failure_code=None, created_at=NOW,
        )
        session = _Session([event, connection])
        with patch("app.services.integrations.record_automation_event") as record_event, patch(
            "app.services.integrations._record_inbound_message", new=AsyncMock(),
        ) as record_message:
            result = await service.process_integration_webhook_event(
                session, business_id=BUSINESS_ID, event_id=event.id,  # type: ignore[arg-type]
            )
        self.assertEqual(result.status, "processed")
        self.assertIsNotNone(result.processed_at)
        record_message.assert_awaited_once_with(session, connection, event, NOW)
        record_event.assert_called_once()
        self.assertEqual(record_event.call_args.kwargs["business_id"], BUSINESS_ID)


class _Session:
    def __init__(self, scalar_results: list[object] | None = None) -> None:
        self.scalar_results = list(scalar_results or [])
        self.added: list[object] = []

    async def scalar(self, *_args, **_kwargs):
        return self.scalar_results.pop(0) if self.scalar_results else None

    def add(self, value: object) -> None:
        if getattr(value, "id", None) is None:
            value.id = uuid4()  # type: ignore[attr-defined]
        self.added.append(value)

    async def flush(self) -> None:
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid4()  # type: ignore[attr-defined]


class _ScalarCollection:
    def __init__(self, values: list[Customer]) -> None:
        self.values = values

    def all(self) -> list[Customer]:
        return self.values


class _InboundIdentitySession(_Session):
    def __init__(
        self,
        *,
        scalar_results: list[object] | None = None,
        identity_matches: list[Customer] | None = None,
    ) -> None:
        super().__init__(scalar_results)
        self.identity_matches = list(identity_matches or [])
        self.identity_statements: list[object] = []

    async def scalars(self, statement):
        self.identity_statements.append(statement)
        return _ScalarCollection(self.identity_matches)


def _identity_customer(
    *,
    email: str | None,
    phone: str | None,
) -> Customer:
    return Customer(
        id=uuid4(),
        business_id=BUSINESS_ID,
        display_name="Existing QA Customer",
        first_name=None,
        last_name=None,
        email=email,
        phone=phone,
        status="active",
        source="manual",
        tags=[],
        company=None,
        notes=None,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )


class _Verifier:
    def __init__(self, valid: bool) -> None:
        self.valid = valid

    def verify(self, *, body: bytes, headers) -> bool:
        return self.valid


class _FakeConnector:
    connector_type = "gmail"

    def __init__(self, refresh_status: str = "refreshed") -> None:
        self.refresh_status = refresh_status
        self.authorization_requests: list[AuthorizationRequest] = []
        self.webhook_event = NormalizedIntegrationEvent(
            external_event_id="event-1", event_type="email_received", occurred_at=NOW, safe_payload={},
        )

    async def build_authorization_url(self, request: AuthorizationRequest) -> str:
        self.authorization_requests.append(request)
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={request.state}"

    async def exchange_authorization_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> AuthorizationExchange:
        return AuthorizationExchange(
            credentials=CredentialMaterial(values={"access": "server-only", "refresh": "server-only"}),
            granted_scopes=("openid", "email"),
        )

    async def refresh_credentials(self, credentials: CredentialMaterial) -> CredentialRefreshResult:
        if self.refresh_status == "refreshed":
            return CredentialRefreshResult(status="refreshed", credentials=credentials)
        return CredentialRefreshResult(status=self.refresh_status, failure_code=self.refresh_status)  # type: ignore[arg-type]

    async def revoke_credentials(self, credentials: CredentialMaterial) -> None:
        return None

    async def get_identity(self, credentials: CredentialMaterial) -> ExternalIdentity:
        return ExternalIdentity(external_account_reference="mailbox-1", display_name="Business mailbox")

    async def list_resources(self, credentials: CredentialMaterial):
        return [ExternalResource(resource_type="mailbox", external_reference="mailbox-1", display_name="Business mailbox")]

    async def health_check(self, credentials: CredentialMaterial) -> ConnectionHealthResult:
        return ConnectionHealthResult(health="healthy")

    async def normalize_webhook(self, payload):
        return self.webhook_event


def _connection(
    *,
    connector_type: str = "gmail",
    status: str = "connected",
    authentication_state: str = "authorized",
    health: str = "healthy",
    credential_reference: str = "test-credential:opaque",
) -> IntegrationConnection:
    selected_resources = (
        [{
            "resource_type": "whatsapp_business_account",
            "external_reference": "123456789",
            "display_name": "Test WhatsApp account",
        }]
        if connector_type == "whatsapp_business"
        else [{
            "resource_type": "facebook_page",
            "external_reference": "page-1",
            "display_name": "Test Page",
        }]
        if connector_type == "facebook"
        else []
    )
    return IntegrationConnection(
        id=uuid4(), business_id=BUSINESS_ID, connector_type=connector_type,
        display_name=connector_type.replace("_", " ").title(), status=status,
        authentication_state=authentication_state, health=health,
        credential_reference=credential_reference, external_account_reference="account-1",
        external_account_display_name="Business account", selected_resources=selected_resources,
        scopes_granted=["openid"], connected_by_user_id=USER_ID, connected_at=NOW,
        last_health_check_at=NOW, last_successful_sync_at=None, failure_code=None,
        created_at=NOW, updated_at=NOW,
    )


if __name__ == "__main__":
    unittest.main()
