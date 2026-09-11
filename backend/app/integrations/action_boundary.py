from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
from urllib.parse import urlsplit
from types import MappingProxyType
from typing import Final, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.integrations import require_external_connector_writes_enabled
from app.core.config import Settings, settings
from app.exceptions.integration import IntegrationNotFoundError, IntegrationStateError
from app.models.approval_request import ApprovalRequest
from app.models.integration import IntegrationConnection
from app.models.customer import Customer
from app.models.conversation import Conversation, ConversationMessage
from app.models.automation_intelligence import MarketingActionProposal
from app.models.business_branding import BusinessBranding
from app.models.marketing import Campaign, CreativeAsset, MarketingContent
from app.integrations.action_adapters import (
    ConnectorActionAdapterRegistry,
    connector_action_adapters,
)
from app.integrations.registry import require_connector
from app.schemas.ai_action_payload import (
    ActionPayloadType,
    CreateGoogleAdsCampaignPayload,
    CreateMetaCampaignPayload,
    PublishSocialPostPayload,
)
from app.storage.base import ObjectStorage, StorageError
from app.storage.factory import get_object_storage
from app.exceptions.logo import LogoTooLargeError, LogoValidationError
from app.services.business_branding import (
    business_logo_reference,
    validated_business_logo_key,
)
from app.services.logo_image import (
    GOOGLE_ADS_IMAGE_MAX_BYTES,
    google_ads_square_logo_bytes,
)
from app.services.action_execution_attempt import (
    revalidate_action_execution_attempt_for_dispatch,
)
from app.services.billing import require_feature


CONNECTOR_ACTION_TYPES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType({
    "send_email": ("gmail", "microsoft_outlook"),
    "send_whatsapp_message": ("whatsapp_business",),
    "send_customer_message": (
        "whatsapp_business", "gmail", "microsoft_outlook", "facebook",
    ),
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


class _MaterializedMetaCampaignPayload(CreateMetaCampaignPayload):
    """Dispatch-only Meta payload carrying trusted short-lived media URLs."""

    campaign_media_urls: tuple[str, ...]


class _MaterializedGoogleAdsCampaignPayload(CreateGoogleAdsCampaignPayload):
    """
    Dispatch-only Google payload carrying private trusted bytes.

    These fields never exist in the persisted AIAction, approval snapshot,
    authorization hash, audit metadata, or provider-safe metadata.
    """

    campaign_media_bytes: tuple[bytes, bytes]
    campaign_logo_bytes: bytes


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
    storage: ObjectStorage | None = None,
) -> ConnectorDispatchContext:
    """Resolve a tenant-owned, provider-capable dispatch using database truth."""
    require_external_connector_writes_enabled(
        configuration.external_connector_writes_enabled
        and configuration.external_connector_write_mode == "enabled"
    )
    # A connected account does not outlive the business's platform
    # entitlement. Recheck the current plan before loading connector secrets.
    await require_feature(session, business_id=business_id, key="integrations")

    attempt, action, _action_definition, payload = (
        await revalidate_action_execution_attempt_for_dispatch(
            session, business_id=business_id, attempt_id=attempt_id
        )
    )
    allowed_connectors = CONNECTOR_ACTION_TYPES.get(attempt.action_type, ())
    capability = CONNECTOR_WRITE_CAPABILITIES.get(attempt.action_type)
    if not allowed_connectors or capability is None:
        raise IntegrationStateError("connector_dispatch_not_supported")
    bound_connection_id = await _conversation_connection_binding(
        session,
        business_id=business_id,
        action_type=attempt.action_type,
        payload=payload,
    )
    if connection_id is not None and bound_connection_id not in {None, connection_id}:
        raise IntegrationStateError("conversation_connection_conflict")
    effective_connection_id = bound_connection_id or connection_id
    statement = select(IntegrationConnection).where(
        IntegrationConnection.business_id == business_id,
        IntegrationConnection.connector_type.in_(allowed_connectors),
        IntegrationConnection.status == "connected",
        IntegrationConnection.authentication_state == "authorized",
    )
    if effective_connection_id is not None:
        statement = statement.where(IntegrationConnection.id == effective_connection_id)
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
        ApprovalRequest.reason_code == action.policy_reason_code,
        ApprovalRequest.action_type_snapshot == action.action_type,
        ApprovalRequest.authorized_payload_hash_snapshot
        == action.authorized_payload_hash,
    ))
    connector_definition = require_connector(connection.connector_type)
    if (
        approval is None
        or action.action_type != attempt.action_type
        or connection.connector_type not in allowed_connectors
        or capability not in connector_definition.future_write_capabilities
        or not connection.credential_reference
        or (
            attempt.action_type == "publish_social_post"
            and not set(connector_definition.oauth_write_scopes).issubset(
                set(connection.scopes_granted or [])
            )
        )
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
    dispatch_payload = payload
    if attempt.action_type == "publish_social_post":
        dispatch_payload = await _materialize_publish_media_payload(
            session,
            business_id=business_id,
            action_id=action.id,
            payload=payload,
            storage=storage,
        )
    elif attempt.action_type in {
        "create_meta_campaign",
        "create_google_ads_campaign",
    }:
        dispatch_payload = await _materialize_campaign_media_payload(
            session,
            business_id=business_id,
            action_id=action.id,
            action_type=attempt.action_type,
            payload=payload,
            storage=storage,
        )

    delivery_target = await _resolve_delivery_target(
        session,
        business_id=business_id,
        connector_type=connection.connector_type,
        connection_id=connection.id,
        action_type=attempt.action_type,
        payload=dispatch_payload,
    )
    if attempt.action_type in {"create_google_ads_campaign", "create_meta_campaign"}:
        proposal = await session.scalar(select(MarketingActionProposal).where(
            MarketingActionProposal.business_id == business_id,
            MarketingActionProposal.ai_action_id == action.id,
            MarketingActionProposal.entity_type == "campaign",
        ))
        if proposal is None:
            raise IntegrationStateError("campaign_proposal_link_required")
        campaign = await session.scalar(select(Campaign).where(
            Campaign.business_id == business_id,
            Campaign.id == proposal.entity_id,
        ).with_for_update())
        if campaign is None:
            raise IntegrationStateError("campaign_not_found")
        campaign.status = "executing"
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
        payload=dispatch_payload,
        delivery_target=delivery_target,
    )


