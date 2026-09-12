from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from app.exceptions.marketing import MarketingValidationError
from app.services.marketing_media import MAX_MARKETING_VIDEO_BYTES
from app.services.marketing_video import (
    MARKETING_VIDEO_VARIANTS,
    ProbedMarketingVideo,
    probe_marketing_video_file,
    render_marketing_video_variant,
)
from app.storage.base import ObjectStorage, StorageError


# Rendered 1080p derivatives may be larger than the uploaded source after
# normalization. Keep a hard operational ceiling so a malformed/high-complexity
# input cannot create unbounded object-storage writes.
MAX_MARKETING_VIDEO_DERIVATIVE_BYTES = 250_000_000

_SOURCE_EXTENSIONS = frozenset({"mp4", "webm"})


@dataclass(frozen=True, slots=True)
class PreparedMarketingVideoDerivative:
    key: str
    storage_reference: str
    content_type: str
    width: int
    height: int
    aspect_ratio: str
    duration_seconds: int
    video_codec: str
    audio_codec: str | None
    transformation: str


@dataclass(frozen=True, slots=True)
class PreparedMarketingVideoPackage:
    width: int
    height: int
    duration_seconds: int
    video_codec: str
    audio_codec: str | None
    format_name: str | None
    variants: tuple[PreparedMarketingVideoDerivative, ...]


async def prepare_marketing_video_derivatives(
    *,
    storage: ObjectStorage,
    business_id: UUID,
    asset_id: UUID,
    source_reference: str,
    source_extension: str,
) -> PreparedMarketingVideoPackage:
    """
    Prepare deterministic social-video derivatives from an immutable upload.

    The source and outputs stay file-backed throughout processing. The durable
    source object is never deleted. If derivative preparation fails, every
    derivative key attempted by this invocation is removed best-effort.
    """
    normalized_extension = source_extension.casefold().strip()
    if normalized_extension not in _SOURCE_EXTENSIONS:
        raise MarketingValidationError("marketing_media_unsupported")

    expected_source_key = (
        f"businesses/{business_id}/marketing/uploads/{asset_id}/"
        f"source.{normalized_extension}"
    )

    try:
        source_key = storage.object_key_from_reference(source_reference)
    except StorageError:
        raise MarketingValidationError(
            "marketing_media_reference_invalid"
        ) from None

    if source_key != expected_source_key:
        raise MarketingValidationError(
            "marketing_media_reference_invalid"
        )

    attempted_variant_keys: list[str] = []

    with TemporaryDirectory(
        prefix="aibos-marketing-video-preparation-"
    ) as temp_directory:
        root = Path(temp_directory)
        source_path = root / f"source.{normalized_extension}"
        output_directory = root / "variants"

        await storage.get_file(
            source_key,
            source_path,
            max_bytes=MAX_MARKETING_VIDEO_BYTES,
        )

        source_probe = await probe_marketing_video_file(source_path)

        prepared: list[PreparedMarketingVideoDerivative] = []

        try:
            for key, width, height, aspect_ratio in MARKETING_VIDEO_VARIANTS:
                rendered = await render_marketing_video_variant(
                    source_path,
                    output_directory,
                    key=key,
                    width=width,
                    height=height,
                    aspect_ratio=aspect_ratio,
                    duration_seconds=source_probe.duration_seconds,
                )

                rendered_probe = await probe_marketing_video_file(
                    rendered.path
                )
                _validate_rendered_derivative(
                    rendered_probe,
                    expected_width=width,
                    expected_height=height,
                    source_duration_seconds=source_probe.duration_seconds,
                )

                variant_object_key = (
                    f"businesses/{business_id}/marketing/uploads/{asset_id}/"
                    f"variants/{key}.mp4"
                )
                attempted_variant_keys.append(variant_object_key)

                await storage.put_file(
                    variant_object_key,
                    rendered.path,
                    "video/mp4",
                    max_bytes=MAX_MARKETING_VIDEO_DERIVATIVE_BYTES,
                )

                reference = storage.public_url(variant_object_key)
                if (
                    not isinstance(reference, str)
                    or not reference
                    or len(reference) > 1024
                ):
                    raise StorageError(
                        "Invalid video derivative reference"
                    )

                prepared.append(
                    PreparedMarketingVideoDerivative(
                        key=key,
                        storage_reference=reference,
                        content_type="video/mp4",
                        width=width,
                        height=height,
                        aspect_ratio=aspect_ratio,
                        duration_seconds=rendered_probe.duration_seconds,
                        video_codec=rendered_probe.video_codec,
                        audio_codec=rendered_probe.audio_codec,
                        transformation="contain_no_crop",
                    )
                )

        except Exception:
            for object_key in reversed(attempted_variant_keys):
                await _best_effort_delete(storage, object_key)
            raise

    return PreparedMarketingVideoPackage(
        width=source_probe.width,
        height=source_probe.height,
        duration_seconds=source_probe.duration_seconds,
        video_codec=source_probe.video_codec,
        audio_codec=source_probe.audio_codec,
        format_name=source_probe.format_name,
        variants=tuple(prepared),
    )


def _validate_rendered_derivative(
    probe: ProbedMarketingVideo,
    *,
    expected_width: int,
    expected_height: int,
    source_duration_seconds: int,
) -> None:
    if (
        probe.width != expected_width
        or probe.height != expected_height
        or probe.video_codec != "h264"
        or probe.audio_codec not in {None, "aac"}
        or abs(
            probe.duration_seconds - source_duration_seconds
        ) > 1
        or not probe.format_name
        or "mp4" not in probe.format_name.casefold()
    ):
        raise MarketingValidationError(
            "marketing_video_render_failed"
        )


async def _best_effort_delete(
    storage: ObjectStorage,
    object_key: str,
) -> None:
    try:
        await storage.delete(object_key)
    except StorageError:
        pass
