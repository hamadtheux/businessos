from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.exceptions.integration import (
    IntegrationConflictError,
    IntegrationCredentialUnavailableError,
    IntegrationNotFoundError,
    IntegrationPersistenceError,
    IntegrationProviderUnavailableError,
    IntegrationStateError,
    IntegrationValidationError,
    IntegrationWebhookVerificationError,
)
from app.exceptions.operations import (
    OperationsConflictError,
    OperationsPersistenceError,
    OperationsValidationError,
)
from app.integrations.adapters import ConnectorAdapterRegistry, connector_adapters
from app.integrations.action_adapters import connector_action_adapters
from app.integrations.contracts import (
    AuthorizationRequest,
    ExternalMailMessage,
    ExternalMailMessageContent,
    ExternalResource,
    NormalizedAdPerformance,
    NormalizedIntegrationEvent,
)
from app.integrations.credentials import (
    CredentialMaterial,
    IntegrationCredentialStore,
    credential_store,
)
from app.integrations.registry import ConnectorDefinition, list_connector_definitions, require_connector
from app.integrations.webhooks import WebhookSignatureVerifier
from app.models.appointment import Appointment
from app.services.billing import require_capacity, require_feature
from app.models.conversation import Conversation, ConversationMessage
from app.models.integration import (
    IntegrationConnection,
    IntegrationEntityLink,
    IntegrationOAuthState,
    IntegrationWebhookEvent,
)
from app.models.marketing import Campaign, MarketingPerformance
from app.models.notification import Notification
from app.schemas.integration import (
    AuthorizationCallbackResponse,
    AuthorizationStartResponse,
    ConnectorDefinitionResponse,
    EntityLinkCreate,
    ResourceSelectionRequest,
)
from app.schemas.marketing import PerformanceCreate
from app.services.automation_events import record_automation_event
from app.services.background_jobs import enqueue_job
from app.services.customer_identity import resolve_customer_identity
from app.services.marketing import derive_metrics
from app.services.operations import record_audit


_RESOURCE_LIMIT = 100
_SAFE_WEBHOOK_FIELDS = frozenset({
    "external_conversation_reference",
    "external_message_reference",
    "sender_email",
    "sender_phone",
    "sender_display_name",
    "content",
    "delivery_status",
    "external_resource_reference",
    "external_campaign_reference",
})
_MESSAGE_CONNECTOR_CHANNEL = {
    "whatsapp_business": "whatsapp",
    "gmail": "email",
    "facebook": "facebook",
    "instagram": "instagram",
    "microsoft_outlook": "email",
}
_CONNECTOR_EVENT_TYPES = {
    "whatsapp_business": frozenset({"message_received", "message_status_updated"}),
    "gmail": frozenset({"email_received"}),
    "google_calendar": frozenset({"calendar_event_changed"}),
    "google_ads": frozenset({"performance_data_available"}),
    "meta_ads": frozenset({"performance_data_available"}),
    "facebook": frozenset({"message_received", "performance_data_available"}),
    "instagram": frozenset({"message_received", "performance_data_available"}),
    "microsoft_outlook": frozenset({"email_received", "calendar_event_changed"}),
}


def connector_catalog(configuration: Settings = settings) -> list[ConnectorDefinitionResponse]:
    result: list[ConnectorDefinitionResponse] = []
    for definition in list_connector_definitions():
        configured = connector_adapters.is_configured(definition.connector_type)
        setup_status = (
            "coming_soon"
            if definition.foundation_only
            else "available"
            if configured
            else "provider_setup_required"
        )
        writes_enabled = any(
            connector_action_adapters.supports(
                definition.connector_type, action_type
            )
            for action_type in (
                "send_email", "send_whatsapp_message", "send_customer_message",
                "publish_social_post", "create_meta_campaign",
                "launch_meta_campaign", "create_google_ads_campaign",
                "launch_google_ads_campaign", "change_ad_budget", "pause_ad_campaign",
            )
        )
        result.append(ConnectorDefinitionResponse(
            connector_type=definition.connector_type,
            display_name=definition.display_name,
            description=definition.description,
            category=definition.category,
            authentication_type=definition.authentication_type,
            capabilities=definition.capabilities,
            read_capabilities=definition.read_capabilities,
            future_write_capabilities=definition.future_write_capabilities,
            requested_scopes=definition.requested_oauth_scopes(
                configuration.external_connector_write_mode
            ),
            webhook_support=definition.webhook_support,
            external_writes_enabled=writes_enabled,
            resource_types=definition.resource_types,
            configuration_requirements=(
                () if configured else (
                    "platform_provider_app_configuration",
                    "secure_credential_store",
                    "oauth_callback_url",
                )
            ),
            resource_selection_required=definition.resource_selection_required,
            setup_status=setup_status,
        ))
    return result


async def list_connections(session: AsyncSession, *, business_id: UUID) -> list[IntegrationConnection]:
    try:
        values = await session.scalars(
            select(IntegrationConnection)
            .where(IntegrationConnection.business_id == business_id)
            .order_by(IntegrationConnection.connector_type, IntegrationConnection.id)
        )
        return list(values.all())
    except SQLAlchemyError:
        raise IntegrationPersistenceError("connections_unavailable") from None


async def get_connection(
    session: AsyncSession, *, business_id: UUID, connection_id: UUID, for_update: bool = False,
) -> IntegrationConnection:
    statement = select(IntegrationConnection).where(
        IntegrationConnection.business_id == business_id,
        IntegrationConnection.id == connection_id,
    )
    if for_update:
        statement = statement.with_for_update()
    try:
        value = await session.scalar(statement)
    except SQLAlchemyError:
        raise IntegrationPersistenceError("connection_unavailable") from None
    if value is None:
        raise IntegrationNotFoundError("connection_not_found")
    return value


