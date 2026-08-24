from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CreativeGenerationRequest:
    """Provider-neutral request for a future internal draft asset generator."""

    business_id: UUID
    creative_asset_id: UUID
    instructions: str
    width: int | None
    height: int | None
    aspect_ratio: str | None

    def __post_init__(self) -> None:
        if not self.instructions.strip() or len(self.instructions) > 5000:
            raise ValueError("instructions must contain 1 to 5000 characters")


@dataclass(frozen=True, slots=True)
class CreativeGenerationResult:
    """Safe reference result; binary asset bytes never enter PostgreSQL."""

    storage_reference: str
    width: int
    height: int
    provider_request_id: str | None = None


@runtime_checkable
class CreativeGenerationProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    async def generate_draft(self, request: CreativeGenerationRequest) -> CreativeGenerationResult: ...


class CreativeProviderNotConfiguredError(RuntimeError):
    pass


class UnavailableCreativeGenerationProvider:
    """Intentional default: it makes the disabled integration explicit and testable."""

    provider_name = "unconfigured"

    async def generate_draft(self, request: CreativeGenerationRequest) -> CreativeGenerationResult:
        del request
        raise CreativeProviderNotConfiguredError("Creative generation provider is not configured")
