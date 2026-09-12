from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import AsyncSessionFactory
from app.exceptions.marketing import MarketingValidationError
from app.models.background_job import BackgroundJob
from app.models.marketing import CreativeAsset
from app.services.marketing_video_preparation import (
    PreparedMarketingVideoPackage,
    prepare_marketing_video_derivatives,
)
from app.storage.base import ObjectStorage, StorageError
from app.storage.factory import get_object_storage


@dataclass(frozen=True, slots=True)
class MarketingVideoPreparationDispatchOutcome:
    succeeded: bool
    failure_code: str | None = None
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class _PreparationContext:
    source_reference: str
    source_extension: str


_TERMINAL_MEDIA_FAILURES = frozenset(
    {
        "marketing_media_unsupported",
        "marketing_media_unreadable",
        "marketing_video_duration_required",
        "marketing_media_reference_invalid",
        "marketing_video_profile_invalid",
    }
)

_REQUIRED_VARIANTS = {
    "vertical_9_16": (1080, 1920, "9:16"),
    "landscape_16_9": (1920, 1080, "16:9"),
}


async def dispatch_marketing_video_preparation_job(
    job: BackgroundJob,
    *,
    storage: ObjectStorage | None = None,
) -> MarketingVideoPreparationDispatchOutcome:
    """
    Prepare one uploaded video without holding a DB transaction during FFmpeg.

    Protocol:
      1. tenant-scoped DB preflight
      2. close DB transaction
      3. storage/ffprobe/FFmpeg work
      4. short tenant-scoped DB finalization
    """
    asset_id = job.creative_asset_id
    if asset_id is None:
        return MarketingVideoPreparationDispatchOutcome(
            False,
            "invalid_job_state",
        )

    try:
        object_storage = storage or get_object_storage()
    except Exception:
        return MarketingVideoPreparationDispatchOutcome(
            False,
            "dependency_unavailable",
            True,
        )

    # ------------------------------------------------------------
    # Short preflight transaction.
    # ------------------------------------------------------------
    try:
        async with AsyncSessionFactory() as session:
            asset = await session.scalar(
                select(CreativeAsset)
                .where(
                    CreativeAsset.id == asset_id,
                    CreativeAsset.business_id == job.business_id,
                )
                .with_for_update()
            )

            if asset is None:
                await session.commit()
                return MarketingVideoPreparationDispatchOutcome(
                    False,
                    "resource_not_found",
                )

            if asset.generation_status == "ready":
                ready = _ready_asset_has_prepared_variants(
                    asset,
                    object_storage,
                )
                await session.commit()
                return MarketingVideoPreparationDispatchOutcome(
                    ready,
                    None if ready else "invalid_job_state",
                )

            if (
                asset.media_type != "video"
                or asset.source_type != "import"
                or asset.asset_type != "video_source"
                or asset.generation_status != "processing"
                or not asset.storage_reference
            ):
                await session.commit()
                return MarketingVideoPreparationDispatchOutcome(
                    False,
                    "invalid_job_state",
                )

            try:
                source_extension = _trusted_source_extension(
                    asset,
                    object_storage,
                )
            except MarketingValidationError as error:
                _mark_asset_failed(
                    asset,
                    failure_code=str(error),
                )
                await session.commit()
                return MarketingVideoPreparationDispatchOutcome(True)

            context = _PreparationContext(
                source_reference=asset.storage_reference,
                source_extension=source_extension,
            )
            await session.commit()

    except SQLAlchemyError:
        return MarketingVideoPreparationDispatchOutcome(
            False,
            "dependency_unavailable",
            True,
        )

    # ------------------------------------------------------------
    # No DB transaction is open here.
    # ------------------------------------------------------------
    try:
        package = await prepare_marketing_video_derivatives(
            storage=object_storage,
            business_id=job.business_id,
            asset_id=asset_id,
            source_reference=context.source_reference,
            source_extension=context.source_extension,
        )
    except MarketingValidationError as error:
        code = str(error)

        if code in _TERMINAL_MEDIA_FAILURES:
            if not await _persist_terminal_failure(
                business_id=job.business_id,
                asset_id=asset_id,
                source_reference=context.source_reference,
                failure_code=code,
            ):
                return MarketingVideoPreparationDispatchOutcome(
                    False,
                    "dependency_unavailable",
                    True,
                )
            # The queue successfully classified a permanently invalid upload.
            return MarketingVideoPreparationDispatchOutcome(True)

        if _is_final_attempt(job):
            await _persist_terminal_failure(
                business_id=job.business_id,
                asset_id=asset_id,
                source_reference=context.source_reference,
                failure_code="marketing_video_preparation_failed",
            )

        return MarketingVideoPreparationDispatchOutcome(
            False,
            "dependency_unavailable",
            True,
        )

    except (StorageError, OSError, TimeoutError, NotImplementedError):
        if _is_final_attempt(job):
            await _persist_terminal_failure(
                business_id=job.business_id,
                asset_id=asset_id,
                source_reference=context.source_reference,
                failure_code="marketing_video_preparation_unavailable",
            )

        return MarketingVideoPreparationDispatchOutcome(
            False,
            "dependency_unavailable",
            True,
        )

    # ------------------------------------------------------------
    # Short finalization transaction.
    # ------------------------------------------------------------
    try:
        async with AsyncSessionFactory() as session:
            asset = await session.scalar(
                select(CreativeAsset)
                .where(
                    CreativeAsset.id == asset_id,
                    CreativeAsset.business_id == job.business_id,
                )
                .with_for_update()
            )

            if asset is None:
                await session.commit()
                return MarketingVideoPreparationDispatchOutcome(
                    False,
                    "resource_not_found",
                )

            if asset.generation_status == "ready":
                ready = _ready_asset_has_prepared_variants(
                    asset,
                    object_storage,
                )
                await session.commit()
                return MarketingVideoPreparationDispatchOutcome(
                    ready,
                    None if ready else "invalid_job_state",
                )

            if (
                asset.media_type != "video"
                or asset.source_type != "import"
                or asset.asset_type != "video_source"
                or asset.generation_status != "processing"
                or asset.storage_reference != context.source_reference
            ):
                await session.commit()
                return MarketingVideoPreparationDispatchOutcome(
                    False,
                    "invalid_job_state",
                )

            _finalize_asset(
                asset,
                package=package,
            )
            await session.commit()

    except SQLAlchemyError:
        # Derivatives use deterministic keys. A retry may safely overwrite
        # them if this DB commit did not land.
        return MarketingVideoPreparationDispatchOutcome(
            False,
            "dependency_unavailable",
            True,
        )

    return MarketingVideoPreparationDispatchOutcome(True)