_PUBLISH_MEDIA_HANDLE_PREFIX = "creative_asset:"
_PUBLISH_MEDIA_TTL_SECONDS = 3600
_CREATE_PUBLISH_PACKAGE_KEY = re.compile(
    r"^create-publish:([0-9a-f-]{36}):"
    r"(instagram|facebook|linkedin|tiktok|youtube)(?::v\\d+)?$",
    flags=re.IGNORECASE,
)


async def _materialize_campaign_media_payload(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_id: UUID,
    action_type: str,
    payload: ActionPayloadType,
    storage: ObjectStorage | None,
) -> ActionPayloadType:
    """
    Resolve an approved creative_asset:<uuid> into transient provider media URLs.

    The persisted AIAction, approval snapshot and authorized payload hash continue
    to contain only the stable creative_asset:<uuid> identity.
    """
    if not isinstance(
        payload,
        (CreateMetaCampaignPayload, CreateGoogleAdsCampaignPayload),
    ):
        raise IntegrationStateError("campaign_payload_invalid")

    refs = payload.creative.creative_refs

    # Existing manual/commerce campaigns without Create & Publish media keep
    # their established provider path.
    if not any(ref.startswith(_PUBLISH_MEDIA_HANDLE_PREFIX) for ref in refs):
        return payload

    if (
        len(refs) != 1
        or not refs[0].startswith(_PUBLISH_MEDIA_HANDLE_PREFIX)
    ):
        raise IntegrationStateError("campaign_media_contract_invalid")

    try:
        asset_id = UUID(
            refs[0].removeprefix(_PUBLISH_MEDIA_HANDLE_PREFIX)
        )
    except ValueError:
        raise IntegrationStateError("campaign_media_handle_invalid") from None

    proposal = await session.scalar(
        select(MarketingActionProposal).where(
            MarketingActionProposal.business_id == business_id,
            MarketingActionProposal.ai_action_id == action_id,
            MarketingActionProposal.entity_type == "campaign",
        )
    )
    if proposal is None:
        raise IntegrationStateError("campaign_proposal_link_required")

    asset = await session.scalar(
        select(CreativeAsset).where(
            CreativeAsset.business_id == business_id,
            CreativeAsset.id == asset_id,
            CreativeAsset.campaign_id == proposal.entity_id,
            CreativeAsset.source_type == "import",
            CreativeAsset.generation_status == "ready",
            CreativeAsset.storage_reference.is_not(None),
        )
    )
    if (
        asset is None
        or asset.business_id != business_id
        or asset.campaign_id != proposal.entity_id
        or asset.source_type != "import"
        or asset.generation_status != "ready"
        or not asset.storage_reference
    ):
        raise IntegrationStateError("campaign_media_asset_invalid")

    # Video provider execution gets its own verified transcoding/upload path.
    # Never pretend an arbitrary uploaded MP4/WebM is ad-ready.
    if asset.media_type != "image":
        raise IntegrationStateError(
            "campaign_video_media_processing_required"
        )

    metadata = asset.creative_metadata or {}
    variants = metadata.get("variants")
    if not isinstance(variants, dict):
        raise IntegrationStateError("campaign_media_variants_required")

    required_variant_names = (
        ("square_1_1", "portrait_4_5", "vertical_9_16")
        if action_type == "create_meta_campaign"
        else ("square_1_1", "landscape_1_91_1")
    )

    object_storage = storage or get_object_storage()
    signed_urls: list[str] = []
    google_media_bytes: list[bytes] = []

    for variant_name in required_variant_names:
        variant = variants.get(variant_name)
        if not isinstance(variant, dict):
            raise IntegrationStateError(
                f"campaign_media_variant_missing:{variant_name}"
            )

        reference = variant.get("storage_reference")
        if not isinstance(reference, str) or not reference:
            raise IntegrationStateError(
                "campaign_media_variant_reference_invalid"
            )

        try:
            object_key = object_storage.object_key_from_reference(
                reference
            )
        except (StorageError, ValueError):
            raise IntegrationStateError(
                "campaign_media_variant_reference_invalid"
            ) from None

        expected_key = (
            f"businesses/{business_id}/marketing/uploads/{asset.id}/"
            f"variants/{variant_name}.jpg"
        )
        if object_key != expected_key:
            raise IntegrationStateError(
                "campaign_media_variant_reference_invalid"
            )

        if action_type == "create_google_ads_campaign":
            try:
                content = await object_storage.get(
                    object_key,
                    max_bytes=GOOGLE_ADS_IMAGE_MAX_BYTES,
                )
            except (StorageError, ValueError):
                raise IntegrationStateError(
                    "campaign_media_unavailable"
                ) from None

            if (
                not content
                or len(content)
                > GOOGLE_ADS_IMAGE_MAX_BYTES
            ):
                raise IntegrationStateError(
                    "google_campaign_image_invalid"
                )

            google_media_bytes.append(content)
            continue

        try:
            signed_url = object_storage.presentation_url(
                object_key,
                expires_in_seconds=_PUBLISH_MEDIA_TTL_SECONDS,
            )
        except (StorageError, ValueError):
            raise IntegrationStateError(
                "campaign_media_unavailable"
            ) from None

        if not _safe_dispatch_media_url(signed_url):
            raise IntegrationStateError(
                "campaign_media_unavailable"
            )

        signed_urls.append(signed_url)

    if action_type == "create_meta_campaign":
        if not isinstance(
            payload,
            CreateMetaCampaignPayload,
        ):
            raise IntegrationStateError(
                "campaign_payload_invalid"
            )
        return _MaterializedMetaCampaignPayload(
            **payload.model_dump(mode="python"),
            campaign_media_urls=tuple(signed_urls),
        )

    if action_type == "create_google_ads_campaign":
        if not isinstance(
            payload,
            CreateGoogleAdsCampaignPayload,
        ):
            raise IntegrationStateError(
                "campaign_payload_invalid"
            )

        if (
            payload.network != "performance_max"
            or payload.merchant_account_ref is not None
            or len(google_media_bytes) != 2
        ):
            raise IntegrationStateError(
                "google_campaign_media_contract_invalid"
            )

        branding = await session.scalar(
            select(BusinessBranding).where(
                BusinessBranding.business_id
                == business_id
            )
        )

        expected_logo_ref = business_logo_reference(
            branding,
            business_id=business_id,
        )
        logo_key = validated_business_logo_key(
            branding,
            business_id=business_id,
        )

        if (
            expected_logo_ref is None
            or logo_key is None
            or payload.business_logo_ref
            != expected_logo_ref
        ):
            raise IntegrationStateError(
                "google_campaign_logo_reapproval_required"
            )

        try:
            raw_logo = await object_storage.get(
                logo_key,
                max_bytes=GOOGLE_ADS_IMAGE_MAX_BYTES,
            )
            logo_bytes = google_ads_square_logo_bytes(
                raw_logo
            )
        except (
            StorageError,
            ValueError,
            LogoTooLargeError,
            LogoValidationError,
        ):
            raise IntegrationStateError(
                "google_campaign_logo_invalid"
            ) from None

        if (
            not logo_bytes
            or len(logo_bytes)
            > GOOGLE_ADS_IMAGE_MAX_BYTES
        ):
            raise IntegrationStateError(
                "google_campaign_logo_invalid"
            )

        return _MaterializedGoogleAdsCampaignPayload(
            **payload.model_dump(mode="python"),
            campaign_media_bytes=(
                google_media_bytes[0],
                google_media_bytes[1],
            ),
            campaign_logo_bytes=logo_bytes,
        )

    raise IntegrationStateError(
        "campaign_action_type_invalid"
    )


