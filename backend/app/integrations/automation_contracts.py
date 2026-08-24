from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal, Mapping, Protocol, Sequence, runtime_checkable

from app.integrations.contracts import CredentialMaterial


EvidenceType = Literal[
    "provider_result", "public_url", "public_metadata", "ai_inference"
]


@dataclass(frozen=True, slots=True)
class CompetitorResearchContext:
    business_name: str
    industry: str
    business_description: str | None
    products_and_services: tuple[str, ...]
    location_or_service_area: str | None
    website_domain: str | None
    connected_metadata: tuple[str, ...]
    brain_revision: str


@dataclass(frozen=True, slots=True)
class CompetitorEvidenceResult:
    source_type: EvidenceType
    source_reference: str
    title: str
    excerpt: str
    observed_at: datetime
    # Untrusted provider metadata. The service boundary validates recursive
    # JSON shape, depth, key count, string length, and encoded size.
    safe_metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompetitorCandidateResult:
    name: str
    website_domain: str | None
    canonical_url: str | None
    discovery_reason: str
    confidence: Decimal
    industry_relationship: str | None
    geographic_relationship: str | None
    evidence: tuple[CompetitorEvidenceResult, ...]


@runtime_checkable
class CompetitorResearchProvider(Protocol):
    provider_key: str

    async def discover(
        self, context: CompetitorResearchContext, *, limit: int
    ) -> Sequence[CompetitorCandidateResult]: ...


@dataclass(frozen=True, slots=True)
class WebsiteInstallationRequest:
    widget_public_id: str
    external_site_reference: str


@dataclass(frozen=True, slots=True)
class WebsiteInstallationResult:
    installed: bool
    verification_status: Literal["not_checked", "healthy", "failed"]
    external_reference: str | None = None
    failure_code: str | None = None


@runtime_checkable
class WebsiteDeploymentProvider(Protocol):
    provider_key: str
    target_type: str

    async def install(
        self,
        credentials: CredentialMaterial,
        request: WebsiteInstallationRequest,
        *,
        idempotency_key: str,
    ) -> WebsiteInstallationResult: ...


@dataclass(frozen=True, slots=True)
class AdvertisingExecutionRequest:
    campaign_reference: str
    safe_configuration: Mapping[str, object]


@runtime_checkable
class AdvertisingProvider(Protocol):
    provider_key: str
    connector_type: str

    async def create_campaign(
        self,
        credentials: CredentialMaterial,
        request: AdvertisingExecutionRequest,
        *,
        idempotency_key: str,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class SocialPublishingRequest:
    content_reference: str
    content: str
    media_references: tuple[str, ...]


@runtime_checkable
class SocialPublishingProvider(Protocol):
    provider_key: str
    connector_type: str

    async def publish(
        self,
        credentials: CredentialMaterial,
        request: SocialPublishingRequest,
        *,
        idempotency_key: str,
    ) -> str: ...
