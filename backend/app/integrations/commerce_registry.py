from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.integrations.commerce_contracts import CommerceConnector, CommerceFeedConnector
from app.integrations.commerce_adapters import BUILTIN_COMMERCE_CONNECTORS
from app.models.commerce import COMMERCE_PROVIDERS


@dataclass(frozen=True, slots=True)
class CommerceProviderDefinition:
    provider: str
    display_name: str
    authentication: str
    capabilities: tuple[str, ...]
    configured: bool
    implementation_status: str


_DISPLAY_NAMES = {
    "shopify": "Shopify",
    "woocommerce": "WooCommerce",
    "bigcommerce": "BigCommerce",
    "magento": "Magento / Adobe Commerce",
    "custom_api": "Custom commerce API",
    "csv": "CSV import",
    "xml_feed": "XML product feed",
    "google_product_feed": "Google-style product feed",
    "website": "Ecommerce website discovery",
    "manual": "Manual catalog",
}


class CommerceConnectorRegistry:
    def __init__(
        self,
        connectors: Mapping[str, CommerceConnector] | None = None,
        feed_connectors: Mapping[str, CommerceFeedConnector] | None = None,
    ) -> None:
        values = dict(connectors or {})
        feeds = dict(feed_connectors or {})
        if any(key not in COMMERCE_PROVIDERS or value.provider != key for key, value in values.items()):
            raise ValueError("Invalid commerce connector registration")
        if any(key not in {"google_merchant_center", "meta_product_catalog"} or value.provider != key for key, value in feeds.items()):
            raise ValueError("Invalid commerce feed connector registration")
        self._connectors = MappingProxyType(values)
        self._feed_connectors = MappingProxyType(feeds)

    def connector(self, provider: str) -> CommerceConnector | None:
        return self._connectors.get(provider)

    def feed_connector(self, provider: str) -> CommerceFeedConnector | None:
        return self._feed_connectors.get(provider)

    def provider_definitions(self) -> tuple[CommerceProviderDefinition, ...]:
        local_imports = {"csv", "xml_feed", "google_product_feed"}
        return tuple(
            CommerceProviderDefinition(
                provider=provider,
                display_name=_DISPLAY_NAMES[provider],
                authentication=(
                    "local_import"
                    if provider in local_imports or provider == "manual"
                    else "provider_configuration"
                ),
                capabilities=(
                    tuple(sorted(self._connectors[provider].capabilities))
                    if provider in self._connectors
                    else ("catalog_import", "repeat_import_idempotency")
                    if provider in {"csv", "xml_feed", "google_product_feed"}
                    else ("manual_catalog",)
                    if provider == "manual"
                    else ()
                ),
                configured=provider in self._connectors,
                implementation_status=(
                    "code_ready_credentials_required"
                    if provider in self._connectors
                    else "production_functional"
                    if provider in local_imports or provider == "manual"
                    else "not_implemented"
                ),
            )
            for provider in COMMERCE_PROVIDERS
        )


# The adapters are real code, but a connection is never healthy until its own
# credential reference authenticates and a durable sync succeeds.
commerce_connectors = CommerceConnectorRegistry(BUILTIN_COMMERCE_CONNECTORS)
