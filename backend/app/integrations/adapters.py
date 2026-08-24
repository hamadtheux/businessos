from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from app.exceptions.integration import IntegrationProviderUnavailableError
from app.integrations.contracts import IntegrationConnector
from app.integrations.registry import CONNECTOR_REGISTRY
from app.core.config import settings


class DisabledIntegrationConnector:
    """Truthful runtime adapter until provider app config and vault exist."""

    def __init__(self, connector_type: str) -> None:
        self.connector_type = connector_type

    def _unavailable(self) -> None:
        raise IntegrationProviderUnavailableError("provider_unavailable")

    async def build_authorization_url(self, request): self._unavailable()
    async def exchange_authorization_code(self, **kwargs): self._unavailable()
    async def refresh_credentials(self, credentials): self._unavailable()
    async def revoke_credentials(self, credentials): self._unavailable()
    async def get_identity(self, credentials): self._unavailable()
    async def list_resources(self, credentials): self._unavailable()
    async def health_check(self, credentials): self._unavailable()
    async def normalize_webhook(self, payload): self._unavailable()


class ConnectorAdapterRegistry:
    def __init__(self, adapters: Mapping[str, IntegrationConnector] | None = None) -> None:
        values: dict[str, IntegrationConnector] = {}
        for connector_type, adapter in (adapters or {}).items():
            if connector_type not in CONNECTOR_REGISTRY or adapter.connector_type != connector_type:
                raise ValueError("Invalid connector adapter registration")
            values[connector_type] = adapter
        self._adapters = MappingProxyType(values)

    def get(self, connector_type: str) -> IntegrationConnector:
        if connector_type not in CONNECTOR_REGISTRY:
            raise IntegrationProviderUnavailableError("provider_unavailable")
        return self._adapters.get(connector_type) or DisabledIntegrationConnector(connector_type)  # type: ignore[return-value]

    def is_configured(self, connector_type: str) -> bool:
        return connector_type in self._adapters


from app.integrations.oauth_adapters import build_configured_oauth_adapters

connector_adapters = ConnectorAdapterRegistry(build_configured_oauth_adapters(settings))