def _trusted_source_extension(
    asset: CreativeAsset,
    storage: ObjectStorage,
) -> str:
    if not asset.storage_reference:
        raise MarketingValidationError(
            "marketing_media_reference_invalid"
        )

    try:
        object_key = storage.object_key_from_reference(
            asset.storage_reference
        )
    except StorageError:
        raise MarketingValidationError(
            "marketing_media_reference_invalid"
        ) from None

    prefix = (
        f"businesses/{asset.business_id}/marketing/uploads/{asset.id}/"
        "source."
    )

    if object_key == f"{prefix}mp4":
        return "mp4"
    if object_key == f"{prefix}webm":
        return "webm"
    if object_key == f"{prefix}mov":
        return "mov"

    raise MarketingValidationError(
        "marketing_media_reference_invalid"
    )


def _finalize_asset(
    asset: CreativeAsset,
    *,
    package: PreparedMarketingVideoPackage,
) -> None:
    metadata = dict(asset.creative_metadata or {})
    variants = dict(metadata.get("variants") or {})

    for variant in package.variants:
        variants[variant.key] = {
            "storage_reference": variant.storage_reference,
            "content_type": variant.content_type,
            "width": variant.width,
            "height": variant.height,
            "aspect_ratio": variant.aspect_ratio,
            "duration_seconds": variant.duration_seconds,
            "video_codec": variant.video_codec,
            "audio_codec": variant.audio_codec,
            "transformation": variant.transformation,
        }

    metadata["variants"] = variants
    metadata["video_source"] = {
        "width": package.width,
        "height": package.height,
        "duration_seconds": package.duration_seconds,
        "video_codec": package.video_codec,
        "audio_codec": package.audio_codec,
        "format_name": package.format_name,
    }
    metadata["video_preparation"] = {
        "status": "ready",
        "version": 1,
    }

    asset.width = package.width
    asset.height = package.height
    asset.duration_seconds = package.duration_seconds
    asset.aspect_ratio = _reduced_aspect_ratio(
        package.width,
        package.height,
    )

    if package.width == package.height:
        asset.asset_type = "video_square"
    elif package.width > package.height:
        asset.asset_type = "video_landscape"
    else:
        asset.asset_type = "video_vertical"

    asset.creative_metadata = metadata
    asset.generation_status = "ready"


