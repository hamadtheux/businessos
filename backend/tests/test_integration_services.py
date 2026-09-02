from __future__ import annotations

import hashlib
import os
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.core.config import settings  # noqa: E402
from app.exceptions.integration import IntegrationCredentialUnavailableError, IntegrationPersistenceError, IntegrationProviderUnavailableError, IntegrationStateError, IntegrationValidationError, IntegrationWebhookVerificationError  # noqa: E402
from app.integrations.adapters import ConnectorAdapterRegistry  # noqa: E402
from app.integrations.contracts import (  # noqa: E402
    AuthorizationExchange,
    AuthorizationRequest,
    ConnectionHealthResult,
    CredentialRefreshResult,
    ExternalCalendarEvent,
    ExternalIdentity,
    ExternalMailMessageContent,
    ExternalResource,
    NormalizedAdPerformance,
    NormalizedIntegrationEvent,
)
from app.integrations.credentials import CredentialMaterial, InMemoryIntegrationCredentialStore  # noqa: E402
from app.models.conversation import Conversation, ConversationMessage  # noqa: E402
from app.models.customer import Customer  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
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
            self.assertEqual(oauth_state.business_id, BUSINESS_ID)
            self.assertEqual(oauth_state.user_id, USER_ID)
            self.assertEqual(oauth_state.redirect_target, "/integrations")
            state_material = await credential_store.retrieve(
                oauth_state.pkce_verifier_reference,
                business_id=BUSINESS_ID,
                connector_type="gmail",
                purpose="oauth_pkce",
            )
            self.assertEqual(state_material.values["oauth_provider"], "google")
            self.assertEqual(connection.status, "pending")
            self.assertEqual(result.connector_type, "gmail")
            states.append(state)
        self.assertNotEqual(states[0], states[1])

    async def test_callback_consumes_state_stores_opaque_reference_and_rejects_replay(self) -> None:
        adapter = _FakeConnector()
        adapter.revoke_credentials = AsyncMock()
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
        adapter.revoke_credentials.assert_not_awaited()

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

    async def test_callback_rejects_tampered_state(self) -> None:
        configuration = settings.model_copy(
            update={
                "integration_oauth_callback_url": (
                    "https://api.example.test/api/v1/integrations/oauth/callback"
                )
            }
        )
        with self.assertRaises(IntegrationStateError):
            await service.complete_authorization(
                _Session(),  # type: ignore[arg-type]
                connector_type=None,
                state="tampered-state-with-no-server-record",
                code="one-use-code",
                configuration=configuration,
            )

    async def test_meta_callback_persists_only_tenant_bound_authorized_assets(self) -> None:
        class MetaConnector(_FakeConnector):
            connector_type = "facebook"

            async def exchange_authorization_code(self, **_kwargs):
                return AuthorizationExchange(
                    credentials=CredentialMaterial(
                        values={
                            "access_token": "server-only-system-user-token",
                            "meta_token_type": "SYSTEM_USER",
                        }
                    ),
                    granted_scopes=(
                        "pages_show_list",
                        "pages_read_engagement",
                        "pages_manage_metadata",
                        "pages_messaging",
                        "leads_retrieval",
                        "pages_manage_ads",
                    ),
                )

            async def get_identity(self, _credentials):
                return ExternalIdentity(
                    external_account_reference="system-user-1",
                    display_name="9D Brain business integration",
                )

            async def list_resources(self, _credentials):
                return [
                    ExternalResource(
                        "meta_business",
                        "meta-business-1",
                        "Customer Business",
                    ),
                    ExternalResource(
                        "facebook_page",
                        "page-1",
                        "Customer Page",
                        parent_reference="meta-business-1",
                        metadata={
                            "meta_business_id": "meta-business-1",
                            "capabilities": "MESSAGING,ANALYZE",
                        },
                    ),
                ]

        adapter = MetaConnector()
        adapter.revoke_credentials = AsyncMock()
        credential_store = InMemoryIntegrationCredentialStore()
        raw_state = "tenant-a-meta-state"
        verifier_reference = await credential_store.store(
            business_id=BUSINESS_ID,
            connector_type="facebook",
            purpose="oauth_pkce",
            material=CredentialMaterial(
                values={"code_verifier": "server-verifier"}
            ),
        )
        oauth_state = IntegrationOAuthState(
            id=uuid4(),
            business_id=BUSINESS_ID,
            connector_type="facebook",
            user_id=USER_ID,
            state_hash=hashlib.sha256(raw_state.encode()).hexdigest(),
            pkce_verifier_reference=verifier_reference,
            redirect_target="/integrations",
            expires_at=NOW + timedelta(minutes=5),
            consumed_at=None,
            created_at=NOW,
        )
        connection = _connection(
            connector_type="facebook",
            status="pending",
            authentication_state="authorization_pending",
            health="not_checked",
            credential_reference="",
        )
        configuration = settings.model_copy(
            update={
                "integration_oauth_callback_url": (
                    "https://api.example.test/api/v1/integrations/oauth/callback"
                )
            }
        )

        with patch("app.services.integrations.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            result = await service.complete_authorization(
                _Session([oauth_state, connection]),  # type: ignore[arg-type]
                connector_type=None,
                state=raw_state,
                code="one-use-meta-code",
                adapters=ConnectorAdapterRegistry({"facebook": adapter}),
                credentials=credential_store,
                configuration=configuration,
            )

        self.assertEqual(result.status, "connected")
        adapter.revoke_credentials.assert_not_awaited()
        self.assertEqual(connection.business_id, BUSINESS_ID)
        self.assertEqual(
            [item["external_reference"] for item in connection.selected_resources],
            ["meta-business-1", "page-1"],
        )
        self.assertEqual(
            connection.authorized_resources[1]["metadata"],
            {
                "meta_business_id": "meta-business-1",
                "capabilities": "MESSAGING,ANALYZE",
            },
        )
        self.assertNotIn(
            "server-only-system-user-token",
            repr(connection.__dict__),
        )
        with self.assertRaises(IntegrationCredentialUnavailableError):
            await credential_store.retrieve(
                connection.credential_reference,
                business_id=uuid4(),
                connector_type="facebook",
                purpose="oauth_credentials",
            )

    async def test_meta_provider_error_is_consumed_audited_and_safe(self) -> None:
        credential_store = InMemoryIntegrationCredentialStore()
        raw_state = "meta-denied-state"
        verifier_reference = await credential_store.store(
            business_id=BUSINESS_ID,
            connector_type="facebook",
            purpose="oauth_pkce",
            material=CredentialMaterial(
                values={"code_verifier": "server-verifier"}
            ),
        )
        oauth_state = IntegrationOAuthState(
            id=uuid4(), business_id=BUSINESS_ID, connector_type="facebook",
            user_id=USER_ID,
            state_hash=hashlib.sha256(raw_state.encode()).hexdigest(),
            pkce_verifier_reference=verifier_reference,
            redirect_target="/integrations",
            expires_at=NOW + timedelta(minutes=5), consumed_at=None,
            created_at=NOW,
        )
        connection = _connection(
            connector_type="facebook", status="pending",
            authentication_state="authorization_pending", health="not_checked",
        )
        session = _Session([oauth_state, connection])
        configuration = settings.model_copy(
            update={
                "integration_oauth_callback_url": (
                    "https://api.example.test/api/v1/integrations/oauth/callback"
                )
            }
        )

        with patch("app.services.integrations.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            result = await service.complete_authorization(
                session,  # type: ignore[arg-type]
                connector_type=None,
                state=raw_state,
                code=None,
                provider_error="access_denied",
                credentials=credential_store,
                configuration=configuration,
            )

        self.assertEqual(result.status, "degraded")
        self.assertEqual(connection.failure_code, "authorization_denied")
        self.assertEqual(connection.authentication_state, "failed")
        self.assertEqual(oauth_state.consumed_at, NOW)
        audit = next(item for item in session.added if isinstance(item, AuditLog))
        self.assertEqual(audit.event_type, "integration.connection_failed")
        self.assertEqual(audit.status, "failed")
        with self.assertRaises(IntegrationCredentialUnavailableError):
            await credential_store.retrieve(
                verifier_reference,
                business_id=BUSINESS_ID,
                connector_type="facebook",
                purpose="oauth_pkce",
            )

    async def _complete_meta_callback(self, adapter, *, raw_state: str):
        credential_store = InMemoryIntegrationCredentialStore()
        verifier_reference = await credential_store.store(
            business_id=BUSINESS_ID,
            connector_type="facebook",
            purpose="oauth_pkce",
            material=CredentialMaterial(
                values={"code_verifier": "server-verifier"}
            ),
        )
        oauth_state = IntegrationOAuthState(
            id=uuid4(), business_id=BUSINESS_ID, connector_type="facebook",
            user_id=USER_ID,
            state_hash=hashlib.sha256(raw_state.encode()).hexdigest(),
            pkce_verifier_reference=verifier_reference,
            redirect_target="/integrations",
            expires_at=NOW + timedelta(minutes=5), consumed_at=None,
            created_at=NOW,
        )
        connection = _connection(
            connector_type="facebook", status="pending",
            authentication_state="authorization_pending", health="not_checked",
        )
        configuration = settings.model_copy(
            update={
                "integration_oauth_callback_url": (
                    "https://api.example.test/api/v1/integrations/oauth/callback"
                )
            }
        )

        with patch("app.services.integrations.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            result = await service.complete_authorization(
                _Session([oauth_state, connection]),  # type: ignore[arg-type]
                connector_type=None,
                state=raw_state,
                code="one-use-meta-code",
                adapters=ConnectorAdapterRegistry({"facebook": adapter}),
                credentials=credential_store,
                configuration=configuration,
            )

        return (
            result,
            connection,
            oauth_state,
            credential_store,
            verifier_reference,
        )

    async def _complete_meta_finalization_failure(
        self,
        adapter,
        credential_store,
        *,
        raw_state: str,
        failure: str,
    ) -> None:
        verifier_reference = await credential_store.store(
            business_id=BUSINESS_ID,
            connector_type="facebook",
            purpose="oauth_pkce",
            material=CredentialMaterial(
                values={"code_verifier": "server-verifier"}
            ),
        )
        oauth_state = IntegrationOAuthState(
            id=uuid4(),
            business_id=BUSINESS_ID,
            connector_type="facebook",
            user_id=USER_ID,
            state_hash=hashlib.sha256(raw_state.encode()).hexdigest(),
            pkce_verifier_reference=verifier_reference,
            redirect_target="/integrations",
            expires_at=NOW + timedelta(minutes=5),
            consumed_at=None,
            created_at=NOW,
        )
        connection = _connection(
            connector_type="facebook",
            status="pending",
            authentication_state="authorization_pending",
            health="not_checked",
        )
        session = (
            _Session([oauth_state, None])
            if failure == "state"
            else _FailingFlushSession([oauth_state, connection])
        )
        configuration = settings.model_copy(
            update={
                "integration_oauth_callback_url": (
                    "https://api.example.test/api/v1/integrations/oauth/callback"
                )
            }
        )

        with patch("app.services.integrations.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            await service.complete_authorization(
                session,  # type: ignore[arg-type]
                connector_type=None,
                state=raw_state,
                code="one-use-meta-code",
                adapters=ConnectorAdapterRegistry({"facebook": adapter}),
                credentials=credential_store,
                configuration=configuration,
            )

    async def _assert_meta_callback_failure(
        self,
        adapter,
        *,
        raw_state: str,
        failure_code: str,
    ) -> None:
        result, connection, oauth_state, credential_store, verifier_reference = (
            await self._complete_meta_callback(adapter, raw_state=raw_state)
        )

        self.assertEqual(result.status, "degraded")
        self.assertEqual(connection.failure_code, failure_code)
        self.assertEqual(connection.authentication_state, "failed")
        self.assertEqual(oauth_state.consumed_at, NOW)
        with self.assertRaises(IntegrationCredentialUnavailableError):
            await credential_store.retrieve(
                verifier_reference,
                business_id=BUSINESS_ID,
                connector_type="facebook",
                purpose="oauth_pkce",
            )

    async def _assert_new_oauth_credential_removed(
        self,
        credential_store,
    ) -> None:
        reference = credential_store.oauth_credential_reference
        self.assertIsNotNone(reference)
        with self.assertRaises(IntegrationCredentialUnavailableError):
            await credential_store.retrieve(
                reference,
                business_id=BUSINESS_ID,
                connector_type="facebook",
                purpose="oauth_credentials",
            )

    async def test_finalization_state_error_cleans_provider_and_local_credential(self) -> None:
        adapter = _MetaAuthorizationConnector()
        adapter.revoke_credentials = AsyncMock()
        credential_store = _TrackingCredentialStore()

        with self.assertRaisesRegex(
            IntegrationStateError,
            "authorization_state_invalid",
        ):
            await self._complete_meta_finalization_failure(
                adapter,
                credential_store,
                raw_state="meta-finalization-state-error",
                failure="state",
            )

        adapter.revoke_credentials.assert_awaited_once()
        await self._assert_new_oauth_credential_removed(credential_store)

    async def test_finalization_persistence_error_cleans_provider_and_local_credential(self) -> None:
        adapter = _MetaAuthorizationConnector()
        adapter.revoke_credentials = AsyncMock()
        credential_store = _TrackingCredentialStore()

        with self.assertRaisesRegex(
            IntegrationPersistenceError,
            "authorization_unavailable",
        ):
            await self._complete_meta_finalization_failure(
                adapter,
                credential_store,
                raw_state="meta-finalization-persistence-error",
                failure="persistence",
            )

        adapter.revoke_credentials.assert_awaited_once()
        await self._assert_new_oauth_credential_removed(credential_store)

    async def test_provider_revoke_failure_does_not_mask_finalization_error(self) -> None:
        cases = (
            ("state", IntegrationStateError, "authorization_state_invalid"),
            (
                "persistence",
                IntegrationPersistenceError,
                "authorization_unavailable",
            ),
        )
        for failure, expected_error, message in cases:
            with self.subTest(failure=failure):
                adapter = _MetaAuthorizationConnector()
                adapter.revoke_credentials = AsyncMock(
                    side_effect=RuntimeError("private-provider-revoke-error")
                )
                credential_store = _TrackingCredentialStore()

                with self.assertRaisesRegex(expected_error, message):
                    await self._complete_meta_finalization_failure(
                        adapter,
                        credential_store,
                        raw_state=f"meta-{failure}-provider-revoke-error",
                        failure=failure,
                    )

                adapter.revoke_credentials.assert_awaited_once()
                await self._assert_new_oauth_credential_removed(
                    credential_store
                )

    async def test_local_cleanup_failure_does_not_mask_finalization_error(self) -> None:
        cases = (
            ("state", IntegrationStateError, "authorization_state_invalid"),
            (
                "persistence",
                IntegrationPersistenceError,
                "authorization_unavailable",
            ),
        )
        for failure, expected_error, message in cases:
            with self.subTest(failure=failure):
                adapter = _MetaAuthorizationConnector()
                adapter.revoke_credentials = AsyncMock()
                credential_store = _TrackingCredentialStore(
                    fail_oauth_cleanup=True
                )

                with self.assertRaisesRegex(expected_error, message):
                    await self._complete_meta_finalization_failure(
                        adapter,
                        credential_store,
                        raw_state=f"meta-{failure}-local-cleanup-error",
                        failure=failure,
                    )

                adapter.revoke_credentials.assert_awaited_once()
                self.assertEqual(
                    credential_store.oauth_cleanup_attempts,
                    1,
                )

    async def test_meta_token_exchange_failure_marks_connection_error(self) -> None:
        class FailingMetaConnector(_FakeConnector):
            connector_type = "facebook"

            async def exchange_authorization_code(self, **_kwargs):
                raise IntegrationProviderUnavailableError(
                    "private-meta-error"
                )

        adapter = FailingMetaConnector()
        adapter.revoke_credentials = AsyncMock()
        await self._assert_meta_callback_failure(
            adapter,
            raw_state="meta-exchange-failure-state",
            failure_code="authorization_exchange_failed",
        )
        adapter.revoke_credentials.assert_not_awaited()

    async def test_meta_identity_fetch_failure_marks_connection_error(self) -> None:
        class FailingMetaConnector(_FakeConnector):
            connector_type = "facebook"

            async def get_identity(self, _credentials):
                raise IntegrationCredentialUnavailableError(
                    "private-meta-credential-error"
                )

        adapter = FailingMetaConnector()
        adapter.revoke_credentials = AsyncMock()
        await self._assert_meta_callback_failure(
            adapter,
            raw_state="meta-identity-failure-state",
            failure_code="provider_identity_fetch_failed",
        )
        adapter.revoke_credentials.assert_awaited_once()
        self.assertIsInstance(
            adapter.revoke_credentials.await_args.args[0],
            CredentialMaterial,
        )

    async def test_meta_asset_discovery_failure_marks_connection_error(self) -> None:
        class FailingMetaConnector(_FakeConnector):
            connector_type = "facebook"

            async def list_resources(self, _credentials):
                raise RuntimeError("private-meta-assets-error")

        adapter = FailingMetaConnector()
        adapter.revoke_credentials = AsyncMock()
        await self._assert_meta_callback_failure(
            adapter,
            raw_state="meta-assets-failure-state",
            failure_code="authorized_assets_fetch_failed",
        )
        adapter.revoke_credentials.assert_awaited_once()
        self.assertIsInstance(
            adapter.revoke_credentials.await_args.args[0],
            CredentialMaterial,
        )

    async def test_meta_empty_authorized_pages_marks_assets_unavailable(self) -> None:
        adapter = _MetaAuthorizationConnector(resources=[])
        adapter.revoke_credentials = AsyncMock()
        await self._assert_meta_callback_failure(
            adapter,
            raw_state="meta-empty-assets-state",
            failure_code="authorized_assets_unavailable",
        )
        adapter.revoke_credentials.assert_awaited_once()

    async def test_meta_invalid_authorized_resource_is_revoked(self) -> None:
        adapter = _MetaAuthorizationConnector(
            resources=[
                ExternalResource(
                    "facebook_page",
                    "",
                    "Invalid Page",
                )
            ]
        )
        adapter.revoke_credentials = AsyncMock()

        await self._assert_meta_callback_failure(
            adapter,
            raw_state="meta-invalid-assets-state",
            failure_code="authorized_assets_invalid",
        )

        adapter.revoke_credentials.assert_awaited_once()

    async def test_meta_public_profile_is_accepted_without_pages_manage_ads(self) -> None:
        granted_scopes = (
            "pages_show_list",
            "pages_read_engagement",
            "pages_manage_metadata",
            "pages_messaging",
            "leads_retrieval",
            "public_profile",
        )
        adapter = _MetaAuthorizationConnector(
            granted_scopes=granted_scopes
        )
        adapter.revoke_credentials = AsyncMock()

        result, connection, _, _, _ = await self._complete_meta_callback(
            adapter,
            raw_state="meta-public-profile-state",
        )

        self.assertEqual(result.status, "connected")
        self.assertEqual(connection.status, "connected")
        self.assertEqual(connection.authentication_state, "authorized")
        self.assertEqual(set(connection.scopes_granted), set(granted_scopes))
        self.assertNotIn("pages_manage_ads", connection.scopes_granted)
        adapter.revoke_credentials.assert_not_awaited()

    async def test_meta_missing_or_invalid_granted_scopes_are_revoked(self) -> None:
        cases = (
            ("missing", ()),
            ("invalid", ("pages_show_list", "unexpected_scope")),
        )
        for label, granted_scopes in cases:
            with self.subTest(case=label):
                adapter = _MetaAuthorizationConnector(
                    granted_scopes=granted_scopes
                )
                adapter.revoke_credentials = AsyncMock()

                await self._assert_meta_callback_failure(
                    adapter,
                    raw_state=f"meta-{label}-scopes-state",
                    failure_code="granted_scopes_invalid",
                )

                adapter.revoke_credentials.assert_awaited_once()

    async def test_meta_invalid_provider_identity_is_revoked(self) -> None:
        adapter = _MetaAuthorizationConnector(
            identity=ExternalIdentity(
                external_account_reference="",
                display_name="Invalid Meta identity",
            )
        )
        adapter.revoke_credentials = AsyncMock()

        await self._assert_meta_callback_failure(
            adapter,
            raw_state="meta-invalid-identity-state",
            failure_code="provider_identity_invalid",
        )

        adapter.revoke_credentials.assert_awaited_once()

    async def test_meta_revoke_failures_preserve_post_exchange_failure_codes(self) -> None:
        cases = (
            (
                "scopes",
                _MetaAuthorizationConnector(granted_scopes=()),
                "granted_scopes_invalid",
            ),
            (
                "identity",
                _MetaAuthorizationConnector(
                    identity=ExternalIdentity("", "Invalid Meta identity")
                ),
                "provider_identity_invalid",
            ),
            (
                "empty-assets",
                _MetaAuthorizationConnector(resources=[]),
                "authorized_assets_unavailable",
            ),
            (
                "invalid-assets",
                _MetaAuthorizationConnector(
                    resources=[
                        ExternalResource(
                            "facebook_page",
                            "",
                            "Invalid Page",
                        )
                    ]
                ),
                "authorized_assets_invalid",
            ),
        )
        for label, adapter, failure_code in cases:
            with self.subTest(case=label):
                adapter.revoke_credentials = AsyncMock(
                    side_effect=RuntimeError("private-provider-revoke-error")
                )

                await self._assert_meta_callback_failure(
                    adapter,
                    raw_state=f"meta-revoke-{label}-state",
                    failure_code=failure_code,
                )

                adapter.revoke_credentials.assert_awaited_once()

    async def test_meta_revoke_failure_does_not_mask_identity_failure(self) -> None:
        class FailingMetaConnector(_FakeConnector):
            connector_type = "facebook"

            async def get_identity(self, _credentials):
                raise RuntimeError("private-meta-identity-error")

        adapter = FailingMetaConnector()
        adapter.revoke_credentials = AsyncMock(
            side_effect=RuntimeError("private-meta-revoke-error")
        )
        await self._assert_meta_callback_failure(
            adapter,
            raw_state="meta-revoke-failure-state",
            failure_code="provider_identity_fetch_failed",
        )
        adapter.revoke_credentials.assert_awaited_once()


class IntegrationLifecycleServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_meta_disconnect_revokes_tenant_credential_and_clears_assets(self) -> None:
        credential_store = InMemoryIntegrationCredentialStore()
        reference = await credential_store.store(
            business_id=BUSINESS_ID,
            connector_type="facebook",
            purpose="oauth_credentials",
            material=CredentialMaterial(
                values={"access_token": "server-only-system-user-token"}
            ),
        )
        connection = _connection(
            connector_type="facebook",
            credential_reference=reference,
        )
        connection.authorized_resources = [
            {
                "resource_type": "facebook_page",
                "external_reference": "page-1",
                "display_name": "Customer Page",
            }
        ]
        connector = _FakeConnector()
        connector.connector_type = "facebook"

        result = await service.disconnect(
            _Session([connection]),  # type: ignore[arg-type]
            business_id=BUSINESS_ID,
            connection_id=connection.id,
            actor_user_id=USER_ID,
            adapters=ConnectorAdapterRegistry({"facebook": connector}),
            credentials=credential_store,
        )

        self.assertEqual(result.status, "disconnected")
        self.assertEqual(result.authentication_state, "not_authorized")
        self.assertEqual(result.health, "not_checked")
        self.assertIsNone(result.credential_reference)
        self.assertEqual(result.selected_resources, [])
        self.assertEqual(result.authorized_resources, [])
        with self.assertRaises(IntegrationCredentialUnavailableError):
            await credential_store.retrieve(
                reference,
                business_id=BUSINESS_ID,
                connector_type="facebook",
                purpose="oauth_credentials",
            )

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

    async def test_gmail_mail_read_requires_selected_mailbox_and_tenant_bound_credentials(self) -> None:
        credential_store = InMemoryIntegrationCredentialStore()
        reference = await credential_store.store(
            business_id=BUSINESS_ID,
            connector_type="gmail",
            purpose="oauth_credentials",
            material=CredentialMaterial(
                values={"access_token": "server-only-token"}
            ),
        )

        connection = _connection(credential_reference=reference)
        connection.selected_resources = [
            {
                "resource_type": "mailbox",
                "external_reference": "account-1",
                "display_name": "Business mailbox",
            }
        ]

        adapter = _FakeConnector()
        adapter.list_mail_messages = AsyncMock(return_value=[])

        with patch(
            "app.services.integrations._authorized_connection",
            new=AsyncMock(return_value=connection),
        ):
            result = await service.list_mail_messages(
                _Session(),  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                connection_id=connection.id,
                limit=5,
                adapters=ConnectorAdapterRegistry({"gmail": adapter}),
                credentials=credential_store,
            )

        self.assertEqual(result, [])
        adapter.list_mail_messages.assert_awaited_once()
        call = adapter.list_mail_messages.await_args
        self.assertEqual(
            call.args[0].values["access_token"],
            "server-only-token",
        )
        self.assertEqual(call.kwargs["limit"], 5)

        connection.selected_resources = []
        adapter.list_mail_messages.reset_mock()

        with patch(
            "app.services.integrations._authorized_connection",
            new=AsyncMock(return_value=connection),
        ):
            with self.assertRaises(IntegrationStateError):
                await service.list_mail_messages(
                    _Session(),  # type: ignore[arg-type]
                    business_id=BUSINESS_ID,
                    connection_id=connection.id,
                    limit=5,
                    adapters=ConnectorAdapterRegistry({"gmail": adapter}),
                    credentials=credential_store,
                )

        adapter.list_mail_messages.assert_not_awaited()


    async def test_calendar_read_requires_selected_calendar_and_tenant_bound_credentials(self) -> None:
        credential_store = InMemoryIntegrationCredentialStore()
        reference = await credential_store.store(
            business_id=BUSINESS_ID,
            connector_type="google_calendar",
            purpose="oauth_credentials",
            material=CredentialMaterial(
                values={"access_token": "server-only-token"}
            ),
        )

        connection = _connection(
            connector_type="google_calendar",
            credential_reference=reference,
        )
        connection.selected_resources = [
            {
                "resource_type": "calendar",
                "external_reference": "primary@example.com",
                "display_name": "Primary Calendar",
            }
        ]

        starts_at = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
        ends_at = starts_at + timedelta(days=30)

        expected = [
            ExternalCalendarEvent(
                external_event_id="event-1",
                external_calendar_reference="primary@example.com",
                title="Client meeting",
                starts_at=starts_at + timedelta(hours=2),
                ends_at=starts_at + timedelta(hours=3),
                status="confirmed",
                updated_at=starts_at,
            )
        ]

        adapter = _FakeConnector()
        adapter.connector_type = "google_calendar"
        adapter.list_calendar_events = AsyncMock(
            return_value=expected
        )

        with patch(
            "app.services.integrations._authorized_connection",
            new=AsyncMock(return_value=connection),
        ):
            result = await service.list_calendar_events(
                _Session(),  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                connection_id=connection.id,
                starts_at=starts_at,
                ends_at=ends_at,
                adapters=ConnectorAdapterRegistry(
                    {"google_calendar": adapter}
                ),
                credentials=credential_store,
            )

        self.assertEqual(result, expected)

        call = adapter.list_calendar_events.await_args
        self.assertEqual(
            call.args[0].values["access_token"],
            "server-only-token",
        )
        self.assertEqual(
            call.kwargs["calendar_reference"],
            "primary@example.com",
        )
        self.assertEqual(call.kwargs["starts_at"], starts_at)
        self.assertEqual(call.kwargs["ends_at"], ends_at)

        connection.selected_resources = []
        adapter.list_calendar_events.reset_mock()

        with patch(
            "app.services.integrations._authorized_connection",
            new=AsyncMock(return_value=connection),
        ):
            with self.assertRaises(IntegrationStateError):
                await service.list_calendar_events(
                    _Session(),  # type: ignore[arg-type]
                    business_id=BUSINESS_ID,
                    connection_id=connection.id,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    adapters=ConnectorAdapterRegistry(
                        {"google_calendar": adapter}
                    ),
                    credentials=credential_store,
                )

        adapter.list_calendar_events.assert_not_awaited()


    async def test_gmail_content_read_requires_selected_mailbox_and_tenant_bound_credentials(self) -> None:
        credential_store = InMemoryIntegrationCredentialStore()
        reference = await credential_store.store(
            business_id=BUSINESS_ID,
            connector_type="gmail",
            purpose="oauth_credentials",
            material=CredentialMaterial(
                values={"access_token": "server-only-token"}
            ),
        )

        connection = _connection(credential_reference=reference)
        connection.selected_resources = [
            {
                "resource_type": "mailbox",
                "external_reference": "account-1",
                "display_name": "Business mailbox",
            }
        ]

        message = ExternalMailMessageContent(
            external_message_reference="message-1",
            external_thread_reference="thread-1",
            sender="customer@example.com",
            subject="Order 1042",
            snippet="Customer needs help with order 1042.",
            body_text="Hello,\n\nI need help with order 1042.",
        )

        adapter = _FakeConnector()
        adapter.read_mail_message = AsyncMock(return_value=message)

        with patch(
            "app.services.integrations._authorized_connection",
            new=AsyncMock(return_value=connection),
        ):
            result = await service.read_mail_message(
                _Session(),  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                connection_id=connection.id,
                message_reference="message-1",
                adapters=ConnectorAdapterRegistry({"gmail": adapter}),
                credentials=credential_store,
            )

        self.assertIs(result, message)
        adapter.read_mail_message.assert_awaited_once()
        call = adapter.read_mail_message.await_args
        self.assertEqual(
            call.args[0].values["access_token"],
            "server-only-token",
        )
        self.assertEqual(
            call.kwargs["message_reference"],
            "message-1",
        )

        connection.selected_resources = []
        adapter.read_mail_message.reset_mock()

        with patch(
            "app.services.integrations._authorized_connection",
            new=AsyncMock(return_value=connection),
        ):
            with self.assertRaises(IntegrationStateError):
                await service.read_mail_message(
                    _Session(),  # type: ignore[arg-type]
                    business_id=BUSINESS_ID,
                    connection_id=connection.id,
                    message_reference="message-1",
                    adapters=ConnectorAdapterRegistry({"gmail": adapter}),
                    credentials=credential_store,
                )

        adapter.read_mail_message.assert_not_awaited()


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