async def _materialize_publish_media_payload(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_id: UUID,
    payload: ActionPayloadType,
    storage: ObjectStorage | None,
) -> ActionPayloadType:
    """Turn an approved stable media handle into a transient signed provider URL."""
    if not isinstance(payload, PublishSocialPostPayload):
        raise IntegrationStateError("publish_payload_invalid")

    if not payload.media_refs:
        return payload

    if len(payload.media_refs) != 1 or payload.media_type not in {"image", "video"}:
        raise IntegrationStateError("publish_media_contract_invalid")

    handle = payload.media_refs[0]
    if not handle.startswith(_PUBLISH_MEDIA_HANDLE_PREFIX):
        raise IntegrationStateError("publish_media_handle_invalid")

    try:
        asset_id = UUID(handle.removeprefix(_PUBLISH_MEDIA_HANDLE_PREFIX))
    except ValueError:
        raise IntegrationStateError("publish_media_handle_invalid") from None

    proposal = await session.scalar(
        select(MarketingActionProposal).where(
            MarketingActionProposal.business_id == business_id,
            MarketingActionProposal.ai_action_id == action_id,
            MarketingActionProposal.entity_type == "content",
        )
    )
    if proposal is None or proposal.channel != payload.platform:
        raise IntegrationStateError("publish_media_proposal_invalid")

    content = await session.scalar(
        select(MarketingContent).where(
            MarketingContent.business_id == business_id,
            MarketingContent.id == proposal.entity_id,
        )
    )
    if content is None:
        raise IntegrationStateError("publish_content_not_found")

    asset = await session.scalar(
        select(CreativeAsset).where(
            CreativeAsset.business_id == business_id,
            CreativeAsset.id == asset_id,
            CreativeAsset.generation_status == "ready",
            CreativeAsset.source_type == "import",
            CreativeAsset.storage_reference.is_not(None),
        )
    )
    if (
        asset is None
        or asset.business_id != business_id
        or asset.media_type != payload.media_type
        or asset.content_id is None
    ):
        raise IntegrationStateError("publish_media_asset_invalid")

    if not await _publish_asset_belongs_to_content(
        session,
        business_id=business_id,
        content=content,
        asset=asset,
    ):
        raise IntegrationStateError("publish_media_content_conflict")

    durable_reference = asset.storage_reference
    if not isinstance(durable_reference, str) or not durable_reference:
        raise IntegrationStateError("publish_media_reference_invalid")

    object_storage = storage or get_object_storage()
    try:
        object_key = object_storage.object_key_from_reference(durable_reference)
    except (StorageError, ValueError):
        raise IntegrationStateError("publish_media_reference_invalid") from None

    if not _trusted_publish_media_object_key(
        business_id=business_id,
        asset=asset,
        object_key=object_key,
    ):
        raise IntegrationStateError("publish_media_reference_invalid")

    try:
        signed_url = object_storage.presentation_url(
            object_key,
            expires_in_seconds=_PUBLISH_MEDIA_TTL_SECONDS,
        )
    except (StorageError, ValueError):
        raise IntegrationStateError("publish_media_unavailable") from None

    if not _safe_dispatch_media_url(signed_url):
        raise IntegrationStateError("publish_media_unavailable")

    # Important: this object is transient. The persisted AIAction and its
    # approval/hash continue to contain only creative_asset:<uuid>.
    return PublishSocialPostPayload(
        platform=payload.platform,
        content=payload.content,
        media_refs=[signed_url],
        media_type=payload.media_type,
    )


