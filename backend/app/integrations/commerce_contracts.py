from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Protocol, Sequence, runtime_checkable

from app.integrations.credentials import CredentialMaterial
from app.schemas.commerce import (
    NormalizedCustomer,
    NormalizedOrder,
    NormalizedProduct,
    NormalizedStore,
    NormalizedWebhookEvent,
)


@dataclass(frozen=True, slots=True)
class CommerceSyncRequest:
    external_account_id: str
    mode: str
    domain: str = "store"
    cursor: Mapping[str, object] = field(default_factory=dict)
    store_url: str | None = None
    updated_since: datetime | None = None
    external_object_id: str | None = None
    page_size: int = 100


@dataclass(frozen=True, slots=True)
class CommerceSyncPage:
    """One bounded provider page containing only normalized objects."""

    domain: str
    store: NormalizedStore | None = None
    products: Sequence[NormalizedProduct] = ()
    customers: Sequence[NormalizedCustomer] = ()
    orders: Sequence[NormalizedOrder] = ()
    next_cursor: Mapping[str, object] = field(default_factory=dict)
    has_more: bool = False
    complete_snapshot: bool = False
    provider_metadata: Mapping[str, str | int | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.domain not in {"store", "products", "customers", "orders"}:
            raise ValueError("unsupported commerce sync domain")
        if len(self.products) + len(self.customers) + len(self.orders) > 1_000:
            raise ValueError("commerce sync page is too large")


@dataclass(frozen=True, slots=True)
class CommerceWebhookRequest:
    headers: Mapping[str, str]
    body: bytes
    connection_external_account_id: str | None = None


@runtime_checkable
class CommerceConnector(Protocol):
    """Provider-neutral read boundary; implementations never persist secrets."""

    provider: str
    capabilities: frozenset[str]

    async def synchronize(
        self,
        credentials: CredentialMaterial,
        request: CommerceSyncRequest,
        *,
        idempotency_key: str,
    ) -> CommerceSyncPage: ...

    def verify_and_parse_webhook(
        self,
        credentials: CredentialMaterial,
        request: CommerceWebhookRequest,
    ) -> NormalizedWebhookEvent: ...


@dataclass(frozen=True, slots=True)
class FeedProductResult:
    external_product_id: str | None
    status: str
    missing_attributes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    provider_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class FeedSyncResult:
    products: Mapping[str, FeedProductResult]
    provider_metadata: Mapping[str, str | int | bool | None] = field(default_factory=dict)


@runtime_checkable
class CommerceFeedConnector(Protocol):
    provider: str

    async def synchronize_products(
        self,
        credentials: CredentialMaterial,
        products: Sequence[Mapping[str, object]],
        *,
        external_account_id: str,
        idempotency_key: str,
    ) -> FeedSyncResult: ...
