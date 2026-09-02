from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings, settings
from app.db.session import AsyncSessionFactory
from app.domain.integrations import (
    ExternalConnectorWritesDisabledError,
    require_external_connector_writes_enabled,
)
from app.exceptions.integration import (
    IntegrationCredentialUnavailableError,
    IntegrationError,
)
from app.integrations.action_adapters import (
    ConnectorActionAdapterRegistry,
    ConnectorRejectedError,
    ConnectorRequestNotSentError,
    connector_action_adapters,
)
from app.integrations.credentials import (
    IntegrationCredentialStore,
    credential_store,
)
from app.integrations.registry import require_connector
from app.models.background_job import BackgroundJob
from app.models.conversation import (
    Conversation,
    ConversationMessage,
    CustomerChannelIdentity,
)
from app.models.customer import Customer
from app.models.integration import IntegrationConnection
from app.schemas.ai_action_payload import (
    ActionPayload,
    SendCustomerMessagePayload,
    SendEmailPayload,
    SendWhatsAppMessagePayload,
)
from app.services.automation_events import record_automation_event
from app.services.billing import BillingEntitlementError, require_feature
from app.services.operations import record_audit


@dataclass(frozen=True, slots=True)
class ConversationMessageDispatchOutcome:
    succeeded: bool
    failure_code: str | None = None
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class ConversationMessageDispatchContext:
    business_id: UUID
    message_id: UUID
    conversation_id: UUID
    connection_id: UUID
    connector_type: str
    credential_reference: str
    action_type: str
    payload: ActionPayload
    selected_resources: tuple[Mapping[str, str], ...]
    delivery_target: str


_TERMINAL_DELIVERY_STATUSES = frozenset(
    {
        "submitted",
        "sent",
        "delivered",
        "read",
        "failed",
        "uncertain",
    }
)