async def begin_authorization(
    session: AsyncSession,
    *,
    business_id: UUID,
    user_id: UUID,
    connector_type: str,
    redirect_target: str,
    adapters: ConnectorAdapterRegistry = connector_adapters,
    credentials: IntegrationCredentialStore = credential_store,
    configuration: Settings = settings,
) -> AuthorizationStartResponse:
    if isinstance(session, AsyncSession):
        await require_feature(session, business_id=business_id, key="integrations")
        await require_capacity(session, business_id=business_id, key="max_integrations")
    definition = require_connector(connector_type)
    if definition.foundation_only:
        raise IntegrationProviderUnavailableError("provider_unavailable")
    if redirect_target != "/integrations" or configuration.integration_oauth_callback_url is None:
        raise IntegrationProviderUnavailableError("provider_unavailable")
    now = datetime.now(UTC)
    try:
        recent_attempts = int(await session.scalar(select(func.count()).select_from(IntegrationOAuthState).where(
            IntegrationOAuthState.business_id == business_id,
            IntegrationOAuthState.user_id == user_id,
            IntegrationOAuthState.connector_type == connector_type,
            IntegrationOAuthState.created_at >= now - timedelta(minutes=10),
        )) or 0)
    except SQLAlchemyError:
        raise IntegrationPersistenceError("authorization_unavailable") from None
    if recent_attempts >= 5:
        raise IntegrationConflictError("authorization_rate_limited")

    state = secrets.token_urlsafe(48)
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    expires_at = now + timedelta(seconds=configuration.integration_oauth_state_ttl_seconds)
    verifier_reference = await credentials.store(
        business_id=business_id,
        connector_type=connector_type,
        purpose="oauth_pkce",
        material=_material({"code_verifier": verifier}),
    )
    try:
        authorization_url = await adapters.get(connector_type).build_authorization_url(AuthorizationRequest(
            state=state,
            code_challenge=challenge,
            redirect_uri=str(configuration.integration_oauth_callback_url),
            scopes=definition.requested_oauth_scopes(
                configuration.external_connector_write_mode
            ),
        ))
    except Exception as exc:
        await _best_effort_revoke(
            credentials, verifier_reference, business_id, connector_type, "oauth_pkce"
        )
        if isinstance(exc, (IntegrationProviderUnavailableError, IntegrationCredentialUnavailableError)):
            raise
        raise IntegrationProviderUnavailableError("provider_unavailable") from None
    if not _authorization_url_is_safe(authorization_url, definition):
        await _best_effort_revoke(
            credentials, verifier_reference, business_id, connector_type, "oauth_pkce"
        )
        raise IntegrationProviderUnavailableError("provider_unavailable")

    try:
        connection = await session.scalar(select(IntegrationConnection).where(
            IntegrationConnection.business_id == business_id,
            IntegrationConnection.connector_type == connector_type,
        ).with_for_update())
        if connection is None:
            connection = IntegrationConnection(
                business_id=business_id,
                connector_type=connector_type,
                display_name=definition.display_name,
            )
            session.add(connection)
            await session.flush()
        if connection.status == "connected":
            raise IntegrationConflictError("connection_already_connected")
        connection.status = "pending"
        connection.authentication_state = "authorization_pending"
        connection.health = "not_checked"
        connection.failure_code = None
        connection.connected_by_user_id = user_id
        session.add(IntegrationOAuthState(
            business_id=business_id,
            connector_type=connector_type,
            user_id=user_id,
            state_hash=state_hash,
            pkce_verifier_reference=verifier_reference,
            redirect_target=redirect_target,
            expires_at=expires_at,
        ))
        await session.flush()
    except IntegrationConflictError:
        await _best_effort_revoke(credentials, verifier_reference, business_id, connector_type, "oauth_pkce")
        raise
    except SQLAlchemyError:
        await _best_effort_revoke(credentials, verifier_reference, business_id, connector_type, "oauth_pkce")
        raise IntegrationPersistenceError("authorization_unavailable") from None

    record_audit(
        session,
        business_id=business_id,
        actor_user_id=user_id,
        event_type="integration.authorization_initiated",
        entity_type="integration_connection",
        entity_id=connection.id,
        summary=f"Started authorization for {definition.display_name}.",
        after_value="status=pending",
    )
    return AuthorizationStartResponse(
        connector_type=definition.connector_type,
        authorization_url=authorization_url,
        expires_at=expires_at,
    )


async def complete_authorization(
    session: AsyncSession,
    *,
    connector_type: str | None,
    state: str,
    code: str,
    adapters: ConnectorAdapterRegistry = connector_adapters,
    credentials: IntegrationCredentialStore = credential_store,
    configuration: Settings = settings,
) -> AuthorizationCallbackResponse:
    if not state or len(state) > 512 or not code or len(code) > 4096:
        raise IntegrationValidationError("authorization_callback_invalid")
    if configuration.integration_oauth_callback_url is None:
        raise IntegrationProviderUnavailableError("provider_unavailable")
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    try:
        oauth_state = await session.scalar(
            select(IntegrationOAuthState)
            .where(IntegrationOAuthState.state_hash == state_hash)
            .with_for_update()
        )
    except SQLAlchemyError:
        raise IntegrationPersistenceError("authorization_unavailable") from None
    now = datetime.now(UTC)
    if (
        oauth_state is None
        or (
            connector_type is not None
            and oauth_state.connector_type != connector_type
        )
        or oauth_state.consumed_at is not None
        or oauth_state.expires_at <= now
    ):
        raise IntegrationStateError("authorization_state_invalid")

    connector_type = oauth_state.connector_type
    definition = require_connector(connector_type)

    pkce_material = await credentials.retrieve(
        oauth_state.pkce_verifier_reference,
        business_id=oauth_state.business_id,
        connector_type=connector_type,
        purpose="oauth_pkce",
    )
    verifier = pkce_material.values.get("code_verifier")
    if not verifier:
        raise IntegrationStateError("authorization_state_invalid")
    adapter = adapters.get(connector_type)
    try:
        exchange = await adapter.exchange_authorization_code(
            code=code,
            code_verifier=verifier,
            redirect_uri=str(configuration.integration_oauth_callback_url),
        )
        identity = await adapter.get_identity(exchange.credentials)
    except (IntegrationProviderUnavailableError, IntegrationCredentialUnavailableError):
        raise
    except Exception:
        raise IntegrationProviderUnavailableError("provider_unavailable") from None

    granted = tuple(
        dict.fromkeys(
            _normalize_oauth_scope(scope)
            for scope in exchange.granted_scopes
        )
    )
    if (
        not granted
        or len(granted) > 30
        or not set(granted).issubset(set(definition.oauth_scopes))
    ):
        raise IntegrationStateError("granted_scopes_invalid")
    _validate_identity(identity.external_account_reference, identity.display_name)
    credential_reference = await credentials.store(
        business_id=oauth_state.business_id,
        connector_type=connector_type,
        purpose="oauth_credentials",
        material=exchange.credentials,
    )
    try:
        connection = await session.scalar(select(IntegrationConnection).where(
            IntegrationConnection.business_id == oauth_state.business_id,
            IntegrationConnection.connector_type == connector_type,
        ).with_for_update())
        if connection is None:
            raise IntegrationStateError("authorization_state_invalid")
        previous_reference = connection.credential_reference
        was_previously_connected = connection.connected_at is not None
        connection.status = "connected"
        connection.authentication_state = "authorized"
        connection.health = "not_checked"
        connection.credential_reference = credential_reference
        connection.external_account_reference = identity.external_account_reference
        connection.external_account_display_name = identity.display_name
        connection.scopes_granted = list(granted)
        connection.connected_by_user_id = oauth_state.user_id
        connection.connected_at = now
        connection.failure_code = None
        oauth_state.consumed_at = now
        await session.flush()
    except IntegrationStateError:
        await _best_effort_revoke(credentials, credential_reference, oauth_state.business_id, connector_type, "oauth_credentials")
        raise
    except SQLAlchemyError:
        await _best_effort_revoke(credentials, credential_reference, oauth_state.business_id, connector_type, "oauth_credentials")
        raise IntegrationPersistenceError("authorization_unavailable") from None

    await _best_effort_revoke(
        credentials, oauth_state.pkce_verifier_reference, oauth_state.business_id, connector_type, "oauth_pkce"
    )
    if previous_reference and previous_reference != credential_reference:
        await _best_effort_revoke(
            credentials, previous_reference, oauth_state.business_id, connector_type, "oauth_credentials"
        )
    record_audit(
        session,
        business_id=oauth_state.business_id,
        actor_user_id=oauth_state.user_id,
        event_type="integration.reauthenticated" if was_previously_connected else "integration.connected",
        entity_type="integration_connection",
        entity_id=connection.id,
        summary=(f"Reauthenticated {definition.display_name}." if was_previously_connected else f"Connected {definition.display_name}."),
        after_value="status=connected",
    )
    return AuthorizationCallbackResponse(
        connector_type=definition.connector_type,
        status="connected",
        redirect_target="/integrations",
    )