async def _publish_asset_belongs_to_content(
    session: AsyncSession,
    *,
    business_id: UUID,
    content: MarketingContent,
    asset: CreativeAsset,
) -> bool:
    if asset.content_id == content.id:
        return True

    package_match = _CREATE_PUBLISH_PACKAGE_KEY.fullmatch(
        content.proposal_key or ""
    )
    if package_match is not None:
        package_prefix = f"create-publish:{package_match.group(1)}:"
        sibling_id = await session.scalar(
            select(MarketingContent.id).where(
                MarketingContent.business_id == business_id,
                MarketingContent.id == asset.content_id,
                MarketingContent.proposal_key.startswith(package_prefix),
            )
        )
        return sibling_id == asset.content_id

    if content.root_content_id is None:
        return False

    lineage_id = await session.scalar(
        select(MarketingContent.id).where(
            MarketingContent.business_id == business_id,
            MarketingContent.id == asset.content_id,
            MarketingContent.root_content_id == content.root_content_id,
        )
    )
    return lineage_id == asset.content_id


def _trusted_publish_media_object_key(
    *,
    business_id: UUID,
    asset: CreativeAsset,
    object_key: str,
) -> bool:
    if asset.source_type == "import":
        prefix = (
            f"businesses/{business_id}/marketing/uploads/{asset.id}/"
        )
        if not object_key.startswith(prefix):
            return False
        leaf = object_key.removeprefix(prefix)
        return (
            leaf.startswith("source.")
            and "/" not in leaf
            and 7 <= len(leaf) <= 32
        )

    # Generated image finals are deterministic private PNGs. Generated-video
    # provider storage has a different lifecycle and is deliberately not
    # guessed here.
    if asset.source_type == "future_provider" and asset.media_type == "image":
        prefix = (
            f"businesses/{business_id}/marketing/creatives/"
            f"{asset.id}/final/"
        )
        if not object_key.startswith(prefix):
            return False
        leaf = object_key.removeprefix(prefix)
        return re.fullmatch(r"generation-[1-9]\\d*\\.png", leaf) is not None

    return False