async def dispatch_conversation_message_job(
    job: BackgroundJob,
    *,
    adapters: ConnectorActionAdapterRegistry = connector_action_adapters,
    credentials: IntegrationCredentialStore = credential_store,
    configuration: Settings = settings,
) -> ConversationMessageDispatchOutcome:
    """Dispatch one durable human-authorized customer message.

    Safety protocol:

    1. Revalidate all tenant/provider/customer state.
    2. Commit ``delivery_status='dispatching'`` before provider invocation.
    3. Invoke the provider with no open database transaction.
    4. Persist a definite submitted/failed result.
    5. Any ambiguous post-invocation outcome becomes ``uncertain``.
    6. Re-entry of an already-dispatching message never calls the provider.
    """
    message_id = job.conversation_message_id
    if message_id is None:
        return ConversationMessageDispatchOutcome(False, "invalid_job_state")

    # ------------------------------------------------------------
    # Claim the domain send boundary.
    #
    # The BackgroundJob itself was already durably claimed by the worker.
    # Here we separately claim the message. Once "dispatching" commits,
    # nobody may invoke the provider for this message again.
    # ------------------------------------------------------------
    try:
        async with AsyncSessionFactory() as session:
            snapshot = await session.scalar(
                select(ConversationMessage).where(
                    ConversationMessage.id == message_id,
                    ConversationMessage.business_id == job.business_id,
                )
            )
            if snapshot is None:
                return ConversationMessageDispatchOutcome(
                    False, "resource_not_found"
                )

            if snapshot.delivery_status in _TERMINAL_DELIVERY_STATUSES:
                return ConversationMessageDispatchOutcome(True)

            if snapshot.delivery_status == "dispatching":
                message = await _lock_message(
                    session,
                    business_id=job.business_id,
                    message_id=message_id,
                )
                if message.delivery_status == "dispatching":
                    _mark_uncertain(
                        session,
                        message=message,
                        reason="dispatch_reentered_after_committed_claim",
                    )
                    await session.commit()
                return ConversationMessageDispatchOutcome(True)

            if snapshot.delivery_status != "queued":
                await _record_definite_failure(
                    business_id=job.business_id,
                    message_id=message_id,
                    reason="invalid_delivery_state",
                )
                return ConversationMessageDispatchOutcome(True)

            # Keep lock ordering canonical: conversation first, then message.
            conversation = await session.scalar(
                select(Conversation)
                .where(
                    Conversation.id == snapshot.conversation_id,
                    Conversation.business_id == job.business_id,
                )
                .with_for_update()
            )
            if conversation is None:
                return ConversationMessageDispatchOutcome(
                    False, "resource_not_found"
                )

            message = await _lock_message(
                session,
                business_id=job.business_id,
                message_id=message_id,
            )

            if message.conversation_id != conversation.id:
                await session.rollback()
                return ConversationMessageDispatchOutcome(
                    False, "invalid_job_state"
                )

            if message.delivery_status in _TERMINAL_DELIVERY_STATUSES:
                await session.commit()
                return ConversationMessageDispatchOutcome(True)

            if message.delivery_status == "dispatching":
                _mark_uncertain(
                    session,
                    message=message,
                    reason="dispatch_reentered_after_committed_claim",
                )
                await session.commit()
                return ConversationMessageDispatchOutcome(True)

            if message.delivery_status != "queued":
                message.delivery_status = "failed"
                _record_failure_audit(
                    session,
                    message=message,
                    reason="invalid_delivery_state",
                )
                await session.commit()
                return ConversationMessageDispatchOutcome(True)

            context = await _prepare_dispatch_context(
                session,
                message=message,
                conversation=conversation,
                adapters=adapters,
                configuration=configuration,
            )

            # This commit is the critical side-effect boundary.
            #
            # A future/replacement worker that sees "dispatching" must never
            # call the provider. It will classify the outcome as uncertain.
            message.delivery_status = "dispatching"
            record_audit(
                session,
                business_id=message.business_id,
                actor_user_id=message.sender_user_id,
                event_type="conversation.manual_outbound_dispatching",
                entity_type="conversation_message",
                entity_id=message.id,
                summary=(
                    "Committed the manual message dispatch boundary before "
                    "provider invocation."
                ),
            )
            await session.commit()

    except (
        ExternalConnectorWritesDisabledError,
        IntegrationError,
        BillingEntitlementError,
    ):
        await _record_definite_failure(
            business_id=job.business_id,
            message_id=message_id,
            reason="dispatch_preflight_rejected",
        )
        return ConversationMessageDispatchOutcome(True)
    except SQLAlchemyError:
        return ConversationMessageDispatchOutcome(
            False, "dependency_unavailable", True
        )
    except Exception:
        # No provider invocation has happened yet. This is therefore still a
        # definite non-send, not an uncertain external outcome.
        await _record_definite_failure(
            business_id=job.business_id,
            message_id=message_id,
            reason="dispatch_preflight_failed",
        )
        return ConversationMessageDispatchOutcome(True)

    # ------------------------------------------------------------
    # No database transaction is open below this point.
    # ------------------------------------------------------------
    try:
        material = await credentials.retrieve(
            context.credential_reference,
            business_id=context.business_id,
            connector_type=context.connector_type,
            purpose="oauth_credentials",
        )
    except IntegrationCredentialUnavailableError:
        await _record_definite_failure(
            business_id=context.business_id,
            message_id=context.message_id,
            reason="credential_unavailable",
        )
        return ConversationMessageDispatchOutcome(True)
    except Exception:
        # Credential lookup is still before the provider mutation.
        await _record_definite_failure(
            business_id=context.business_id,
            message_id=context.message_id,
            reason="credential_lookup_failed",
        )
        return ConversationMessageDispatchOutcome(True)

    adapter = adapters.get(context.connector_type, context.action_type)
    if adapter is None:
        await _record_definite_failure(
            business_id=context.business_id,
            message_id=context.message_id,
            reason="connector_dispatch_not_authorized",
        )
        return ConversationMessageDispatchOutcome(True)

    # ------------------------------------------------------------
    # External provider boundary.
    # ------------------------------------------------------------
    try:
        result = await adapter.execute(
            credentials=material,
            action_type=context.action_type,
            payload=context.payload,
            selected_resources=context.selected_resources,
            delivery_target=context.delivery_target,
            idempotency_key=f"manual-message:{context.message_id}",
        )
    except ConnectorRequestNotSentError:
        await _record_definite_failure(
            business_id=context.business_id,
            message_id=context.message_id,
            reason="connector_request_not_sent",
        )
        return ConversationMessageDispatchOutcome(True)
    except ConnectorRejectedError:
        await _record_definite_failure(
            business_id=context.business_id,
            message_id=context.message_id,
            reason="provider_rejected",
        )
        return ConversationMessageDispatchOutcome(True)
    except Exception:
        # The adapter contract explicitly treats provider 5xx/unavailability
        # after invocation as potentially accepted. Never replay it.
        await _record_uncertain(
            business_id=context.business_id,
            message_id=context.message_id,
            reason="provider_outcome_unknown",
        )
        return ConversationMessageDispatchOutcome(True)

    if (
        not result.succeeded
        or not result.external_reference_id
    ):
        # A malformed/ambiguous response can occur after the mutation reached
        # the provider, therefore it is not safe to retry.
        await _record_uncertain(
            business_id=context.business_id,
            message_id=context.message_id,
            reason=result.failure_code or "provider_outcome_unknown",
        )
        return ConversationMessageDispatchOutcome(True)

    await _record_submitted(
        business_id=context.business_id,
        message_id=context.message_id,
        external_reference=result.external_reference_id,
    )
    return ConversationMessageDispatchOutcome(True)