def _credential_expires_soon(
    material: CredentialMaterial,
    *,
    skew_seconds: int = 60,
) -> bool:
    value = material.values.get("expires_at")

    # Some providers do not return an expiry. In that case we
    # preserve the existing credential rather than refreshing
    # unnecessarily.
    if value is None:
        return False

    if not isinstance(value, str) or not value:
        return True

    try:
        expires_at = datetime.fromisoformat(value)
    except ValueError:
        return True

    if (
        expires_at.tzinfo is None
        or expires_at.utcoffset() is None
    ):
        return True

    return expires_at.astimezone(UTC) <= (
        datetime.now(UTC) + timedelta(seconds=skew_seconds)
    )


async def _provider_read_material(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection: IntegrationConnection,
    adapters: ConnectorAdapterRegistry,
    credentials: IntegrationCredentialStore,
) -> CredentialMaterial:
    reference = _credential_reference(connection)

    material = await credentials.retrieve(
        reference,
        business_id=business_id,
        connector_type=connection.connector_type,
        purpose="oauth_credentials",
    )

    if not _credential_expires_soon(material):
        return material

    refreshed = await refresh_connection_credentials(
        session,
        business_id=business_id,
        connection_id=connection.id,
        adapters=adapters,
        credentials=credentials,
    )

    if (
        refreshed.status != "connected"
        or refreshed.authentication_state != "authorized"
        or not refreshed.credential_reference
    ):
        if refreshed.status in {"reauth_required", "revoked"}:
            raise IntegrationStateError(
                "connection_not_authorized"
            )

        raise IntegrationProviderUnavailableError(
            "provider_unavailable"
        )

    # rotate() preserves the opaque reference, so retrieve the
    # freshly rotated material using the exact tenant binding.
    return await credentials.retrieve(
        refreshed.credential_reference,
        business_id=business_id,
        connector_type=refreshed.connector_type,
        purpose="oauth_credentials",
    )


async def list_resources(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    adapters: ConnectorAdapterRegistry = connector_adapters,
    credentials: IntegrationCredentialStore = credential_store,
) -> list[ExternalResource]:
    connection = await _authorized_connection(session, business_id, connection_id)
    material = await _provider_read_material(
        session,
        business_id=business_id,
        connection=connection,
        adapters=adapters,
        credentials=credentials,
    )
    try:
        resources = list(await adapters.get(connection.connector_type).list_resources(material))
    except (IntegrationProviderUnavailableError, IntegrationCredentialUnavailableError):
        raise
    except Exception:
        raise IntegrationProviderUnavailableError("provider_unavailable") from None
    if len(resources) > _RESOURCE_LIMIT:
        raise IntegrationProviderUnavailableError("provider_response_invalid")
    definition = require_connector(connection.connector_type)
    for resource in resources:
        _validate_resource(resource, definition)
    return resources


async def select_resource(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    actor_user_id: UUID,
    data: ResourceSelectionRequest,
    adapters: ConnectorAdapterRegistry = connector_adapters,
    credentials: IntegrationCredentialStore = credential_store,
) -> IntegrationConnection:
    available = await list_resources(
        session,
        business_id=business_id,
        connection_id=connection_id,
        adapters=adapters,
        credentials=credentials,
    )
    connection = await _authorized_connection(
        session, business_id, connection_id, for_update=True
    )
    selected = next((item for item in available if (
        item.resource_type == data.resource_type
        and item.external_reference == data.external_reference
    )), None)
    if selected is None:
        raise IntegrationValidationError("resource_not_available")
    values = [item for item in connection.selected_resources if item.get("resource_type") != data.resource_type]
    values.append({
        "resource_type": selected.resource_type,
        "external_reference": selected.external_reference,
        "display_name": selected.display_name,
    })
    if len(values) > 20:
        raise IntegrationValidationError("resource_selection_invalid")
    connection.selected_resources = values
    try:
        await session.flush()
    except SQLAlchemyError:
        raise IntegrationPersistenceError("resource_selection_unavailable") from None
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        event_type="integration.resource_selected",
        entity_type="integration_connection",
        entity_id=connection.id,
        summary=f"Selected a {selected.resource_type} resource for {connection.display_name}.",
    )
    return connection


async def check_health(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    actor_user_id: UUID,
    adapters: ConnectorAdapterRegistry = connector_adapters,
    credentials: IntegrationCredentialStore = credential_store,
) -> IntegrationConnection:
    connection = await _authorized_connection(session, business_id, connection_id, for_update=True)
    material = await _provider_read_material(
        session,
        business_id=business_id,
        connection=connection,
        adapters=adapters,
        credentials=credentials,
    )
    try:
        result = await adapters.get(connection.connector_type).health_check(material)
    except (IntegrationProviderUnavailableError, IntegrationCredentialUnavailableError):
        raise
    except Exception:
        raise IntegrationProviderUnavailableError("provider_unavailable") from None
    if result.health not in {"healthy", "degraded", "reauth_required", "revoked"}:
        raise IntegrationProviderUnavailableError("provider_response_invalid")
    before = connection.health
    connection.health = result.health
    connection.status = "connected" if result.health == "healthy" else result.health
    connection.authentication_state = "revoked" if result.health == "revoked" else (
        "failed" if result.health == "reauth_required" else "authorized"
    )
    connection.failure_code = _safe_failure_code(result.failure_code)
    connection.last_health_check_at = datetime.now(UTC)
    if result.health == "healthy":
        connection.failure_code = None
    try:
        await session.flush()
    except SQLAlchemyError:
        raise IntegrationPersistenceError("health_check_unavailable") from None
    if before != result.health:
        _record_health_change(session, connection, actor_user_id, before)
    return connection


