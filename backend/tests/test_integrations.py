from __future__ import annotations

import hashlib
import hmac
import os
import unittest
from datetime import UTC, datetime
from types import MappingProxyType
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.core.config import settings  # noqa: E402
from app.domain.integrations import (  # noqa: E402
    CANONICAL_CONNECTOR_TYPES,
    ExternalConnectorWritesDisabledError,
    require_external_connector_writes_enabled,
)
from app.integrations.action_boundary import CONNECTOR_ACTION_TYPES, prepare_connector_dispatch_context  # noqa: E402
from app.integrations.adapters import ConnectorAdapterRegistry, DisabledIntegrationConnector  # noqa: E402
from app.integrations.credentials import CredentialMaterial, InMemoryIntegrationCredentialStore  # noqa: E402
from app.integrations.contracts import (  # noqa: E402
    AuthorizationExchange,
    AuthorizationRequest,
    ConnectionHealthResult,
    CredentialRefreshResult,
    ExternalIdentity,
    ExternalResource,
    NormalizedIntegrationEvent,
)
from app.integrations.oauth_adapters import ConfiguredOAuthConnector  # noqa: E402
from app.integrations.registry import CONNECTOR_REGISTRY, list_connector_definitions  # noqa: E402
from app.integrations.webhooks import MetaWebhookSignatureVerifier  # noqa: E402
from app.models.integration import (  # noqa: E402
    IntegrationConnection,
    IntegrationEntityLink,
    IntegrationOAuthState,
    IntegrationWebhookEvent,
)
from app.schemas.integration import IntegrationConnectionResponse  # noqa: E402
from app.services.ai_workforce import route_command  # noqa: E402
from app.services.integrations import _authorization_url_is_safe, connector_catalog  # noqa: E402


