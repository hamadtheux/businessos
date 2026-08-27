from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal, Mapping, Protocol, Sequence, runtime_checkable

from app.integrations.credentials import CredentialMaterial


Provider = Literal["google", "meta"]
ConnectionState = Literal[
    "configuration_required", "authorization_required", "connected", "healthy",
    "degraded", "reauthorization_required", "failed", "disabled",
]
ProductState = Literal[
    "attention_required", "submitted", "processing", "eligible", "limited",
    "ineligible", "warning", "error", "archived",
]
IssueResolution = Literal[
    "auto_fix_safe", "owner_input_required", "store_source_update_required",
    "provider_policy_review_required",
]
EvidenceClass = Literal[
    "first_party_observed", "business_brain", "provider_supplied",
    "public_research", "ai_inference",
]
AttributionClass = Literal[
    "provider_attributed", "first_party_observed", "ai_business_os_derived", "unknown",
]


@dataclass(frozen=True, slots=True)
class AdvertisingAccount:
    provider: Provider
    external_reference: str
    display_name: str
    currency: str | None = None
    manager_reference: str | None = None
    status: ConnectionState = "connected"


@dataclass(frozen=True, slots=True)
class CommerceDestinationAccount:
    provider: Provider
    external_reference: str
    display_name: str
    status: ConnectionState = "connected"
    parent_reference: str | None = None


@dataclass(frozen=True, slots=True)
class ProductDestination:
    external_reference: str
    display_name: str
    managed: bool
    destination_type: Literal["api_data_source", "catalog"]
    parent_reference: str | None = None


@dataclass(frozen=True, slots=True)
class ProductIssue:
    code: str
    message: str
    severity: Literal["warning", "error"]
    resolution: IssueResolution
    provider_reference: str | None = None
    attribute: str | None = None


@dataclass(frozen=True, slots=True)
class ProductDestinationStatus:
    offer_id: str
    external_product_reference: str | None
    state: ProductState
    issues: tuple[ProductIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class NormalizedProductDestinationInput:
    offer_id: str
    title: str
    description: str
    link: str
    image_link: str
    availability: Literal["in_stock", "out_of_stock", "preorder", "backorder"]
    price: Decimal
    currency: str
    content_language: str = "en"
    feed_label: str | None = None
    condition: Literal["new", "refurbished", "used"] = "new"
    additional_image_links: tuple[str, ...] = ()
    sale_price: Decimal | None = None
    brand: str | None = None
    gtin: str | None = None
    mpn: str | None = None
    google_product_category: str | None = None
    product_type: str | None = None
    item_group_id: str | None = None
    custom_labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProductWriteResult:
    offer_id: str
    external_product_reference: str | None
    state: ProductState
    acknowledged: bool
    issues: tuple[ProductIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductGroup:
    external_key: str
    name: str
    rule: Mapping[str, object]
    offer_ids: tuple[str, ...]
    external_reference: str | None = None


@dataclass(frozen=True, slots=True)
class AudienceReference:
    strategy: str
    countries: tuple[str, ...]
    first_party_segment_references: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    lifecycle_stages: tuple[str, ...] = ()
    consent_required: bool = False


@dataclass(frozen=True, slots=True)
class CampaignBudget:
    amount: Decimal
    currency: str
    interval: Literal["daily", "lifetime"]
    maximum_planned_spend: Decimal
    rationale: str


@dataclass(frozen=True, slots=True)
class CampaignAsset:
    asset_type: Literal["image", "logo", "video", "text", "business_name"]
    value: str
    source: Literal["catalog", "business_brain", "owner", "ai_generated"]


@dataclass(frozen=True, slots=True)
class CampaignCreative:
    headlines: tuple[str, ...]
    descriptions: tuple[str, ...]
    primary_text: str | None
    call_to_action: str
    landing_url: str
    assets: tuple[CampaignAsset, ...] = ()


@dataclass(frozen=True, slots=True)
class CampaignEvidence:
    classification: EvidenceClass
    summary: str
    source_type: str
    source_reference: str | None = None


@dataclass(frozen=True, slots=True)
class CampaignDraft:
    name: str
    goal: str
    provider: Provider
    campaign_type: Literal["retail_performance_max", "catalog_sales"]
    why_provider: str
    product_offer_ids: tuple[str, ...]
    product_group: ProductGroup | None
    audience: AudienceReference
    budget: CampaignBudget
    creative: CampaignCreative
    conversion_goal: str
    measurement_plan: str
    utm_parameters: Mapping[str, str]
    offer: str | None = None
    offer_source: Literal["none", "authoritative_promotion", "owner_authorized"] = "none"
    required_assets: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    evidence: tuple[CampaignEvidence, ...] = ()
    confidence: Decimal = Decimal("0.50")


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    code: str
    message: str
    blocking: bool = True


@dataclass(frozen=True, slots=True)
class CampaignPreflight:
    ready: bool
    issues: tuple[PreflightIssue, ...]
    eligible_products: int
    selected_products: int
    approval_required: bool = True


@dataclass(frozen=True, slots=True)
class ExternalCampaign:
    provider: Provider
    campaign_reference: str
    child_references: Mapping[str, str]
    status: Literal[
        "provider_pending", "active", "paused", "completed", "failed",
        "attention_required", "unknown_external_state",
    ]


@dataclass(frozen=True, slots=True)
class CampaignPerformance:
    campaign_reference: str
    period_start: date
    period_end: date
    spend: Decimal
    impressions: int
    clicks: int
    conversions: Decimal
    conversion_value: Decimal
    attribution: AttributionClass
    product_offer_id: str | None = None
    product_group_reference: str | None = None


@runtime_checkable
class ProductDestinationConnector(Protocol):
    provider: Provider

    async def list_accounts(self, credentials: CredentialMaterial) -> Sequence[CommerceDestinationAccount]: ...
    async def list_destinations(self, credentials: CredentialMaterial, *, account_reference: str) -> Sequence[ProductDestination]: ...
    async def upsert_product(self, credentials: CredentialMaterial, *, account_reference: str, destination_reference: str, product: NormalizedProductDestinationInput, idempotency_key: str) -> ProductWriteResult: ...
    async def archive_product(self, credentials: CredentialMaterial, *, account_reference: str, destination_reference: str, offer_id: str, external_product_reference: str | None, owned: bool, idempotency_key: str) -> ProductWriteResult: ...
    async def reconcile_products(self, credentials: CredentialMaterial, *, account_reference: str, destination_reference: str | None = None) -> Sequence[ProductDestinationStatus]: ...
    async def upsert_product_group(self, credentials: CredentialMaterial, *, account_reference: str, group: ProductGroup, idempotency_key: str) -> ProductGroup: ...