async def list_mail_messages(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    limit: int,
    adapters: ConnectorAdapterRegistry = connector_adapters,
    credentials: IntegrationCredentialStore = credential_store,
) -> list[ExternalMailMessage]:
    if not 1 <= limit <= 20:
        raise IntegrationValidationError("mail_read_limit_invalid")

    connection = await _authorized_connection(
        session,
        business_id,
        connection_id,
    )
    if connection.connector_type != "gmail":
        raise IntegrationValidationError("mail_connector_invalid")

    mailbox_selected = any(
        item.get("resource_type") == "mailbox"
        and item.get("external_reference")
        == connection.external_account_reference
        for item in connection.selected_resources
    )
    if not mailbox_selected:
        raise IntegrationStateError("mailbox_selection_required")

    material = await _provider_read_material(
        session,
        business_id=business_id,
        connection=connection,
        adapters=adapters,
        credentials=credentials,
    )

    adapter = adapters.get("gmail")
    read_mail = getattr(adapter, "list_mail_messages", None)
    if not callable(read_mail):
        raise IntegrationProviderUnavailableError("provider_unavailable")

    try:
        messages = list(
            await read_mail(
                material,
                limit=limit,
            )
        )
    except (
        IntegrationProviderUnavailableError,
        IntegrationCredentialUnavailableError,
    ):
        raise
    except Exception:
        raise IntegrationProviderUnavailableError(
            "provider_unavailable"
        ) from None

    if len(messages) > limit:
        raise IntegrationProviderUnavailableError(
            "provider_response_invalid"
        )

    return messages


async def read_mail_message(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    message_reference: str,
    adapters: ConnectorAdapterRegistry = connector_adapters,
    credentials: IntegrationCredentialStore = credential_store,
) -> ExternalMailMessageContent:
    if not 1 <= len(message_reference) <= 255:
        raise IntegrationValidationError("mail_message_reference_invalid")

    connection = await _authorized_connection(
        session,
        business_id,
        connection_id,
    )

    if connection.connector_type != "gmail":
        raise IntegrationValidationError("mail_connector_invalid")

    mailbox_selected = any(
        item.get("resource_type") == "mailbox"
        and item.get("external_reference")
        == connection.external_account_reference
        for item in connection.selected_resources
    )

    if not mailbox_selected:
        raise IntegrationStateError("mailbox_selection_required")

    material = await _provider_read_material(
        session,
        business_id=business_id,
        connection=connection,
        adapters=adapters,
        credentials=credentials,
    )

    adapter = adapters.get("gmail")
    read_mail = getattr(adapter, "read_mail_message", None)

    if not callable(read_mail):
        raise IntegrationProviderUnavailableError(
            "provider_unavailable"
        )

    try:
        message = await read_mail(
            material,
            message_reference=message_reference,
        )
    except (
        IntegrationProviderUnavailableError,
        IntegrationCredentialUnavailableError,
    ):
        raise
    except Exception:
        raise IntegrationProviderUnavailableError(
            "provider_unavailable"
        ) from None

    if message.external_message_reference != message_reference:
        raise IntegrationProviderUnavailableError(
            "provider_response_invalid"
        )

    return message


async def disconnect(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    actor_user_id: UUID,
    adapters: ConnectorAdapterRegistry = connector_adapters,
    credentials: IntegrationCredentialStore = credential_store,
) -> IntegrationConnection:
    connection = await get_connection(
        session, business_id=business_id, connection_id=connection_id, for_update=True
    )
    reference = connection.credential_reference
    if reference:
        try:
            material = await credentials.retrieve(
                reference,
                business_id=business_id,
                connector_type=connection.connector_type,
                purpose="oauth_credentials",
            )
            try:
                await adapters.get(connection.connector_type).revoke_credentials(material)
            except Exception:
                pass
            await credentials.revoke(
                reference,
                business_id=business_id,
                connector_type=connection.connector_type,
                purpose="oauth_credentials",
            )
        except IntegrationCredentialUnavailableError:
            pass
    connection.status = "disabled"
    connection.authentication_state = "revoked"
    connection.health = "revoked"
    connection.credential_reference = None
    connection.selected_resources = []
    connection.scopes_granted = []
    connection.failure_code = None
    try:
        await session.flush()
    except SQLAlchemyError:
        raise IntegrationPersistenceError("disconnect_unavailable") from None
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        event_type="integration.disconnected",
        entity_type="integration_connection",
        entity_id=connection.id,
        summary=f"Disconnected {connection.display_name}; external operations are blocked.",
        after_value="status=disabled",
    )
    if reference:
        record_audit(
            session,
            business_id=business_id,
            actor_user_id=actor_user_id,
            event_type="integration.credential_revoked",
            entity_type="integration_connection",
            entity_id=connection.id,
            summary=f"Revoked the credential reference for {connection.display_name}.",
        )
        session.add(Notification(
            business_id=business_id,
            recipient_user_id=connection.connected_by_user_id,
            category="integration_health",
            title=f"{connection.display_name} disconnected",
            message="The local connection is disabled and its credential reference was removed.",
            priority="medium",
            related_entity_type="integration_connection",
            related_entity_id=connection.id,
        ))
    return connection