async def _prepare_dispatch_context(
    session,
    *,
    message: ConversationMessage,
    conversation: Conversation,
    adapters: ConnectorActionAdapterRegistry,
    configuration: Settings,
) -> ConversationMessageDispatchContext:
    business_id = message.business_id

    if (
        message.direction != "outbound"
        or message.sender_type != "user"
        or message.sender_user_id is None
        or message.client_request_id is None
    ):
        raise IntegrationError("manual_message_identity_invalid")

    require_external_connector_writes_enabled(
        configuration.external_connector_writes_enabled
        and configuration.external_connector_write_mode == "enabled"
    )
    await require_feature(session, business_id=business_id, key="integrations")

    if conversation.integration_connection_id is None:
        raise IntegrationError("conversation_connection_unavailable")

    connection = await session.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.id == conversation.integration_connection_id,
            IntegrationConnection.business_id == business_id,
            IntegrationConnection.status == "connected",
            IntegrationConnection.authentication_state == "authorized",
        )
    )
    if connection is None or not connection.credential_reference:
        raise IntegrationError("conversation_connection_unavailable")

    action_type: str
    payload: ActionPayload
    target: str

    if conversation.channel == "facebook":
        await _require_open_customer_service_window(
            session,
            business_id=business_id,
            conversation_id=conversation.id,
            error_code="messenger_customer_service_window_closed",
        )

        identity = await session.scalar(
            select(CustomerChannelIdentity).where(
                CustomerChannelIdentity.id
                == conversation.customer_channel_identity_id,
                CustomerChannelIdentity.business_id == business_id,
                CustomerChannelIdentity.integration_connection_id
                == connection.id,
                CustomerChannelIdentity.external_resource_reference
                == conversation.external_resource_reference,
            )
        )
        if (
            identity is None
            or identity.provider != "facebook"
            or not conversation.external_resource_reference
            or not conversation.external_reference
            or identity.external_user_reference != conversation.external_reference
        ):
            raise IntegrationError("delivery_target_required")

        action_type = "send_customer_message"
        target = identity.external_user_reference
        payload = SendCustomerMessagePayload(
            customer_ref=str(identity.id),
            message=message.content,
            conversation_ref=str(conversation.id),
            channel_resource_ref=conversation.external_resource_reference,
        )

    else:
        customer = await session.scalar(
            select(Customer).where(
                Customer.id == conversation.customer_id,
                Customer.business_id == business_id,
                Customer.status != "archived",
            )
        )
        if customer is None:
            raise IntegrationError("delivery_target_required")

        if conversation.channel == "email" and customer.email:
            action_type = "send_email"
            target = customer.email
            payload = SendEmailPayload(
                recipient_ref=str(customer.id),
                subject="Re: Customer conversation",
                body=message.content,
                conversation_ref=str(conversation.id),
                thread_ref=conversation.external_reference,
            )

        elif conversation.channel == "whatsapp" and customer.phone:
            await _require_open_customer_service_window(
                session,
                business_id=business_id,
                conversation_id=conversation.id,
                error_code="whatsapp_customer_service_window_closed",
            )

            target = "".join(
                character for character in customer.phone if character.isdigit()
            )
            if not target:
                raise IntegrationError("delivery_target_required")

            action_type = "send_whatsapp_message"
            payload = SendWhatsAppMessagePayload(
                customer_ref=str(customer.id),
                message=message.content,
                conversation_ref=str(conversation.id),
            )

        else:
            raise IntegrationError("conversation_channel_unsupported")

    adapter = adapters.get(connection.connector_type, action_type)
    definition = require_connector(connection.connector_type)
    capability = (
        "future_send_email"
        if action_type == "send_email"
        else "future_send_messages"
    )
    if (
        adapter is None
        or capability not in definition.future_write_capabilities
    ):
        raise IntegrationError("connector_dispatch_not_authorized")

    selected_resources = tuple(
        {
            key: value
            for key, value in item.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        for item in connection.selected_resources[:20]
    )

    return ConversationMessageDispatchContext(
        business_id=business_id,
        message_id=message.id,
        conversation_id=conversation.id,
        connection_id=connection.id,
        connector_type=connection.connector_type,
        credential_reference=connection.credential_reference,
        action_type=action_type,
        payload=payload,
        selected_resources=selected_resources,
        delivery_target=target,
    )


async def _require_open_customer_service_window(
    session,
    *,
    business_id: UUID,
    conversation_id: UUID,
    error_code: str,
) -> None:
    latest_inbound = await session.scalar(
        select(ConversationMessage.sent_at)
        .where(
            ConversationMessage.business_id == business_id,
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.direction == "inbound",
            ConversationMessage.sender_type == "customer",
        )
        .order_by(
            ConversationMessage.sent_at.desc(),
            ConversationMessage.id.desc(),
        )
        .limit(1)
    )

    if (
        latest_inbound is None
        or latest_inbound < datetime.now(UTC) - timedelta(hours=24)
    ):
        raise IntegrationError(error_code)


async def _lock_message(
    session,
    *,
    business_id: UUID,
    message_id: UUID,
) -> ConversationMessage:
    message = await session.scalar(
        select(ConversationMessage)
        .where(
            ConversationMessage.id == message_id,
            ConversationMessage.business_id == business_id,
        )
        .with_for_update()
    )
    if message is None:
        raise IntegrationError("conversation_message_not_found")
    return message


async def _record_submitted(
    *,
    business_id: UUID,
    message_id: UUID,
    external_reference: str,
) -> None:
    async with AsyncSessionFactory() as session:
        message = await _lock_message(
            session,
            business_id=business_id,
            message_id=message_id,
        )

        provider_reference = external_reference[:255]

        # Delivery/read webhooks can race the provider-success persistence.
        # Watermark callbacks do not require the provider message reference and
        # may therefore advance dispatching -> sent/delivered/read first.
        #
        # Always retain the definitive provider reference, but never regress a
        # stronger delivery state back to submitted.
        if (
            message.external_reference is not None
            and message.external_reference != provider_reference
        ):
            _mark_uncertain(
                session,
                message=message,
                reason="provider_reference_conflict",
            )
            await session.commit()
            return

        if message.delivery_status not in {
            "dispatching",
            "uncertain",
            "submitted",
            "sent",
            "delivered",
            "read",
            "failed",
        }:
            _mark_uncertain(
                session,
                message=message,
                reason="provider_success_with_invalid_local_state",
            )
            await session.commit()
            return

        message.external_reference = provider_reference

        # A definitive provider acceptance is stronger than our conservative
        # uncertain state, but it must not overwrite a later webhook state.
        if message.delivery_status in {"dispatching", "uncertain"}:
            message.delivery_status = "submitted"

        record_automation_event(
            session,
            business_id=business_id,
            event_type="outbound_message_recorded",
            entity_type="conversation_message",
            entity_id=message.id,
            payload={
                "delivery_status": message.delivery_status,
                "sender_type": "user",
            },
        )
        record_audit(
            session,
            business_id=business_id,
            actor_user_id=message.sender_user_id,
            event_type="conversation.manual_outbound_message_submitted",
            entity_type="conversation_message",
            entity_id=message.id,
            summary=(
                "Provider accepted the durable human-authorized customer "
                "message."
            ),
        )
        await session.commit()


async def _record_definite_failure(
    *,
    business_id: UUID,
    message_id: UUID,
    reason: str,
) -> None:
    async with AsyncSessionFactory() as session:
        message = await session.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.id == message_id,
                ConversationMessage.business_id == business_id,
            )
            .with_for_update()
        )
        if message is None:
            await session.rollback()
            return

        if message.delivery_status in {
            "submitted",
            "sent",
            "delivered",
            "read",
        }:
            await session.commit()
            return

        message.delivery_status = "failed"
        _record_failure_audit(session, message=message, reason=reason)
        await session.commit()


