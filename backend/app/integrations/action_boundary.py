from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.integrations import require_external_connector_writes_enabled
from app.core.config import Settings, settings
from app.exceptions.integration import IntegrationNotFoundError, IntegrationStateError
from app.models.action_execution_attempt import ActionExecutionAttempt
from app.models.ai_action import AIAction
from app.models.approval_request import ApprovalRequest
from app.models.integration import IntegrationConnection
from app.models.customer import Customer
from app.integrations.action_adapters import (
    ConnectorActionAdapterRegistry,
    connector_action_adapters,
)
from app.integrations.registry import require_connector
from app.schemas.ai_action_payload import ActionPayloadType
from app.services.action_execution_attempt import (
    revalidate_action_execution_attempt_for_dispatch,
)


CONNECTOR_ACTION_TYPES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType({
    "send_email": ("gmail", "microsoft_outlook"),
    "send_whatsapp_message": ("whatsapp_business",),
    "send_customer_message": ("whatsapp_business", "gmail", "microsoft_outlook"),
    "publish_social_post": ("facebook", "instagram"),
    "create_meta_campaign": ("meta_ads",),
    "launch_meta_campaign": ("meta_ads",),
    "create_google_ads_campaign": ("google_ads",),
    "launch_google_ads_campaign": ("google_ads",),
    "change_ad_budget": ("google_ads", "meta_ads"),
    "pause_ad_campaign": ("google_ads", "meta_ads"),
})

CONNECTOR_WRITE_CAPABILITIES: Final[Mapping[str, str]] = MappingProxyType({
    "send_email": "future_send_email",
    "send_whatsapp_message": "future_send_messages",
    "send_customer_message": "future_send_messages",
    "publish_social_post": "future_publish_content",
    "create_meta_campaign": "future_create_campaign",
    "launch_meta_campaign": "future_launch_campaign",
    "create_google_ads_campaign": "future_create_campaign",
    "launch_google_ads_campaign": "future_launch_campaign",
    "change_ad_budget": "future_change_budget",
    "pause_ad_campaign": "future_change_budget",
})


@dataclass(frozen=True, slots=True)
class ConnectorDispatchContext:
    business_id: UUID
    action_id: UUID
    approval_id: UUID
    attempt_id: UUID
    connection_id: UUID
    action_type: str
    connector_type: str
    idempotency_key: str
    credential_reference: str
    selected_resources: tuple[Mapping[str, str], ...]
    payload: ActionPayloadType
    delivery_target: str | None


async def prepare_connector_dispatch_context(
    session: AsyncSession,
    *,
    business_id: UUID,
    attempt_id: UUID,
    connection_id: UUID | None = None,
    adapters: ConnectorActionAdapterRegistry = connector_action_adapters,
    configuration: Settings = settings,
) -> ConnectorDispatchContext:
    """Resolve a tenant-owned, provider-capable dispatch using database truth."""
    require_external_connector_writes_enabled(
        configuration.external_connector_writes_enabled
    )

    attempt, action, _action_definition, payload = (
        await revalidate_action_execution_attempt_for_dispatch(
            session, business_id=business_id, attempt_id=attempt_id
        )
    )
    allowed_connectors = CONNECTOR_ACTION_TYPES.get(attempt.action_type, ())
    capability = CONNECTOR_WRITE_CAPABILITIES.get(attempt.action_type)
    if not allowed_connectors or capability is None:
        raise IntegrationStateError("connector_dispatch_not_supported")
    statement = select(IntegrationConnection).where(
        IntegrationConnection.business_id == business_id,
        IntegrationConnection.connector_type.in_(allowed_connectors),
        IntegrationConnection.status == "connected",
        IntegrationConnection.authentication_state == "authorized",
    )
    if connection_id is not None:
        statement = statement.where(IntegrationConnection.id == connection_id)
    statement = statement.order_by(
        IntegrationConnection.connector_type.asc(), IntegrationConnection.id.asc()
    ).limit(1)
    connection = await session.scalar(statement)
    if connection is None:
        raise IntegrationNotFoundError("connector_dispatch_resource_not_found")
    approval = await session.scalar(select(ApprovalRequest).where(
        ApprovalRequest.business_id == business_id,
        ApprovalRequest.action_id == attempt.action_id,
        ApprovalRequest.status == "approved",
    ))
    connector_definition = require_connector(connection.connector_type)
    if (
        approval is None
        or action.action_type != attempt.action_type
        or connection.connector_type not in allowed_connectors
        or capability not in connector_definition.future_write_capabilities
        or not connection.credential_reference
        or not adapters.supports(connection.connector_type, attempt.action_type)
    ):
        raise IntegrationStateError("connector_dispatch_not_authorized")
    if connector_definition.resource_selection_required and not connection.selected_resources:
        raise IntegrationStateError("connector_resource_selection_required")
    selected_resources: tuple[Mapping[str, str], ...] = tuple(
        {
            key: value
            for key, value in resource.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        for resource in connection.selected_resources[:20]
    )
    delivery_target = await _resolve_delivery_target(
        session,
        business_id=business_id,
        connector_type=connection.connector_type,
        action_type=attempt.action_type,
        payload=payload,
    )
    return ConnectorDispatchContext(
        business_id=business_id,
        action_id=action.id,
        approval_id=approval.id,
        attempt_id=attempt.id,
        connection_id=connection.id,
        action_type=action.action_type,
        connector_type=connection.connector_type,
        idempotency_key=attempt.idempotency_key,
        credential_reference=connection.credential_reference,
        selected_resources=selected_resources,
        payload=payload,
        delivery_target=delivery_target,
    )


async def _resolve_delivery_target(
    session: AsyncSession,
    *,
    business_id: UUID,
    connector_type: str,
    action_type: str,
    payload: ActionPayloadType,
) -> str | None:
    if action_type not in {"send_email", "send_whatsapp_message", "send_customer_message"}:
        return None
    raw_reference = getattr(payload, "recipient_ref", None) or getattr(
        payload, "customer_ref", None
    )
    if not isinstance(raw_reference, str):
        raise IntegrationStateError("delivery_target_required")
    customer = None
    try:
        customer_id = UUID(raw_reference)
    except ValueError:
        customer_id = None
    if customer_id is not None:
        customer = await session.scalar(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.business_id == business_id,
                Customer.status != "archived",
            )
        )
    if connector_type in {"gmail", "microsoft_outlook"}:
        value = customer.email if customer is not None else raw_reference
        if not isinstance(value, str) or "@" not in value or len(value) > 320:
            raise IntegrationStateError("delivery_target_required")
        return value
    value = customer.phone if customer is not None else raw_reference
    if not isinstance(value, str):
        raise IntegrationStateError("delivery_target_required")
    normalized = "".join(character for character in value if character.isdigit())
    if not 7 <= len(normalized) <= 15:
        raise IntegrationStateError("delivery_target_required")
    return normalized