async def refresh_connection_credentials(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    actor_user_id: UUID | None = None,
    adapters: ConnectorAdapterRegistry = connector_adapters,
    credentials: IntegrationCredentialStore = credential_store,
) -> IntegrationConnection:
    """Typed refresh foundation; no scheduler or background worker is enabled."""
    connection = await _authorized_connection(
        session, business_id, connection_id, for_update=True
    )
    reference = _credential_reference(connection)
    material = await credentials.retrieve(
        reference,
        business_id=business_id,
        connector_type=connection.connector_type,
        purpose="oauth_credentials",
    )
    try:
        result = await adapters.get(connection.connector_type).refresh_credentials(material)
    except (IntegrationProviderUnavailableError, IntegrationCredentialUnavailableError):
        raise
    except Exception:
        raise IntegrationProviderUnavailableError("provider_unavailable") from None
    before = connection.health
    if result.status == "refreshed":
        if result.credentials is None:
            raise IntegrationProviderUnavailableError("provider_response_invalid")
        await credentials.rotate(
            reference,
            business_id=business_id,
            connector_type=connection.connector_type,
            purpose="oauth_credentials",
            material=result.credentials,
        )
        connection.status = "connected"
        connection.authentication_state = "authorized"
        connection.health = "healthy"
        connection.failure_code = None
        event_type = "integration.credentials_refreshed"
    elif result.status == "reauth_required":
        connection.status = "reauth_required"
        connection.authentication_state = "failed"
        connection.health = "reauth_required"
        connection.failure_code = _safe_failure_code(result.failure_code) or "reauth_required"
        event_type = "integration.reauthentication_required"
    elif result.status == "revoked":
        await credentials.revoke(
            reference,
            business_id=business_id,
            connector_type=connection.connector_type,
            purpose="oauth_credentials",
        )
        connection.status = "revoked"
        connection.authentication_state = "revoked"
        connection.health = "revoked"
        connection.credential_reference = None
        connection.selected_resources = []
        connection.scopes_granted = []
        connection.failure_code = _safe_failure_code(result.failure_code) or "connection_revoked"
        event_type = "integration.credential_revoked"
    elif result.status == "temporary_failure":
        connection.status = "degraded"
        connection.authentication_state = "authorized"
        connection.health = "degraded"
        connection.failure_code = _safe_failure_code(result.failure_code) or "temporary_failure"
        event_type = "integration.refresh_degraded"
    else:
        raise IntegrationProviderUnavailableError("provider_response_invalid")
    connection.last_health_check_at = datetime.now(UTC)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise IntegrationPersistenceError("credential_refresh_unavailable") from None
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        entity_type="integration_connection",
        entity_id=connection.id,
        summary=f"Credential refresh state for {connection.display_name}: {result.status}.",
        before_value=f"health={before}",
        after_value=f"health={connection.health}",
    )
    if before != connection.health and connection.health != "healthy":
        _record_health_change(session, connection, actor_user_id, before)
    return connection


