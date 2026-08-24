from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.integrations.credentials import CredentialMaterial
from app.schemas.ai_action_payload import ActionPayloadType
from app.services.action_registry import ACTION_REGISTRY


class ConnectorActionResult(BaseModel):
    succeeded: bool
    external_reference_id: str | None = Field(
        default=None, min_length=1, max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$",
    )
    failure_code: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$"
    )
    safe_metadata: dict[str, str | int | bool] = Field(
        default_factory=dict, max_length=20
    )

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("safe_metadata")
    @classmethod
    def bound_metadata(
        cls, value: dict[str, str | int | bool]
    ) -> dict[str, str | int | bool]:
        for key, item in value.items():
            if not key or len(key) > 64:
                raise ValueError("connector metadata is invalid")
            if isinstance(item, str) and len(item) > 500:
                raise ValueError("connector metadata is invalid")
        return value

    @model_validator(mode="after")
    def validate_outcome(self) -> "ConnectorActionResult":
        if self.succeeded and self.failure_code is not None:
            raise ValueError("successful connector result cannot have a failure code")
        if not self.succeeded and self.failure_code is None:
            raise ValueError("failed connector result requires a failure code")
        return self


class ConnectorRequestNotSentError(Exception):
    """A safe adapter preflight failed before any provider request was sent."""


class ConnectorRejectedError(Exception):
    """The provider definitively rejected a completed request."""


@runtime_checkable
class ConnectorActionAdapter(Protocol):
    connector_type: str
    supported_action_types: frozenset[str]

    async def execute(
        self,
        *,
        credentials: CredentialMaterial,
        action_type: str,
        payload: ActionPayloadType,
        selected_resources: tuple[Mapping[str, str], ...],
        delivery_target: str | None,
        idempotency_key: str,
    ) -> ConnectorActionResult:
        """Invoke one authenticated provider action with stable idempotency."""
        ...


class ConnectorActionAdapterRegistry:
    """Immutable registry assembled from trusted platform configuration."""

    def __init__(
        self,
        adapters: Mapping[str, ConnectorActionAdapter] | None = None,
    ) -> None:
        values: dict[str, ConnectorActionAdapter] = {}
        for connector_type, adapter in (adapters or {}).items():
            if adapter.connector_type != connector_type:
                raise ValueError("connector action adapter identity mismatch")
            if not adapter.supported_action_types:
                raise ValueError("connector action adapter has no capabilities")
            for action_type in adapter.supported_action_types:
                ACTION_REGISTRY.require(action_type)
            values[connector_type] = adapter
        self._adapters = MappingProxyType(values)

    def get(
        self, connector_type: str, action_type: str
    ) -> ConnectorActionAdapter | None:
        adapter = self._adapters.get(connector_type)
        if adapter is None or action_type not in adapter.supported_action_types:
            return None
        return adapter

    def supports(self, connector_type: str, action_type: str) -> bool:
        return self.get(connector_type, action_type) is not None


def _configured_provider_adapters():
    from app.core.config import settings
    from app.integrations.provider_action_adapters import (
        build_configured_action_adapters,
    )

    return build_configured_action_adapters(settings)


# Registration requires an explicit trusted write flag, provider app config,
# and secure credential storage. The normal repository default stays empty.
connector_action_adapters = ConnectorActionAdapterRegistry(
    _configured_provider_adapters()
)