class _FailingFlushSession(_Session):
    async def flush(self) -> None:
        raise SQLAlchemyError("private-database-error")


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


class _MetaAuthorizationConnector(_FakeConnector):
    connector_type = "facebook"

    def __init__(
        self,
        *,
        granted_scopes: tuple[str, ...] = (
            "pages_show_list",
            "pages_read_engagement",
            "pages_manage_metadata",
            "pages_messaging",
            "leads_retrieval",
            "pages_manage_ads",
        ),
        identity: ExternalIdentity | None = None,
        resources: list[ExternalResource] | None = None,
    ) -> None:
        super().__init__()
        self.granted_scopes = granted_scopes
        self.identity = identity or ExternalIdentity(
            external_account_reference="system-user-1",
            display_name="Meta business integration",
        )
        self.resources = (
            [
                ExternalResource(
                    "facebook_page",
                    "page-1",
                    "Authorized Page",
                )
            ]
            if resources is None
            else resources
        )

    async def exchange_authorization_code(self, **_kwargs):
        return AuthorizationExchange(
            credentials=CredentialMaterial(
                values={
                    "access_token": "server-only-system-user-token",
                    "meta_token_type": "SYSTEM_USER",
                }
            ),
            granted_scopes=self.granted_scopes,
        )

    async def get_identity(self, _credentials):
        return self.identity

    async def list_resources(self, _credentials):
        return self.resources


class _TrackingCredentialStore(InMemoryIntegrationCredentialStore):
    def __init__(self, *, fail_oauth_cleanup: bool = False) -> None:
        super().__init__()
        self.fail_oauth_cleanup = fail_oauth_cleanup
        self.oauth_credential_reference: str | None = None
        self.oauth_cleanup_attempts = 0

    async def store(self, **kwargs) -> str:
        reference = await super().store(**kwargs)
        if kwargs.get("purpose") == "oauth_credentials":
            self.oauth_credential_reference = reference
        return reference

    async def revoke(
        self,
        reference: str,
        *,
        business_id: UUID,
        connector_type: str,
        purpose: str,
    ) -> None:
        if purpose == "oauth_credentials":
            self.oauth_cleanup_attempts += 1
            if self.fail_oauth_cleanup:
                raise IntegrationCredentialUnavailableError(
                    "credential_unavailable"
                )
        await super().revoke(
            reference,
            business_id=business_id,
            connector_type=connector_type,
            purpose=purpose,
        )


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
