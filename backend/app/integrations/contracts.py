from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Mapping, Protocol, Sequence, runtime_checkable

from app.integrations.credentials import CredentialMaterial


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    external_account_reference: str
    display_name: str


@dataclass(frozen=True, slots=True)
class ExternalResource:
    resource_type: str
    external_reference: str
    display_name: str
    parent_reference: str | None = None
    metadata: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    state: str
    code_challenge: str
    redirect_uri: str
    scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorizationExchange:
    credentials: CredentialMaterial
    granted_scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CredentialRefreshResult:
    status: Literal["refreshed", "reauth_required", "revoked", "temporary_failure"]
    credentials: CredentialMaterial | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectionHealthResult:
    health: Literal["healthy", "degraded", "reauth_required", "revoked"]
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedIntegrationEvent:
    external_event_id: str
    event_type: Literal[
        "message_received", "message_status_updated", "email_received",
        "calendar_event_changed", "performance_data_available",
    ]
    occurred_at: datetime
    safe_payload: Mapping[str, str | int | bool | None]


@dataclass(frozen=True, slots=True)
class NormalizedAdPerformance:
    external_campaign_reference: str
    period_start: date
    period_end: date
    spend: Decimal
    impressions: int
    clicks: int
    conversions: int
    revenue: Decimal = Decimal("0")
    reach: int = 0
    leads: int = 0
    external_product_reference: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedCampaignStatus:
    external_campaign_reference: str
    status: Literal[
        "provider_pending", "active", "paused", "completed", "failed",
        "attention_required", "unknown_external_state",
    ]
    provider_status: str
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExternalCalendarEvent:
    external_event_id: str
    external_calendar_reference: str
    title: str
    starts_at: datetime
    ends_at: datetime
    status: Literal["confirmed", "canceled"]
    updated_at: datetime


@runtime_checkable
class IntegrationConnector(Protocol):
    connector_type: str
    async def build_authorization_url(self, request: AuthorizationRequest) -> str: ...
    async def exchange_authorization_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> AuthorizationExchange: ...
    async def refresh_credentials(self, credentials: CredentialMaterial) -> CredentialRefreshResult: ...
    async def revoke_credentials(self, credentials: CredentialMaterial) -> None: ...
    async def get_identity(self, credentials: CredentialMaterial) -> ExternalIdentity: ...
    async def list_resources(self, credentials: CredentialMaterial) -> Sequence[ExternalResource]: ...
    async def health_check(self, credentials: CredentialMaterial) -> ConnectionHealthResult: ...
    async def normalize_webhook(self, payload: Mapping[str, object]) -> NormalizedIntegrationEvent: ...
    async def normalize_webhooks(
        self, payload: Mapping[str, object]
    ) -> Sequence[NormalizedIntegrationEvent]: ...


@runtime_checkable
class CalendarReadConnector(IntegrationConnector, Protocol):
    async def list_calendar_events(
        self,
        credentials: CredentialMaterial,
        *,
        calendar_reference: str,
        starts_at: datetime,
        ends_at: datetime,
    ) -> Sequence[ExternalCalendarEvent]: ...


@runtime_checkable
class AdsPerformanceReadConnector(IntegrationConnector, Protocol):
    async def read_campaign_performance(
        self,
        credentials: CredentialMaterial,
        *,
        account_reference: str,
        period_start: date,
        period_end: date,
    ) -> Sequence[NormalizedAdPerformance]: ...
    async def read_campaign_status(
        self, credentials: CredentialMaterial, *, account_reference: str,
        campaign_reference: str,
    ) -> NormalizedCampaignStatus: ...
