from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


class BillingProviderUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedWebhookEvent:
    provider_event_id: str
    event_type: str
    occurred_at: datetime
    business_id: UUID | None


class BillingProvider(Protocol):
    name: str

    async def create_customer(self, *, business_id: UUID, owner_email: str) -> str: ...
    async def create_checkout(self, *, business_id: UUID, plan_code: str, interval: str) -> str: ...
    async def create_portal(self, *, business_id: UUID) -> str: ...
    async def cancel_subscription(self, *, provider_subscription_reference: str) -> None: ...
    async def change_subscription(self, *, provider_subscription_reference: str, plan_code: str, interval: str) -> None: ...
    async def verify_and_normalize_webhook(self, *, body: bytes, headers: dict[str, str]) -> NormalizedWebhookEvent: ...


class DisabledBillingProvider:
    name = "disabled"

    async def create_customer(self, **_: object) -> str:
        raise BillingProviderUnavailableError("billing_provider_unavailable")

    async def create_checkout(self, **_: object) -> str:
        raise BillingProviderUnavailableError("billing_provider_unavailable")

    async def create_portal(self, **_: object) -> str:
        raise BillingProviderUnavailableError("billing_provider_unavailable")

    async def cancel_subscription(self, **_: object) -> None:
        raise BillingProviderUnavailableError("billing_provider_unavailable")

    async def change_subscription(self, **_: object) -> None:
        raise BillingProviderUnavailableError("billing_provider_unavailable")

    async def verify_and_normalize_webhook(self, **_: object) -> NormalizedWebhookEvent:
        # Unverified payloads are never accepted or persisted as trusted events.
        raise BillingProviderUnavailableError("billing_provider_unavailable")


def get_billing_provider() -> BillingProvider:
    return DisabledBillingProvider()