def _mark_asset_failed(
    asset: CreativeAsset,
    *,
    failure_code: str,
) -> None:
    if asset.generation_status != "processing":
        return

    metadata = dict(asset.creative_metadata or {})
    metadata["video_preparation"] = {
        "status": "failed",
        "failure_code": failure_code[:120],
        "version": 1,
    }

    asset.creative_metadata = metadata
    asset.generation_status = "failed"


async def _persist_terminal_failure(
    *,
    business_id: UUID,
    asset_id: UUID,
    source_reference: str,
    failure_code: str,
) -> bool:
    try:
        async with AsyncSessionFactory() as session:
            asset = await session.scalar(
                select(CreativeAsset)
                .where(
                    CreativeAsset.id == asset_id,
                    CreativeAsset.business_id == business_id,
                )
                .with_for_update()
            )

            if asset is None:
                await session.commit()
                return True

            if (
                asset.generation_status == "processing"
                and asset.storage_reference == source_reference
            ):
                _mark_asset_failed(
                    asset,
                    failure_code=failure_code,
                )

            await session.commit()
        return True

    except SQLAlchemyError:
        return False


def _ready_asset_has_prepared_variants(
    asset: CreativeAsset,
    storage: ObjectStorage,
) -> bool:
    if (
        asset.media_type != "video"
        or asset.source_type != "import"
        or asset.generation_status != "ready"
    ):
        return False

    metadata = asset.creative_metadata or {}
    variants = metadata.get("variants")
    if not isinstance(variants, dict):
        return False

    for key, (width, height, aspect_ratio) in _REQUIRED_VARIANTS.items():
        record = variants.get(key)
        if not isinstance(record, dict):
            return False

        if (
            record.get("content_type") != "video/mp4"
            or record.get("width") != width
            or record.get("height") != height
            or record.get("aspect_ratio") != aspect_ratio
            or record.get("video_codec") != "h264"
            or record.get("audio_codec") not in {None, "aac"}
            or record.get("transformation") != "contain_no_crop"
        ):
            return False

        reference = record.get("storage_reference")
        if not isinstance(reference, str):
            return False

        try:
            object_key = storage.object_key_from_reference(reference)
        except StorageError:
            return False

        expected_key = (
            f"businesses/{asset.business_id}/marketing/uploads/{asset.id}/"
            f"variants/{key}.mp4"
        )
        if object_key != expected_key:
            return False

    return True


def _reduced_aspect_ratio(
    width: int,
    height: int,
) -> str:
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def _is_final_attempt(job: BackgroundJob) -> bool:
    return (
        isinstance(job.attempt_count, int)
        and isinstance(job.max_attempts, int)
        and job.attempt_count >= job.max_attempts
    )