class IntegrationFoundationTests(unittest.TestCase):
    def test_registry_is_immutable_canonical_and_explicitly_read_only(self) -> None:
        self.assertIsInstance(CONNECTOR_REGISTRY, MappingProxyType)
        self.assertEqual(tuple(CONNECTOR_REGISTRY), CANONICAL_CONNECTOR_TYPES)
        with self.assertRaises(TypeError):
            CONNECTOR_REGISTRY["gmail"] = CONNECTOR_REGISTRY["gmail"]  # type: ignore[index]
        for definition in list_connector_definitions():
            with self.subTest(connector=definition.connector_type):
                self.assertFalse(definition.external_writes_enabled)
                self.assertTrue(definition.read_capabilities)
                self.assertTrue(definition.oauth_scopes)
                self.assertTrue(all(item.startswith("future_") for item in definition.future_write_capabilities))

    def test_catalog_is_truthful_until_real_provider_and_vault_setup(self) -> None:
        catalog = connector_catalog()
        self.assertEqual(len(catalog), 8)
        self.assertTrue(all(item.external_writes_enabled is False for item in catalog))
        self.assertEqual(
            next(item for item in catalog if item.connector_type == "microsoft_outlook").setup_status,
            "coming_soon",
        )
        self.assertTrue(all(
            item.setup_status == "provider_setup_required"
            for item in catalog if item.connector_type != "microsoft_outlook"
        ))

    def test_models_are_tenant_scoped_bounded_and_never_store_secret_material(self) -> None:
        models = (IntegrationConnection, IntegrationOAuthState, IntegrationWebhookEvent, IntegrationEntityLink)
        forbidden = {"access_token", "refresh_token", "client_secret", "api_key", "password", "code_verifier"}
        for model in models:
            with self.subTest(model=model.__name__):
                self.assertIn("business_id", model.__table__.columns)
                self.assertTrue(forbidden.isdisjoint(model.__table__.columns.keys()))
                self.assertTrue(any(isinstance(item, CheckConstraint) for item in model.__table__.constraints))
        for model in (IntegrationWebhookEvent, IntegrationEntityLink):
            composite = [item for item in model.__table__.constraints if isinstance(item, ForeignKeyConstraint) and len(item.column_keys) == 2]
            self.assertTrue(composite)
        self.assertTrue(any(
            isinstance(item, UniqueConstraint)
            and set(item.columns.keys()) == {"business_id", "connector_type"}
            for item in IntegrationConnection.__table__.constraints
        ))

    def test_connection_response_cannot_serialize_credential_reference(self) -> None:
        self.assertNotIn("credential_reference", IntegrationConnectionResponse.model_fields)
        self.assertNotIn("pkce_verifier_reference", IntegrationConnectionResponse.model_fields)

    def test_connector_action_boundary_is_explicit_and_globally_disabled(self) -> None:
        self.assertEqual(CONNECTOR_ACTION_TYPES["send_email"], ("gmail", "microsoft_outlook"))
        self.assertNotIn("update_crm", CONNECTOR_ACTION_TYPES)
        with self.assertRaises(ExternalConnectorWritesDisabledError):
            require_external_connector_writes_enabled()

    def test_adapter_registry_has_no_generic_execute_contract(self) -> None:
        adapter = ConnectorAdapterRegistry().get("gmail")
        self.assertIsInstance(adapter, DisabledIntegrationConnector)
        self.assertFalse(hasattr(adapter, "execute"))
        with self.assertRaises(ValueError):
            ConnectorAdapterRegistry({"gmail": DisabledIntegrationConnector("google_calendar")})

    def test_meta_signature_verification_is_constant_time_hmac_contract(self) -> None:
        body = b'{"event":"safe"}'
        digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        verifier = MetaWebhookSignatureVerifier("secret")
        self.assertTrue(verifier.verify(body=body, headers={"x-hub-signature-256": f"sha256={digest}"}))
        self.assertFalse(verifier.verify(body=body + b"x", headers={"x-hub-signature-256": f"sha256={digest}"}))

    def test_command_router_can_inspect_status_without_write_capability(self) -> None:
        route = route_command("Is Google Ads connected?")
        self.assertEqual(route.intent, "integration_status")
        self.assertEqual(route.required_capabilities, ["read_integrations"])
        self.assertEqual(route.relevant_modules, ["integrations"])

    def test_authorization_url_is_restricted_to_registry_owned_https_hosts(self) -> None:
        definition = CONNECTOR_REGISTRY["gmail"]
        self.assertTrue(_authorization_url_is_safe(
            "https://accounts.google.com/o/oauth2/v2/auth?client_id=server-owned",
            definition,
        ))
        for unsafe in (
            "http://accounts.google.com/o/oauth2/v2/auth",
            "https://accounts.google.com@attacker.invalid/oauth",
            "https://attacker.invalid/oauth",
            "https://accounts.google.com/oauth#fragment",
            "https://[broken",
        ):
            with self.subTest(url=unsafe):
                self.assertFalse(_authorization_url_is_safe(unsafe, definition))


class ConfiguredGoogleOAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_connector_does_not_incrementally_merge_cross_connector_scopes(self) -> None:
        connector = ConfiguredOAuthConnector(
            connector_type="gmail",
            provider="google",
            client_id="client-id",
            client_secret="client-secret",
            configuration=settings,
        )
        request = AuthorizationRequest(
            state="state",
            code_challenge="challenge",
            redirect_uri="https://api.example.test/api/v1/integrations/oauth/callback",
            scopes=(
                "openid",
                "email",
                "https://www.googleapis.com/auth/gmail.readonly",
            ),
        )

        authorization_url = await connector.build_authorization_url(request)
        query = parse_qs(urlsplit(authorization_url).query)

        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertNotIn("include_granted_scopes", query)
        self.assertEqual(
            query["scope"],
            [
                "openid email "
                "https://www.googleapis.com/auth/gmail.readonly"
            ],
        )


class ConfiguredGmailReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_gmail_read_lists_real_message_metadata_with_bounded_get_requests(self) -> None:
        class GmailHttp:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object]] = []

            async def request_json(self, method: str, url: str, **kwargs):
                self.calls.append((method, url, kwargs.get("params")))

                if url.endswith("/gmail/v1/users/me/messages"):
                    return {
                        "messages": [
                            {
                                "id": "message-1",
                                "threadId": "thread-1",
                            }
                        ]
                    }

                if url.endswith("/gmail/v1/users/me/messages/message-1"):
                    return {
                        "id": "message-1",
                        "threadId": "thread-1",
                        "snippet": "Customer asked about order 1042.",
                        "payload": {
                            "headers": [
                                {
                                    "name": "From",
                                    "value": "customer@example.com",
                                },
                                {
                                    "name": "Subject",
                                    "value": "Order 1042",
                                },
                            ]
                        },
                    }

                raise AssertionError(f"Unexpected provider URL: {url}")

        http = GmailHttp()
        connector = ConfiguredOAuthConnector(
            connector_type="gmail",
            provider="google",
            client_id="client-id",
            client_secret="client-secret",
            configuration=settings,
            http=http,  # type: ignore[arg-type]
        )

        messages = await connector.list_mail_messages(
            CredentialMaterial(values={"access_token": "server-only-token"}),
            limit=5,
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].external_message_reference, "message-1")
        self.assertEqual(messages[0].external_thread_reference, "thread-1")
        self.assertEqual(messages[0].sender, "customer@example.com")
        self.assertEqual(messages[0].subject, "Order 1042")
        self.assertEqual(
            messages[0].snippet,
            "Customer asked about order 1042.",
        )

        self.assertEqual(
            http.calls,
            [
                (
                    "GET",
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                    {"maxResults": "5"},
                ),
                (
                    "GET",
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/message-1",
                    {
                        "format": "metadata",
                        "metadataHeaders": ["From", "Subject"],
                    },
                ),
            ],
        )
        self.assertTrue(all(method == "GET" for method, _, _ in http.calls))


class ConfiguredGmailContentReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_gmail_reads_one_explicit_message_content_with_get_only(self) -> None:
        class GmailHttp:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object]] = []

            async def request_json(self, method: str, url: str, **kwargs):
                self.calls.append((method, url, kwargs.get("params")))

                if url.endswith("/gmail/v1/users/me/messages/message-1"):
                    return {
                        "id": "message-1",
                        "threadId": "thread-1",
                        "snippet": "Customer needs help with order 1042.",
                        "payload": {
                            "mimeType": "text/plain",
                            "headers": [
                                {
                                    "name": "From",
                                    "value": "customer@example.com",
                                },
                                {
                                    "name": "Subject",
                                    "value": "Order 1042",
                                },
                            ],
                            "body": {
                                "data": (
                                    "SGVsbG8sCgpJIG5lZWQgaGVscCB3aXRoIG9yZGVyIDEwNDIu"
                                ),
                            },
                        },
                    }

                raise AssertionError(f"Unexpected provider URL: {url}")

        http = GmailHttp()
        connector = ConfiguredOAuthConnector(
            connector_type="gmail",
            provider="google",
            client_id="client-id",
            client_secret="client-secret",
            configuration=settings,
            http=http,  # type: ignore[arg-type]
        )

        message = await connector.read_mail_message(
            CredentialMaterial(
                values={"access_token": "server-only-token"}
            ),
            message_reference="message-1",
        )

        self.assertEqual(
            message.external_message_reference,
            "message-1",
        )
        self.assertEqual(
            message.external_thread_reference,
            "thread-1",
        )
        self.assertEqual(
            message.sender,
            "customer@example.com",
        )
        self.assertEqual(
            message.subject,
            "Order 1042",
        )
        self.assertEqual(
            message.body_text,
            "Hello,\n\nI need help with order 1042.",
        )

        self.assertEqual(
            http.calls,
            [
                (
                    "GET",
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/message-1",
                    {"format": "full"},
                )
            ],
        )
        self.assertTrue(
            all(method == "GET" for method, _, _ in http.calls)
        )


class IntegrationCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_store_is_bounded_tenant_scoped_and_revocable(self) -> None:
        store = InMemoryIntegrationCredentialStore()
        business_id = uuid4()
        other_business_id = uuid4()
        reference = await store.store(
            business_id=business_id,
            connector_type="gmail",
            purpose="oauth_credentials",
            material=CredentialMaterial(values={"access": "server-only"}),
        )
        retrieved = await store.retrieve(
            reference,
            business_id=business_id,
            connector_type="gmail",
            purpose="oauth_credentials",
        )
        self.assertEqual(retrieved.values["access"], "server-only")
        with self.assertRaises(Exception):
            await store.retrieve(reference, business_id=other_business_id, connector_type="gmail", purpose="oauth_credentials")
        await store.revoke(reference, business_id=business_id, connector_type="gmail", purpose="oauth_credentials")
        with self.assertRaises(Exception):
            await store.retrieve(reference, business_id=business_id, connector_type="gmail", purpose="oauth_credentials")

    async def test_dispatch_context_fails_before_touching_a_session(self) -> None:
        class NeverSession:
            async def scalar(self, *_args, **_kwargs):
                raise AssertionError("disabled boundary must not query")

        with self.assertRaises(ExternalConnectorWritesDisabledError):
            await prepare_connector_dispatch_context(
                NeverSession(),  # type: ignore[arg-type]
                business_id=uuid4(),
                attempt_id=uuid4(),
                connection_id=uuid4(),
            )

    async def test_fake_provider_covers_oauth_resources_health_refresh_and_normalization(self) -> None:
        adapter = _FakeConnector()
        registry = ConnectorAdapterRegistry({"gmail": adapter})
        request = AuthorizationRequest(
            state="state",
            code_challenge="challenge",
            redirect_uri="https://api.example.test/callback",
            scopes=("openid",),
        )
        self.assertIn("accounts.google.com", await registry.get("gmail").build_authorization_url(request))
        exchange = await adapter.exchange_authorization_code(code="code", code_verifier="verifier", redirect_uri=request.redirect_uri)
        self.assertEqual(exchange.granted_scopes, ("openid",))
        self.assertEqual((await adapter.refresh_credentials(exchange.credentials)).status, "refreshed")
        self.assertEqual((await adapter.get_identity(exchange.credentials)).external_account_reference, "mailbox-1")
        self.assertEqual((await adapter.list_resources(exchange.credentials))[0].resource_type, "mailbox")
        self.assertEqual((await adapter.health_check(exchange.credentials)).health, "healthy")
        normalized = await adapter.normalize_webhook({"message": "hello"})
        self.assertEqual(normalized.event_type, "email_received")


class _FakeConnector:
    connector_type = "gmail"

    async def build_authorization_url(self, request: AuthorizationRequest) -> str:
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={request.state}"

    async def exchange_authorization_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> AuthorizationExchange:
        return AuthorizationExchange(
            credentials=CredentialMaterial(values={"access": "secret"}),
            granted_scopes=("openid",),
        )

    async def refresh_credentials(self, credentials: CredentialMaterial) -> CredentialRefreshResult:
        return CredentialRefreshResult(status="refreshed", credentials=credentials)

    async def revoke_credentials(self, credentials: CredentialMaterial) -> None:
        return None

    async def get_identity(self, credentials: CredentialMaterial) -> ExternalIdentity:
        return ExternalIdentity(external_account_reference="mailbox-1", display_name="Business mailbox")

    async def list_resources(self, credentials: CredentialMaterial):
        return [ExternalResource(resource_type="mailbox", external_reference="mailbox-1", display_name="Business mailbox")]

    async def health_check(self, credentials: CredentialMaterial) -> ConnectionHealthResult:
        return ConnectionHealthResult(health="healthy")

    async def normalize_webhook(self, payload):
        return NormalizedIntegrationEvent(
            external_event_id="event-1",
            event_type="email_received",
            occurred_at=datetime.now(UTC),
            safe_payload={},
        )


if __name__ == "__main__":
    unittest.main()
