from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import ValidationError

from app.schemas.marketing import MAX_VIDEO_STRATEGY_BYTES, VideoCreativeStrategy


@dataclass(frozen=True, slots=True)
class VideoGenerationRequest:
    """Internal correlation envelope for one bounded, approved strategy.

    Provider adapters MUST construct their external request from
    :meth:`external_payload`. They must not serialize this internal object, whose
    tenant and asset identifiers exist only for local correlation.

    A real provider adapter must forward ``idempotency_key`` through the
    provider's native idempotency mechanism. That is required to close the
    accepted-by-provider/local-transaction-failed retry window.
    """

    business_id: UUID
    creative_asset_id: UUID
    strategy_json: str
    duration_seconds: int
    aspect_ratio: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if (
            not self.strategy_json.strip()
            or len(self.strategy_json.encode("utf-8")) > MAX_VIDEO_STRATEGY_BYTES
        ):
            raise ValueError("Video strategy size is invalid")
        if self.duration_seconds not in {6, 8, 15, 30}:
            raise ValueError("Video duration is invalid")
        if self.aspect_ratio not in {"9:16", "16:9", "1:1"}:
            raise ValueError("Video aspect ratio is invalid")
        try:
            strategy = VideoCreativeStrategy.model_validate_json(self.strategy_json)
        except ValidationError:
            raise ValueError("Video strategy is invalid") from None
        if (
            strategy.duration_seconds != self.duration_seconds
            or strategy.aspect_ratio != self.aspect_ratio
            or strategy.canonical_json() != self.strategy_json
        ):
            raise ValueError("Video strategy does not match the provider request")
        try:
            normalized_key = str(UUID(self.idempotency_key))
        except (ValueError, AttributeError, TypeError):
            raise ValueError("Video idempotency key is invalid") from None
        if normalized_key != self.idempotency_key:
            raise ValueError("Video idempotency key must be a canonical UUID")

    def external_payload(self) -> dict[str, object]:
        """Return the explicit allowlisted payload safe for an external provider."""
        return {
            "strategy": json.loads(self.strategy_json),
            "duration_seconds": self.duration_seconds,
            "aspect_ratio": self.aspect_ratio,
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True, slots=True)
class VideoGenerationSubmission:
    provider_name: str
    provider_job_reference: str

    def __post_init__(self) -> None:
        provider_name = self.provider_name.strip()
        provider_job_reference = self.provider_job_reference.strip()
        if not 1 <= len(provider_name) <= 64:
            raise ValueError("Video provider name is invalid")
        if not 1 <= len(provider_job_reference) <= 255:
            raise ValueError("Video provider job reference is invalid")
        object.__setattr__(self, "provider_name", provider_name)
        object.__setattr__(self, "provider_job_reference", provider_job_reference)


@runtime_checkable
class VideoGenerationProvider(Protocol):
    """Adapter contract for an idempotent external video submission.

    Implementations must build external data only from ``request.external_payload()``
    and use its stable idempotency key when the provider supports submission keys.
    """

    @property
    def provider_name(self) -> str: ...

    @property
    def configured(self) -> bool: ...

    async def submit(
        self,
        request: VideoGenerationRequest,
    ) -> VideoGenerationSubmission: ...


class VideoProviderError(RuntimeError):
    """Safe provider failure that does not carry external response content."""


class VideoProviderNotConfiguredError(VideoProviderError):
    pass


class UnavailableVideoGenerationProvider:
    """Truthful default until a real Veo-style adapter is configured."""

    provider_name = "unconfigured"
    configured = False

    async def submit(
        self,
        request: VideoGenerationRequest,
    ) -> VideoGenerationSubmission:
        del request
        raise VideoProviderNotConfiguredError
