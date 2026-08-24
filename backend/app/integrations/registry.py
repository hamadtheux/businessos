from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, Mapping

from app.domain.integrations import ConnectorType
from app.exceptions.integration import IntegrationValidationError
from app.integrations.providers import OAUTH_PROVIDER_ENDPOINTS


ConnectorCategory = Literal[
    "communication", "productivity", "calendar", "advertising", "social"
]
OAuthProvider = Literal["google", "meta", "microsoft"]


@dataclass(frozen=True, slots=True)
class ConnectorDefinition:
    connector_type: ConnectorType
    display_name: str
    description: str
    category: ConnectorCategory
    authentication_type: Literal["oauth2"]
    oauth_provider: OAuthProvider
    capabilities: tuple[str, ...]
    read_capabilities: tuple[str, ...]
    future_write_capabilities: tuple[str, ...]
    oauth_scopes: tuple[str, ...]
    webhook_support: bool
    external_writes_enabled: Literal[False]
    resource_types: tuple[str, ...]
    required_configuration_fields: tuple[str, ...]
    resource_selection_required: bool
    trusted_authorization_hosts: tuple[str, ...]
    foundation_only: bool = False


def _connector(
    connector_type: ConnectorType,
    display_name: str,
    description: str,
    category: ConnectorCategory,
    oauth_provider: OAuthProvider,
    *,
    reads: tuple[str, ...],
    future_writes: tuple[str, ...],
    scopes: tuple[str, ...],
    resources: tuple[str, ...] = (),
    webhook: bool = False,
    foundation_only: bool = False,
) -> ConnectorDefinition:
    return ConnectorDefinition(
        connector_type=connector_type,
        display_name=display_name,
        description=description,
        category=category,
        authentication_type="oauth2",
        oauth_provider=oauth_provider,
        capabilities=(*reads, *future_writes),
        read_capabilities=reads,
        future_write_capabilities=future_writes,
        oauth_scopes=scopes,
        webhook_support=webhook,
        external_writes_enabled=False,
        resource_types=resources,
        required_configuration_fields={
            "google": ("google_oauth_client_id", "google_oauth_client_secret", "integration_oauth_callback_url", "credential_store"),
            "meta": ("meta_oauth_client_id", "meta_oauth_client_secret", "integration_oauth_callback_url", "credential_store"),
            "microsoft": ("microsoft_oauth_client_id", "microsoft_oauth_client_secret", "integration_oauth_callback_url", "credential_store"),
        }[oauth_provider],
        resource_selection_required=bool(resources),
        trusted_authorization_hosts=OAUTH_PROVIDER_ENDPOINTS[oauth_provider].trusted_authorization_hosts,
        foundation_only=foundation_only,
    )


_DEFINITIONS: Final[tuple[ConnectorDefinition, ...]] = (
    _connector(
        "whatsapp_business", "WhatsApp Business", "Receive customer messages and read approved business resources.",
        "communication", "meta", reads=("receive_messages", "read_templates"),
        future_writes=("future_send_messages",),
        scopes=("business_management", "whatsapp_business_management", "whatsapp_business_messaging"),
        resources=("meta_business", "whatsapp_business_account", "phone_number"), webhook=True,
    ),
    _connector(
        "gmail", "Gmail", "Read mailbox metadata and explicitly selected mail.",
        "communication", "google", reads=("read_mail_metadata", "read_selected_mail"),
        future_writes=("future_send_email",),
        scopes=("openid", "email", "https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"),
        resources=("mailbox",), webhook=True,
    ),
    _connector(
        "google_calendar", "Google Calendar", "Read calendars and events for selected calendars.",
        "calendar", "google", reads=("read_calendars", "read_events"),
        future_writes=("future_create_event", "future_update_event"),
        scopes=("openid", "email", "https://www.googleapis.com/auth/calendar.readonly"),
        resources=("calendar",), webhook=True,
    ),
    _connector(
        "google_ads", "Google Ads", "Read selected advertising accounts and campaign performance.",
        "advertising", "google", reads=("read_accounts", "read_campaign_performance"),
        future_writes=("future_create_campaign", "future_launch_campaign", "future_change_budget"),
        scopes=("openid", "email", "https://www.googleapis.com/auth/adwords"),
        resources=("google_ads_customer",),
    ),
    _connector(
        "meta_ads", "Meta Ads", "Read selected Meta ad accounts and performance.",
        "advertising", "meta", reads=("read_accounts", "read_campaign_performance"),
        future_writes=("future_create_campaign", "future_launch_campaign", "future_change_budget"),
        scopes=("business_management", "ads_read", "ads_management"), resources=("meta_business", "ad_account"), webhook=True,
    ),
    _connector(
        "facebook", "Facebook", "Read selected Pages and content performance.",
        "social", "meta", reads=("read_pages", "read_content_performance"),
        future_writes=("future_publish_content",),
        scopes=("pages_show_list", "pages_read_engagement", "pages_manage_posts"), resources=("facebook_page",), webhook=True,
    ),
    _connector(
        "instagram", "Instagram", "Read selected professional accounts and content performance.",
        "social", "meta", reads=("read_accounts", "read_content_performance"),
        future_writes=("future_publish_content",),
        scopes=("instagram_basic", "instagram_manage_insights", "instagram_content_publish", "pages_show_list"),
        resources=("facebook_page", "instagram_account"), webhook=True,
    ),
    _connector(
        "microsoft_outlook", "Microsoft Outlook", "Future-ready read-only Outlook mail and calendar foundation.",
        "productivity", "microsoft", reads=("read_mail", "read_calendars", "read_events"),
        future_writes=("future_send_email", "future_create_event", "future_update_event"),
        scopes=("openid", "email", "offline_access", "User.Read", "Mail.Read", "Calendars.Read"),
        resources=("mailbox", "calendar"), webhook=True, foundation_only=True,
    ),
)

CONNECTOR_REGISTRY: Final[Mapping[ConnectorType, ConnectorDefinition]] = MappingProxyType(
    {item.connector_type: item for item in _DEFINITIONS}
)


def list_connector_definitions() -> tuple[ConnectorDefinition, ...]:
    return tuple(CONNECTOR_REGISTRY.values())


def require_connector(connector_type: str) -> ConnectorDefinition:
    value = CONNECTOR_REGISTRY.get(connector_type)  # type: ignore[arg-type]
    if value is None:
        raise IntegrationValidationError("unsupported_connector")
    return value
