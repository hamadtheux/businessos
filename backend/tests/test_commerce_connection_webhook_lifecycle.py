from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.commerce import (  # noqa: E402
    CommerceConfigurationRequiredError,
    CommerceNotFoundError,
    CommercePersistenceError,
    CommerceProviderError,
)
from app.exceptions.integration import IntegrationCredentialUnavailableError  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.integrations.commerce_contracts import CommerceSyncPage  # noqa: E402
from app.integrations.commerce_registry import CommerceConnectorRegistry  # noqa: E402
from app.integrations.credentials import CredentialMaterial  # noqa: E402
from app.models.commerce import CommerceConnection  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.commerce import (  # noqa: E402
    CommerceConnectionConfigure,
    NormalizedStore,
    NormalizedWebhookEvent,
)
from app.services.commerce import configure_connection, ingest_provider_webhook  # noqa: E402
from app.api.v1.commerce import _write  # noqa: E402


class _NestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _type, _value, _traceback):
        return False


class _Connector:
    provider = "custom_api"
    capabilities = frozenset({"store_read", "catalog_read", "webhooks"})

    def __init__(self, *, auth_error: CommerceProviderError | None = None) -> None:
        self.auth_error = auth_error

    async def synchronize(self, credentials, request, *, idempotency_key):
        if self.auth_error:
            raise self.auth_error
        if credentials.values["api_token"] != "new-token":
            raise CommerceProviderError("authentication_failed", retryable=False)
        return CommerceSyncPage(domain="store", store=NormalizedStore(
            external_account_id="verified-account", name="Verified store",
            public_url="https://commerce.example.com", currency="USD", timezone="UTC",
        ))

    def verify_and_parse_webhook(self, credentials, request):
        if credentials.values.get("webhook_secret") != "webhook-secret" or request.body != b"verified":
            raise CommerceProviderError("webhook_verification_failed", retryable=False)
        return NormalizedWebhookEvent(
            external_event_id="delivery-1", topic="orders.updated",
            external_object_id="order-1", reconciliation_domain="orders",
            occurred_at=datetime.now(UTC),
        )


def _connection(*, credential_reference: str | None = "credential:existing") -> CommerceConnection:
    return CommerceConnection(
        id=uuid4(), business_id=uuid4(), provider="custom_api", display_name="Store",
        external_account_id="account-before", store_url="https://commerce.example.com",
        credential_reference=credential_reference, status="connected", health="healthy",
        capabilities=[], sync_cursor={}, safe_metadata={}, consecutive_failures=0,
    )


def _configuration() -> CommerceConnectionConfigure:
    return CommerceConnectionConfigure(credentials={
        "api_token": SecretStr("new-token"),
        "configuration": SecretStr("{}"),
        "webhook_secret": SecretStr("webhook-secret"),
    })


class CommerceCredentialLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_rotation_authenticates_before_replacing_existing_material(self) -> None:
        connection = _connection()
        session = AsyncMock()
        session.add = MagicMock()
        credentials = AsyncMock()
        registry = CommerceConnectorRegistry({"custom_api": _Connector()})
        with patch("app.services.commerce.get_connection", AsyncMock(return_value=connection)):
            result = await configure_connection(
                session, business_id=connection.business_id, connection_id=connection.id,
                actor_user_id=uuid4(), data=_configuration(), credentials=credentials,
                connectors=registry,
            )
        credentials.rotate.assert_awaited_once()
        credentials.store.assert_not_awaited()
        self.assertEqual(result.credential_reference, "credential:existing")
        self.assertEqual(result.external_account_id, "verified-account")
        self.assertEqual(result.status, "connected")
        self.assertNotIn("new-token", repr(result.safe_metadata))
        self.assertNotIn("webhook-secret", repr(result.safe_metadata))

    async def test_failed_rotation_preserves_previous_reference_and_does_not_write(self) -> None:
        connection = _connection()
        session = AsyncMock()
        session.add = MagicMock()
        credentials = AsyncMock()
        registry = CommerceConnectorRegistry({
            "custom_api": _Connector(auth_error=CommerceProviderError("authentication_failed", retryable=False)),
        })
        with (
            patch("app.services.commerce.get_connection", AsyncMock(return_value=connection)),
            self.assertRaises(CommerceProviderError),
        ):
            await configure_connection(
                session, business_id=connection.business_id, connection_id=connection.id,
                actor_user_id=uuid4(), data=_configuration(), credentials=credentials,
                connectors=registry,
            )
        credentials.rotate.assert_not_awaited()
        credentials.store.assert_not_awaited()
        self.assertEqual(connection.credential_reference, "credential:existing")
        self.assertEqual(connection.external_account_id, "account-before")

    async def test_secure_store_rotation_failure_leaves_connection_authority_unchanged(self) -> None:
        connection = _connection()
        session = AsyncMock()
        session.add = MagicMock()
        credentials = AsyncMock()
        credentials.rotate.side_effect = IntegrationCredentialUnavailableError("private-store-error")
        with (
            patch("app.services.commerce.get_connection", AsyncMock(return_value=connection)),
            self.assertRaises(CommerceConfigurationRequiredError),
        ):
            await configure_connection(
                session, business_id=connection.business_id, connection_id=connection.id,
                actor_user_id=uuid4(), data=_configuration(), credentials=credentials,
                connectors=CommerceConnectorRegistry({"custom_api": _Connector()}),
            )
        self.assertEqual(connection.credential_reference, "credential:existing")
        self.assertEqual(connection.external_account_id, "account-before")

    async def test_new_secret_is_revoked_if_database_flush_fails(self) -> None:
        connection = _connection(credential_reference=None)
        session = AsyncMock()
        session.add = MagicMock()
        session.flush.side_effect = SQLAlchemyError("database unavailable")
        credentials = AsyncMock()
        credentials.store.return_value = "credential:new"
        with (
            patch("app.services.commerce.get_connection", AsyncMock(return_value=connection)),
            self.assertRaises(CommercePersistenceError),
        ):
            await configure_connection(
                session, business_id=connection.business_id, connection_id=connection.id,
                actor_user_id=uuid4(), data=_configuration(), credentials=credentials,
                connectors=CommerceConnectorRegistry({"custom_api": _Connector()}),
            )
        credentials.revoke.assert_awaited_once()

    async def test_existing_reference_remains_resolvable_if_database_commit_fails(self) -> None:
        connection = _connection()
        session = AsyncMock()
        session.add = MagicMock()
        session.commit.side_effect = SQLAlchemyError("commit unavailable")
        credentials = AsyncMock()
        with (
            patch("app.services.commerce.get_connection", AsyncMock(return_value=connection)),
            patch("app.api.v1.commerce.materialize_response_before_commit", AsyncMock()),
            self.assertRaises(HTTPException) as caught,
        ):
            await _write(session, Response(), lambda: configure_connection(
                session, business_id=connection.business_id, connection_id=connection.id,
                actor_user_id=uuid4(), data=_configuration(), credentials=credentials,
                connectors=CommerceConnectorRegistry({"custom_api": _Connector()}),
            ))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(connection.credential_reference, "credential:existing")
        credentials.rotate.assert_awaited_once()
        session.rollback.assert_awaited_once()


class CommerceWebhookLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_delivery_is_tenant_bound_queued_once_and_deduplicated(self) -> None:
        connection = _connection()
        session = AsyncMock()
        session.add = MagicMock()
        session.begin_nested = MagicMock(return_value=_NestedTransaction())
        session.scalar.side_effect = [connection, None]
        credentials = AsyncMock()
        credentials.retrieve.return_value = CredentialMaterial({
            "api_token": "new-token", "configuration": "{}", "webhook_secret": "webhook-secret",
        })
        registry = CommerceConnectorRegistry({"custom_api": _Connector()})
        with patch("app.services.commerce.enqueue_job", AsyncMock()) as enqueue:
            receipt, duplicate = await ingest_provider_webhook(
                session, provider="custom_api", connection_id=connection.id,
                headers={}, body=b"verified", credentials=credentials, connectors=registry,
            )
            session.scalar.side_effect = [connection, receipt]
            repeated, repeated_duplicate = await ingest_provider_webhook(
                session, provider="custom_api", connection_id=connection.id,
                headers={}, body=b"verified", credentials=credentials, connectors=registry,
            )
        self.assertFalse(duplicate)
        self.assertTrue(repeated_duplicate)
        self.assertIs(repeated, receipt)
        self.assertEqual(receipt.business_id, connection.business_id)
        self.assertEqual(receipt.connection_id, connection.id)
        self.assertEqual(receipt.status, "queued")
        enqueue.assert_awaited_once()

    async def test_modified_body_is_rejected_before_receipt_or_job(self) -> None:
        connection = _connection()
        session = AsyncMock()
        session.add = MagicMock()
        session.scalar.return_value = connection
        credentials = AsyncMock()
        credentials.retrieve.return_value = CredentialMaterial({
            "api_token": "new-token", "configuration": "{}", "webhook_secret": "webhook-secret",
        })
        with (
            patch("app.services.commerce.enqueue_job", AsyncMock()) as enqueue,
            self.assertRaises(CommerceProviderError),
        ):
            await ingest_provider_webhook(
                session, provider="custom_api", connection_id=connection.id,
                headers={}, body=b"modified", credentials=credentials,
                connectors=CommerceConnectorRegistry({"custom_api": _Connector()}),
            )
        session.add.assert_not_called()
        enqueue.assert_not_awaited()

    async def test_unknown_wrong_provider_disabled_or_cross_tenant_connection_is_not_disclosed(self) -> None:
        # The tenant is intentionally not accepted from the webhook payload or URL.
        # All mismatched/disabled connection lookups resolve to the same not-found boundary.
        for case in ("unknown", "wrong_provider", "disabled", "cross_tenant"):
            session = AsyncMock()
            session.scalar.return_value = None
            with self.subTest(case=case), self.assertRaises(CommerceNotFoundError):
                await ingest_provider_webhook(
                    session, provider="custom_api", connection_id=uuid4(),
                    headers={}, body=b"verified",
                    credentials=AsyncMock(),
                    connectors=CommerceConnectorRegistry({"custom_api": _Connector()}),
                )


class CommerceWebhookApiBoundaryTests(unittest.TestCase):
    def test_oversized_body_is_rejected_before_connection_lookup(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/commerce/webhooks/custom_api/{uuid4()}",
                content=b"x" * (settings.integration_webhook_max_bytes + 1),
                headers={"content-type": "application/json"},
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"detail": {"code": "webhook_payload_too_large"}})