async def _record_uncertain(
    *,
    business_id: UUID,
    message_id: UUID,
    reason: str,
) -> None:
    async with AsyncSessionFactory() as session:
        message = await session.scalar(
            select(ConversationMessage)
            .where(
                ConversationMessage.id == message_id,
                ConversationMessage.business_id == business_id,
            )
            .with_for_update()
        )
        if message is None:
            await session.rollback()
            return

        if message.delivery_status in {
            "submitted",
            "sent",
            "delivered",
            "read",
        }:
            await session.commit()
            return

        _mark_uncertain(session, message=message, reason=reason)
        await session.commit()


def _mark_uncertain(
    session,
    *,
    message: ConversationMessage,
    reason: str,
) -> None:
    message.delivery_status = "uncertain"
    record_audit(
        session,
        business_id=message.business_id,
        actor_user_id=message.sender_user_id,
        event_type="conversation.manual_outbound_message_uncertain",
        entity_type="conversation_message",
        entity_id=message.id,
        summary=(
            "The external delivery outcome is uncertain; the message will not "
            f"be automatically replayed. Reason: {reason[:120]}."
        ),
    )


def _record_failure_audit(
    session,
    *,
    message: ConversationMessage,
    reason: str,
) -> None:
    record_audit(
        session,
        business_id=message.business_id,
        actor_user_id=message.sender_user_id,
        event_type="conversation.manual_outbound_message_failed",
        entity_type="conversation_message",
        entity_id=message.id,
        summary=(
            "The durable human-authorized customer message was not sent. "
            f"Reason: {reason[:120]}."
        ),
    )
