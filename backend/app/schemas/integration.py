from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from app.domain.integrations import AuthenticationState, ConnectionHealth, ConnectionStatus, ConnectorType


SafeReference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
SetupStatus = Literal["available", "provider_setup_required", "coming_soon"]


class ConnectorDefinitionResponse(BaseModel):
    connector_type: ConnectorType
    display_name: str
    description: str
    category: Literal["communication", "productivity", "calendar", "advertising", "social"]
    authentication_type: Literal["oauth2"]
    capabilities: tuple[str, ...]
    read_capabilities: tuple[str, ...]
    future_write_capabilities: tuple[str, ...]
    requested_scopes: tuple[str, ...]
    webhook_support: bool
    external_writes_enabled: bool
    resource_types: tuple[str, ...]
    configuration_requirements: tuple[str, ...]
    resource_selection_required: bool
    setup_status: SetupStatus
    model_config = ConfigDict(extra="forbid")


class IntegrationConnectionResponse(BaseModel):
    id: UUID
    business_id: UUID
    connector_type: ConnectorType
    display_name: str
    status: ConnectionStatus
    authentication_state: AuthenticationState
    health: ConnectionHealth
    external_account_reference: str | None
    external_account_display_name: str | None
    selected_resources: list[dict[str, str]]
    scopes_granted: list[str]
    connected_by_user_id: UUID | None
    connected_at: AwareDatetime | None
    last_health_check_at: AwareDatetime | None
    last_successful_sync_at: AwareDatetime | None
    failure_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AuthorizationStartRequest(BaseModel):
    redirect_target: Literal["/integrations"] = "/integrations"
    model_config = ConfigDict(extra="forbid")


class AuthorizationStartResponse(BaseModel):
    connector_type: ConnectorType
    authorization_url: str = Field(min_length=1, max_length=4096)
    expires_at: AwareDatetime
    model_config = ConfigDict(extra="forbid")


class AuthorizationCallbackResponse(BaseModel):
    connector_type: ConnectorType
    status: ConnectionStatus
    redirect_target: Literal["/integrations"]
    model_config = ConfigDict(extra="forbid")


class ExternalResourceResponse(BaseModel):
    resource_type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    external_reference: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=160)
    parent_reference: str | None = Field(default=None, min_length=1, max_length=255)
    metadata: dict[str, str] | None = None
    model_config = ConfigDict(extra="forbid")


class ResourceSelectionRequest(BaseModel):
    resource_type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    external_reference: SafeReference
    model_config = ConfigDict(extra="forbid")


class IntegrationWebhookEventResponse(BaseModel):
    id: UUID
    business_id: UUID
    integration_connection_id: UUID
    connector_type: ConnectorType
    external_event_id: str
    event_type: Literal[
        "message_received",
        "message_status_updated",
        "email_received",
        "calendar_event_changed",
        "performance_data_available",
    ]
    status: Literal["received", "processed", "failed", "duplicate"]
    normalized_payload: dict[str, object]
    received_at: AwareDatetime
    processed_at: AwareDatetime | None
    failure_code: str | None
    created_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class EntityLinkCreate(BaseModel):
    internal_entity_type: Literal["appointment", "campaign", "conversation"]
    internal_entity_id: UUID
    external_resource_reference: SafeReference
    external_entity_id: SafeReference
    model_config = ConfigDict(extra="forbid")


class IntegrationEntityLinkResponse(EntityLinkCreate):
    id: UUID
    business_id: UUID
    integration_connection_id: UUID
    sync_state: Literal["linked", "in_sync", "internal_changed", "external_changed", "conflict", "unlinked"]
    last_internal_change_at: AwareDatetime | None
    last_external_change_at: AwareDatetime | None
    last_synced_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    model_config = ConfigDict(from_attributes=True, extra="forbid")
