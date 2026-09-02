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
    oauth_read_scopes: tuple[str, ...]
    oauth_write_scopes: tuple[str, ...]
    webhook_support: bool
    external_writes_enabled: Literal[False]
    resource_types: tuple[str, ...]
    required_configuration_fields: tuple[str, ...]
    resource_selection_required: bool
    trusted_authorization_hosts: tuple[str, ...]
    foundation_only: bool = False

    @property
    def oauth_scopes(self) -> tuple[str, ...]:
        """Complete supported scope set retained for validation compatibility."""
        return tuple(dict.fromkeys((*self.oauth_read_scopes, *self.oauth_write_scopes)))

    def requested_oauth_scopes(self, write_mode: str) -> tuple[str, ...]:
        scopes = self.oauth_read_scopes
        if write_mode != "disabled":
            scopes = (*scopes, *self.oauth_write_scopes)
        return tuple(dict.fromkeys(scopes))


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
    write_scopes: tuple[str, ...] = (),
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
        oauth_read_scopes=scopes,
        oauth_write_scopes=write_scopes,
        webhook_support=webhook,
        external_writes_enabled=False,
        resource_types=resources,
        required_configuration_fields={
            "google": ("google_oauth_client_id", "google_oauth_client_secret", "integration_oauth_callback_url", "credential_store"),
            "meta": ("meta_oauth_client_id", "meta_oauth_client_secret", "meta_login_configuration_id", "integration_oauth_callback_url", "credential_store"),
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
        scopes=("business_management", "whatsapp_business_management"),
        write_scopes=("whatsapp_business_messaging",),
        resources=("meta_business", "whatsapp_business_account", "phone_number"), webhook=True,
    ),
    _connector(
        "gmail", "Gmail", "Read mailbox metadata and explicitly selected mail.",
        "communication", "google", reads=("read_mail_metadata", "read_selected_mail"),
        future_writes=("future_send_email",),
        scopes=("openid", "email", "https://www.googleapis.com/auth/gmail.readonly"),
        write_scopes=("https://www.googleapis.com/auth/gmail.send",),
        resources=("mailbox",), webhook=False,
    ),
    _connector(
        "google_calendar", "Google Calendar", "Read calendars and events for selected calendars.",
        "calendar", "google", reads=("read_calendars", "read_events"),
        future_writes=("future_create_event", "future_update_event"),
        scopes=("openid", "email", "https://www.googleapis.com/auth/calendar.readonly"),
        resources=("calendar",), webhook=False,
    ),
    _connector(
        "google_ads", "Google Commerce & Ads", "Connect Merchant Center and Google Ads, select business assets, synchronize products, and execute governed retail campaigns.",
        "advertising", "google", reads=("read_accounts", "read_merchant_products", "read_campaign_performance"),
        future_writes=("future_sync_merchant_products", "future_create_campaign", "future_launch_campaign", "future_change_budget"),
        scopes=("openid", "email", "https://www.googleapis.com/auth/adwords", "https://www.googleapis.com/auth/content"),
        resources=("google_ads_customer", "google_merchant_account", "google_merchant_data_source", "google_merchant_ads_link", "google_conversion_action"),
    ),
    _connector(
        "meta_ads", "Meta Commerce & Ads", "Select Meta business assets, synchronize a catalog, and execute governed sales campaigns.",
        "advertising", "meta", reads=("read_accounts", "read_catalog", "read_campaign_performance"),
        future_writes=("future_sync_catalog", "future_create_campaign", "future_launch_campaign", "future_change_budget"),
        scopes=("business_management", "catalog_management", "ads_read"),
        write_scopes=("ads_management",),
        resources=("meta_business", "ad_account", "meta_catalog", "facebook_page", "conversion_dataset"), webhook=True,
    ),
    _connector(
        "facebook", "Meta Pages & Messenger", "Connect authorized Facebook Pages for engagement, Messenger, and lead retrieval.",
        "social", "meta", reads=("read_pages", "read_content_performance", "receive_messages", "retrieve_leads"),
        future_writes=("future_publish_content",),
        scopes=(
            "pages_show_list",
            "pages_read_engagement",
            "pages_manage_metadata",
            "pages_messaging",
            "leads_retrieval",
        ),
        write_scopes=("pages_manage_ads",),
        resources=("meta_business", "facebook_page"), webhook=True,
    ),
    _connector(
        "instagram", "Instagram", "Read selected professional accounts and content performance.",
        "social", "meta", reads=("read_accounts", "read_content_performance"),
        future_writes=("future_publish_content",),
        scopes=("instagram_basic", "instagram_manage_insights", "pages_show_list"),
        write_scopes=("instagram_content_publish",),
        resources=("facebook_page", "instagram_account"), webhook=True,
    ),
    _connector(
        "microsoft_outlook", "Microsoft Outlook", "Future-ready read-only Outlook mail and calendar foundation.",
        "productivity", "microsoft", reads=("read_mail", "read_calendars", "read_events"),
        future_writes=("future_send_email", "future_create_event", "future_update_event"),
        scopes=("openid", "email", "offline_access", "User.Read", "Mail.Read", "Calendars.Read"),
        resources=("mailbox", "calendar"), webhook=False, foundation_only=True,
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