async def list_webhook_events(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    page: int,
    page_size: int,
) -> tuple[list[IntegrationWebhookEvent], int]:
    if page < 1 or page_size < 1 or page_size > 100:
        raise IntegrationValidationError("pagination_invalid")
    await get_connection(session, business_id=business_id, connection_id=connection_id)
    where = (
        IntegrationWebhookEvent.business_id == business_id,
        IntegrationWebhookEvent.integration_connection_id == connection_id,
    )
    try:
        total = int(await session.scalar(
            select(func.count()).select_from(IntegrationWebhookEvent).where(*where)
        ) or 0)
        values = list((await session.scalars(
            select(IntegrationWebhookEvent)
            .where(*where)
            .order_by(IntegrationWebhookEvent.received_at.desc(), IntegrationWebhookEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).all())
    except SQLAlchemyError:
        raise IntegrationPersistenceError("events_unavailable") from None
    return values, total


async def ingest_webhook(
    session: AsyncSession,
    *,
    connector_type: str,
    connection_id: UUID,
    body: bytes,
    headers: Mapping[str, str],
    payload: Mapping[str, object],
    verifier: WebhookSignatureVerifier,
    adapters: ConnectorAdapterRegistry = connector_adapters,
) -> IntegrationWebhookEvent:
    definition = require_connector(connector_type)
    if not definition.webhook_support or not verifier.verify(body=body, headers=headers):
        raise IntegrationWebhookVerificationError("webhook_verification_failed")
    try:
        connection = await session.scalar(select(IntegrationConnection).where(
            IntegrationConnection.id == connection_id,
            IntegrationConnection.connector_type == connector_type,
            IntegrationConnection.status.in_(("connected", "degraded")),
            IntegrationConnection.authentication_state == "authorized",
        ))
    except SQLAlchemyError:
        raise IntegrationPersistenceError("webhook_unavailable") from None
    if connection is None:
        raise IntegrationNotFoundError("connection_not_found")
    if not _webhook_matches_selected_resources(
        connector_type, payload, connection.selected_resources
    ):
        raise IntegrationWebhookVerificationError("webhook_account_mismatch")
    adapter = adapters.get(connector_type)
    try:
        normalize_many = getattr(adapter, "normalize_webhooks", None)
        if callable(normalize_many):
            normalized_events = tuple(await normalize_many(payload))
        else:
            normalized_events = (await adapter.normalize_webhook(payload),)
    except IntegrationProviderUnavailableError:
        raise
    except Exception:
        raise IntegrationProviderUnavailableError("provider_response_invalid") from None

    # A verified provider request must produce at least one canonical event.
    # Bound the fan-out and fail closed rather than silently dropping provider
    # evidence from an unexpectedly large webhook.
    if not normalized_events or len(normalized_events) > 100:
        raise IntegrationProviderUnavailableError("provider_response_invalid")

    persisted_events: list[IntegrationWebhookEvent] = []

    try:
        for normalized in normalized_events:
            external_event_id = _validate_normalized_event(
                normalized, connector_type
            )
            safe_payload = _safe_webhook_payload(normalized)
            safe_payload["occurred_at"] = (
                normalized.occurred_at.astimezone(UTC).isoformat()
            )

            event_id = uuid4()
            inserted_id = await session.scalar(
                pg_insert(IntegrationWebhookEvent)
                .values(
                    id=event_id,
                    business_id=connection.business_id,
                    integration_connection_id=connection.id,
                    connector_type=connector_type,
                    external_event_id=external_event_id,
                    event_type=normalized.event_type,
                    status="received",
                    normalized_payload=safe_payload,
                    received_at=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(
                    constraint="uq_integration_webhook_events_connection_external"
                )
                .returning(IntegrationWebhookEvent.id)
            )

            if inserted_id is None:
                event = await session.scalar(
                    select(IntegrationWebhookEvent).where(
                        IntegrationWebhookEvent.integration_connection_id
                        == connection.id,
                        IntegrationWebhookEvent.external_event_id
                        == external_event_id,
                    )
                )
                if event is None:
                    raise IntegrationPersistenceError("webhook_unavailable")
            else:
                event = await session.scalar(
                    select(IntegrationWebhookEvent)
                    .where(
                        IntegrationWebhookEvent.id == inserted_id,
                        IntegrationWebhookEvent.business_id == connection.business_id,
                    )
                    .with_for_update()
                )
                if event is None:
                    raise IntegrationPersistenceError("webhook_unavailable")

                await enqueue_job(
                    session,
                    business_id=connection.business_id,
                    job_type="process_integration_event",
                    idempotency_key=f"integration-event:{event.id}",
                    integration_event_id=event.id,
                )

            persisted_events.append(event)

        await session.flush()

        # Preserve the established singular service/API contract. All
        # normalized events above are durable and queued; the first canonical
        # event is simply the representative return value for this HTTP call.
        return persisted_events[0]

    except IntegrationPersistenceError:
        raise
    except SQLAlchemyError:
        raise IntegrationPersistenceError("webhook_unavailable") from None


async def process_integration_webhook_event(
    session: AsyncSession,
    *,
    business_id: UUID,
    event_id: UUID,
) -> IntegrationWebhookEvent:
    """Process one already verified, normalized event without external writes."""
    event = await session.scalar(select(IntegrationWebhookEvent).where(
        IntegrationWebhookEvent.id == event_id,
        IntegrationWebhookEvent.business_id == business_id,
    ).with_for_update())
    if event is None:
        raise IntegrationNotFoundError("event_not_found")
    if event.status == "processed":
        return event
    if event.status not in {"received", "failed"}:
        raise IntegrationStateError("event_not_processable")
    connection = await session.scalar(select(IntegrationConnection).where(
        IntegrationConnection.id == event.integration_connection_id,
        IntegrationConnection.business_id == business_id,
    ))
    if connection is None:
        raise IntegrationNotFoundError("connection_not_found")
    if (
        connection.business_id != business_id
        or connection.id != event.integration_connection_id
    ):
        raise IntegrationNotFoundError("connection_not_found")
    occurred_at_value = event.normalized_payload.get("occurred_at")
    try:
        occurred_at = (
            datetime.fromisoformat(str(occurred_at_value)).astimezone(UTC)
            if occurred_at_value else event.received_at.astimezone(UTC)
        )
    except (TypeError, ValueError):
        raise IntegrationValidationError("event_payload_invalid") from None
    if event.event_type in {"message_received", "email_received"}:
        await _record_inbound_message(session, connection, event, occurred_at)
    elif event.event_type == "message_status_updated":
        await _reconcile_message_delivery(session, connection, event)
    event.status = "processed"
    event.processed_at = datetime.now(UTC)
    event.failure_code = None
    record_automation_event(
        session,
        business_id=business_id,
        event_type="integration_event_received",
        entity_type="integration_webhook_event",
        entity_id=event.id,
        payload={"connector_type": event.connector_type, "category": event.event_type},
        occurred_at=occurred_at,
    )
    await session.flush()
    return event


async def create_entity_link(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    actor_user_id: UUID,
    data: EntityLinkCreate,
) -> IntegrationEntityLink:
    connection = await _authorized_connection(session, business_id, connection_id)
    definition = require_connector(connection.connector_type)
    if data.external_resource_reference not in {
        item.get("external_reference") for item in connection.selected_resources
    }:
        raise IntegrationValidationError("resource_not_selected")
    model = {"appointment": Appointment, "campaign": Campaign, "conversation": Conversation}[data.internal_entity_type]
    if not await session.scalar(select(model.id).where(
        model.id == data.internal_entity_id, model.business_id == business_id
    )):
        raise IntegrationValidationError("internal_entity_invalid")
    if not definition.resource_types:
        raise IntegrationValidationError("resource_not_supported")
    link = IntegrationEntityLink(
        business_id=business_id,
        integration_connection_id=connection_id,
        **data.model_dump(),
    )
    session.add(link)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise IntegrationConflictError("entity_link_conflict") from None
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        event_type="integration.entity_link_created",
        entity_type="integration_entity_link",
        entity_id=link.id,
        summary=f"Linked an internal {data.internal_entity_type} to {connection.display_name}.",
    )
    return link


async def ingest_ad_performance(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    campaign_id: UUID,
    actor_user_id: UUID,
    channel: str,
    normalized: NormalizedAdPerformance,
) -> MarketingPerformance:
    connection = await _authorized_connection(session, business_id, connection_id)
    if connection.connector_type not in {"google_ads", "meta_ads"}:
        raise IntegrationValidationError("performance_connector_invalid")
    link = await session.scalar(select(IntegrationEntityLink).where(
        IntegrationEntityLink.business_id == business_id,
        IntegrationEntityLink.integration_connection_id == connection_id,
        IntegrationEntityLink.internal_entity_type == "campaign",
        IntegrationEntityLink.internal_entity_id == campaign_id,
        IntegrationEntityLink.external_entity_id == normalized.external_campaign_reference,
    ))
    if link is None:
        raise IntegrationValidationError("campaign_link_required")
    data = PerformanceCreate(
        campaign_id=campaign_id,
        channel=channel,
        period_start=normalized.period_start,
        period_end=normalized.period_end,
        data_source="import",
        spend=normalized.spend,
        impressions=normalized.impressions,
        reach=normalized.reach,
        clicks=normalized.clicks,
        leads=normalized.leads,
        conversions=normalized.conversions,
        revenue=normalized.revenue,
    )
    value = await session.scalar(select(MarketingPerformance).where(
        MarketingPerformance.business_id == business_id,
        MarketingPerformance.campaign_id == campaign_id,
        MarketingPerformance.channel == channel,
        MarketingPerformance.period_start == normalized.period_start,
        MarketingPerformance.period_end == normalized.period_end,
        MarketingPerformance.data_source == "future_connector",
        MarketingPerformance.external_campaign_reference == normalized.external_campaign_reference,
    ).with_for_update())
    values = data.model_dump() | {
        "data_source": "future_connector",
        "attribution_class": "provider_attributed",
        "external_campaign_reference": normalized.external_campaign_reference,
    } | derive_metrics(data)
    if value is None:
        value = MarketingPerformance(business_id=business_id, **values)
        session.add(value)
    else:
        for key, item in values.items():
            setattr(value, key, item)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise IntegrationPersistenceError("performance_ingestion_unavailable") from None
    record_audit(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        event_type="integration.performance_ingested",
        entity_type="marketing_performance",
        entity_id=value.id,
        summary=f"Recorded read-only {connection.display_name} performance for a linked campaign.",
    )
    return value


async def _authorized_connection(
    session: AsyncSession,
    business_id: UUID,
    connection_id: UUID,
    *,
    for_update: bool = False,
) -> IntegrationConnection:
    connection = await get_connection(
        session, business_id=business_id, connection_id=connection_id, for_update=for_update
    )
    if (
        connection.status not in {"connected", "degraded"}
        or connection.authentication_state != "authorized"
        or not connection.credential_reference
    ):
        raise IntegrationStateError("connection_not_authorized")
    return connection


async def _record_inbound_message(
    session: AsyncSession,
    connection: IntegrationConnection,
    event: IntegrationWebhookEvent,
    occurred_at: datetime,
) -> None:
    payload = event.normalized_payload
    conversation_reference = str(payload.get("external_conversation_reference") or "").strip()
    message_reference = str(payload.get("external_message_reference") or event.external_event_id).strip()
    content = str(payload.get("content") or "").strip()
    channel = _MESSAGE_CONNECTOR_CHANNEL.get(connection.connector_type)
    if not channel or not conversation_reference or len(conversation_reference) > 255 or not content or len(content) > 10_000:
        raise IntegrationValidationError("message_payload_invalid")
    customer_id = await _match_customer(
        session,
        business_id=connection.business_id,
        display_name=_optional_text(payload.get("sender_display_name"), 160),
        email=_optional_text(payload.get("sender_email"), 320),
        phone=_optional_text(payload.get("sender_phone"), 32),
        source=connection.connector_type,
    )
    conversation = await session.scalar(select(Conversation).where(
        Conversation.business_id == connection.business_id,
        Conversation.channel == channel,
        Conversation.external_reference == conversation_reference,
    ).with_for_update())
    if conversation is None:
        conversation = Conversation(
            business_id=connection.business_id,
            customer_id=customer_id,
            integration_connection_id=connection.id,
            channel=channel,
            external_reference=conversation_reference,
            status="open",
            last_activity_at=occurred_at,
        )
        session.add(conversation)
        await session.flush()
    else:
        if conversation.integration_connection_id not in {None, connection.id}:
            raise IntegrationValidationError("conversation_connection_conflict")
        conversation.integration_connection_id = connection.id
        if conversation.customer_id is None and customer_id is not None:
            conversation.customer_id = customer_id
        conversation.last_activity_at = max(conversation.last_activity_at, occurred_at)
        if conversation.status == "resolved":
            conversation.status = "open"
    existing_message = await session.scalar(select(ConversationMessage.id).where(
        ConversationMessage.business_id == connection.business_id,
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.external_reference == message_reference[:255],
    ))
    if existing_message is not None:
        return
    message = ConversationMessage(
        business_id=connection.business_id,
        conversation_id=conversation.id,
        direction="inbound",
        sender_type="customer",
        content=content,
        sent_at=occurred_at,
        external_reference=message_reference[:255],
        delivery_status="received",
    )
    session.add(message)
    await session.flush()
    inbound_event = record_automation_event(
        session,
        business_id=connection.business_id,
        event_type="inbound_message_recorded",
        entity_type="conversation_message",
        entity_id=message.id,
        payload={"channel": channel, "status": conversation.status},
        occurred_at=occurred_at,
    )

    # Persist the outbox event before enqueue_job validates its tenant-scoped
    # reference. The job and inbound message still commit atomically with the
    # surrounding integration-event transaction.
    await session.flush()

    await enqueue_job(
        session,
        business_id=connection.business_id,
        job_type="customer_agent_response",
        idempotency_key=f"customer-agent-response:{message.id}",
        automation_event_id=inbound_event.id,
    )

    record_audit(
        session,
        business_id=connection.business_id,
        actor_user_id=None,
        event_type="integration.inbound_message_recorded",
        entity_type="conversation_message",
        entity_id=message.id,
        summary=f"Recorded an inbound {channel} message from a verified connector event.",
    )


async def _reconcile_message_delivery(
    session: AsyncSession,
    connection: IntegrationConnection,
    event: IntegrationWebhookEvent,
) -> None:
    payload = event.normalized_payload
    external_reference = str(payload.get("external_message_reference") or "").strip()
    raw_status = str(payload.get("delivery_status") or "").strip().casefold()
    normalized = {
        "accepted": "submitted",
        "submitted": "submitted",
        "sent": "sent",
        "delivered": "delivered",
        "read": "read",
        "failed": "failed",
        "undeliverable": "failed",
    }.get(raw_status)
    if not external_reference or len(external_reference) > 255 or normalized is None:
        raise IntegrationValidationError("message_status_payload_invalid")
    message = await session.scalar(
        select(ConversationMessage)
        .join(
            Conversation,
            (Conversation.id == ConversationMessage.conversation_id)
            & (Conversation.business_id == ConversationMessage.business_id),
        )
        .where(
            ConversationMessage.business_id == connection.business_id,
            ConversationMessage.direction == "outbound",
            ConversationMessage.external_reference == external_reference,
            Conversation.integration_connection_id == connection.id,
        )
        .with_for_update()
    )
    if message is None:
        # Provider callbacks may race the transaction that records a
        # provider-accepted outbound message. Preserve the verified event for
        # bounded worker retry instead of terminally discarding its evidence.
        raise IntegrationPersistenceError("outbound_message_not_available")
    if message.business_id != connection.business_id or message.direction != "outbound":
        raise IntegrationPersistenceError("outbound_message_scope_conflict")
    rank = {"recorded": 0, "submitted": 1, "sent": 2, "delivered": 3, "read": 4}
    if normalized == "failed" or rank.get(normalized, 0) > rank.get(message.delivery_status, -1):
        message.delivery_status = normalized
    record_audit(
        session,
        business_id=connection.business_id,
        actor_user_id=None,
        event_type="integration.message_delivery_updated",
        entity_type="conversation_message",
        entity_id=message.id,
        summary=f"Reconciled outbound message delivery state to {message.delivery_status}.",
    )


async def _match_customer(
    session: AsyncSession,
    *,
    business_id: UUID,
    display_name: str | None,
    email: str | None,
    phone: str | None,
    source: str,
) -> UUID | None:
    """
    Resolve a verified inbound sender through the canonical Customer Identity
    Engine.

    A deterministic email or phone identity may automatically create a
    customer. Display name alone is never sufficient identity authority.

    Ambiguous or invalid identities deliberately remain unlinked so the
    inbound conversation is still recorded without guessing who the customer
    is.
    """
    try:
        resolution = await resolve_customer_identity(
            session,
            business_id=business_id,
            display_name=display_name,
            email=email,
            phone=phone,
            source=source,
            create_if_missing=bool(email or phone),
            actor_user_id=None,
        )
    except OperationsConflictError:
        # Never guess between multiple possible customer identities.
        # Preserve the inbound conversation as anonymous/unlinked.
        return None
    except OperationsValidationError:
        # A malformed provider identity must not cause a legitimate inbound
        # message to disappear. Record the conversation without linking it.
        return None
    except OperationsPersistenceError:
        raise IntegrationPersistenceError(
            "customer_identity_unavailable"
        ) from None

    customer = resolution.customer
    return customer.id if customer is not None else None


def _record_health_change(
    session: AsyncSession,
    connection: IntegrationConnection,
    actor_user_id: UUID | None,
    before: str,
) -> None:
    record_audit(
        session,
        business_id=connection.business_id,
        actor_user_id=actor_user_id,
        event_type="integration.health_changed",
        entity_type="integration_connection",
        entity_id=connection.id,
        summary=f"{connection.display_name} health changed from {before} to {connection.health}.",
        before_value=f"health={before}",
        after_value=f"health={connection.health}",
    )
    record_automation_event(
        session,
        business_id=connection.business_id,
        event_type="integration_health_changed",
        entity_type="integration_connection",
        entity_id=connection.id,
        payload={"connector_type": connection.connector_type, "health": connection.health, "previous_status": before},
    )
    if connection.health != "healthy":
        session.add(Notification(
            business_id=connection.business_id,
            recipient_user_id=connection.connected_by_user_id,
            category="integration_health",
            title=f"{connection.display_name} needs attention",
            message=f"Connection health is {connection.health.replace('_', ' ')}. Review the integration before relying on new data.",
            priority="high" if connection.health in {"reauth_required", "revoked"} else "medium",
            related_entity_type="integration_connection",
            related_entity_id=connection.id,
        ))


def _webhook_matches_selected_resources(
    connector_type: str,
    payload: Mapping[str, object],
    selected_resources: list[dict[str, object]],
) -> bool:
    """Bind signed Meta webhook evidence to the selected tenant resources."""
    if connector_type not in {
        "whatsapp_business", "facebook", "instagram", "meta_ads"
    }:
        return True
    selected: dict[str, set[str]] = {}
    for item in selected_resources:
        resource_type = item.get("resource_type")
        reference = item.get("external_reference")
        if isinstance(resource_type, str) and isinstance(reference, str):
            selected.setdefault(resource_type, set()).add(reference)
    raw_entries = payload.get("entry")
    if not isinstance(raw_entries, list) or not raw_entries:
        return False
    entry_ids = {
        str(entry.get("id"))
        for entry in raw_entries
        if isinstance(entry, Mapping) and entry.get("id") is not None
    }
    if not entry_ids:
        return False
    if connector_type == "whatsapp_business":
        if not entry_ids.issubset(selected.get("whatsapp_business_account", set())):
            return False
        phone_ids: set[str] = set()
        for entry in raw_entries:
            changes = entry.get("changes") if isinstance(entry, Mapping) else None
            if not isinstance(changes, list):
                continue
            for change in changes:
                value = change.get("value") if isinstance(change, Mapping) else None
                metadata = value.get("metadata") if isinstance(value, Mapping) else None
                phone_id = metadata.get("phone_number_id") if isinstance(metadata, Mapping) else None
                if phone_id is not None:
                    phone_ids.add(str(phone_id))
        return not phone_ids or phone_ids.issubset(selected.get("phone_number", set()))
    allowed_types = {
        "facebook": ("facebook_page",),
        "instagram": ("instagram_account", "facebook_page"),
        "meta_ads": ("meta_business", "ad_account"),
    }[connector_type]
    allowed = set().union(*(selected.get(item, set()) for item in allowed_types))
    return bool(allowed) and entry_ids.issubset(allowed)


def _safe_webhook_payload(normalized: NormalizedIntegrationEvent) -> dict[str, object]:
    if not normalized.external_event_id or len(normalized.external_event_id) > 255:
        raise IntegrationValidationError("event_invalid")
    result: dict[str, object] = {}
    for key, value in normalized.safe_payload.items():
        if key not in _SAFE_WEBHOOK_FIELDS or value is None:
            continue
        if not isinstance(value, (str, int, bool)):
            raise IntegrationValidationError("event_payload_invalid")
        if isinstance(value, str):
            limits = {
                "content": 10_000,
                "sender_email": 320,
                "sender_phone": 32,
                "sender_display_name": 160,
                "delivery_status": 64,
            }
            value = value.strip()[:limits.get(key, 255)]
            if not value:
                continue
            if key == "sender_email":
                value = value.casefold()
            elif key == "sender_phone":
                digits = "".join(character for character in value if character.isdigit())
                if len(digits) < 7:
                    raise IntegrationValidationError("event_payload_invalid")
                value = f"+{digits}" if value.startswith("+") else digits
        result[key] = value
    if len(json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > 16_384:
        raise IntegrationValidationError("event_payload_invalid")
    return result


def _validate_normalized_event(
    event: NormalizedIntegrationEvent,
    connector_type: str,
) -> str:
    external_event_id = event.external_event_id.strip()
    if (
        not external_event_id
        or len(external_event_id) > 255
        or event.event_type not in _CONNECTOR_EVENT_TYPES.get(connector_type, frozenset())
        or event.occurred_at.tzinfo is None
        or event.occurred_at.utcoffset() is None
        or event.occurred_at > datetime.now(UTC) + timedelta(minutes=5)
    ):
        raise IntegrationProviderUnavailableError("provider_response_invalid")
    return external_event_id


def _validate_resource(resource: ExternalResource, definition: ConnectorDefinition) -> None:
    if (
        resource.resource_type not in definition.resource_types
        or not resource.external_reference
        or len(resource.external_reference) > 255
        or not resource.display_name
        or len(resource.display_name) > 160
        or (resource.parent_reference is not None and len(resource.parent_reference) > 255)
        or (resource.metadata is not None and (
            len(resource.metadata) > 20
            or any(len(key) > 64 or len(value) > 255 for key, value in resource.metadata.items())
        ))
    ):
        raise IntegrationProviderUnavailableError("provider_response_invalid")


def _normalize_oauth_scope(value: str) -> str:
    """Normalize provider aliases into the canonical scope names we request."""
    aliases = {
        "https://www.googleapis.com/auth/userinfo.email": "email",
    }
    return aliases.get(value, value)


def _authorization_url_is_safe(value: str, definition: ConnectorDefinition) -> bool:
    if not value or len(value) > 4096:
        return False
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname in definition.trusted_authorization_hosts
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def _validate_identity(reference: str, display_name: str) -> None:
    if not reference.strip() or len(reference) > 255 or not display_name.strip() or len(display_name) > 160:
        raise IntegrationProviderUnavailableError("provider_response_invalid")


def _credential_reference(connection: IntegrationConnection) -> str:
    if not connection.credential_reference:
        raise IntegrationStateError("connection_not_authorized")
    return connection.credential_reference


def _safe_failure_code(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.isascii() or not value.replace("_", "a").isalnum() or not value[0].isalpha():
        return "provider_health_failed"
    return value[:64].lower()


def _optional_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] or None


def _material(values: Mapping[str, str]):
    from app.integrations.credentials import CredentialMaterial

    return CredentialMaterial(values=values)


async def _best_effort_revoke(
    store: IntegrationCredentialStore,
    reference: str,
    business_id: UUID,
    connector_type: str,
    purpose: str,
) -> None:
    try:
        await store.revoke(
            reference,
            business_id=business_id,
            connector_type=connector_type,
            purpose=purpose,
        )
    except Exception:
        pass