def _safe_dispatch_media_url(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


async def _resolve_delivery_target(
    session: AsyncSession,
    *,
    business_id: UUID,
    connector_type: str,
    connection_id: UUID,
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
    if connector_type != "facebook":
        try:
            customer_id = UUID(raw_reference)
        except ValueError:
            raise IntegrationStateError("delivery_customer_reference_invalid") from None
        customer = await session.scalar(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.business_id == business_id,
                Customer.status != "archived",
            )
        )
        if customer is None or customer.business_id != business_id:
            raise IntegrationStateError("delivery_customer_not_found")
    conversation_ref = getattr(payload, "conversation_ref", None)
    if connector_type == "whatsapp_business" and conversation_ref is None:
        # Free-form WhatsApp sends are safe only inside a tenant-owned
        # conversation whose latest customer message proves an open service
        # window. Proactive/template and consent semantics are not modeled yet.
        raise IntegrationStateError("whatsapp_conversation_required")
    conversation = None
    if conversation_ref is not None:
        try:
            conversation_id = UUID(conversation_ref)
        except ValueError:
            raise IntegrationStateError("conversation_reference_invalid") from None
        conditions = [
            Conversation.id == conversation_id,
            Conversation.business_id == business_id,
            Conversation.integration_connection_id == connection_id,
        ]
        if customer is not None:
            conditions.append(Conversation.customer_id == customer.id)
        conversation = await session.scalar(select(Conversation).where(*conditions))
        if conversation is None:
            raise IntegrationStateError("conversation_delivery_target_invalid")
    if connector_type == "facebook":
        if conversation is None or not conversation.external_reference:
            raise IntegrationStateError("delivery_target_required")
        if getattr(payload, "channel_resource_ref", None) != conversation.external_resource_reference:
            raise IntegrationStateError("conversation_resource_conflict")
        return conversation.external_reference
    if customer is None:
        raise IntegrationStateError("delivery_customer_not_found")
    if connector_type in {"gmail", "microsoft_outlook"}:
        value = customer.email
        if not isinstance(value, str) or "@" not in value or len(value) > 320:
            raise IntegrationStateError("delivery_target_required")
        return value
    value = customer.phone
    if not isinstance(value, str):
        raise IntegrationStateError("delivery_target_required")
    normalized = "".join(character for character in value if character.isdigit())
    if not 7 <= len(normalized) <= 15:
        raise IntegrationStateError("delivery_target_required")
    return normalized


async def _conversation_connection_binding(
    session: AsyncSession,
    *,
    business_id: UUID,
    action_type: str,
    payload: ActionPayloadType,
) -> UUID | None:
    if action_type not in {"send_email", "send_whatsapp_message", "send_customer_message"}:
        return None
    reference = getattr(payload, "conversation_ref", None)
    if reference is None:
        # Preserve governed legacy communication proposals that predate the
        # unified-conversation binding. Customer Agent always supplies it.
        return None
    try:
        conversation_id = UUID(reference)
    except ValueError:
        raise IntegrationStateError("conversation_reference_invalid") from None
    # This row lock is the final synchronization gate between an AI reply
    # and human conversation controls such as Take Over / Pause / Escalate.
    #
    # control_conversation() locks the same tenant-owned Conversation row.
    # Whichever transaction acquires and commits the lock first establishes
    # the authoritative handling state for this dispatch. The lock is released
    # when the dispatch-preflight transaction commits, before any credential
    # retrieval or provider network request occurs.
    conversation = await session.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.business_id == business_id,
        )
        .with_for_update()
    )
    if conversation is None or conversation.integration_connection_id is None:
        raise IntegrationStateError("conversation_connection_required")
    if (getattr(conversation, "handling_state", None) or "ai_active") != "ai_active":
        raise IntegrationStateError("conversation_ai_handling_inactive")
    raw_customer = getattr(payload, "recipient_ref", None) or getattr(payload, "customer_ref", None)
    expected_channels = {
        "send_email": {"email"},
        "send_whatsapp_message": {"whatsapp"},
        "send_customer_message": {"email", "whatsapp", "facebook"},
    }[action_type]
    if conversation.channel not in expected_channels:
        raise IntegrationStateError("conversation_channel_conflict")
    if conversation.channel == "facebook":
        if raw_customer != str(conversation.customer_channel_identity_id):
            raise IntegrationStateError("conversation_customer_conflict")
        if getattr(payload, "channel_resource_ref", None) != conversation.external_resource_reference:
            raise IntegrationStateError("conversation_resource_conflict")
    elif conversation.customer_id is None or raw_customer != str(conversation.customer_id):
        raise IntegrationStateError("conversation_customer_conflict")
    if conversation.channel in {"whatsapp", "facebook"}:
        latest_inbound = await session.scalar(
            select(ConversationMessage.sent_at)
            .where(
                ConversationMessage.business_id == business_id,
                ConversationMessage.conversation_id == conversation.id,
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
            failure_code = (
                "whatsapp_customer_service_window_closed"
                if conversation.channel == "whatsapp"
                else "messenger_customer_service_window_closed"
            )
            raise IntegrationStateError(failure_code)
    return conversation.integration_connection_id
