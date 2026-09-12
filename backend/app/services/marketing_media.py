from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import UploadFile
from PIL import Image, ImageOps

from app.exceptions.logo import LogoError
from app.exceptions.marketing import MarketingValidationError
from app.services.logo_image import MAX_LOGO_UPLOAD_BYTES, sanitize_logo_bytes


MAX_MARKETING_VIDEO_BYTES = 50_000_000
_READ_CHUNK_BYTES = 64 * 1024
_VIDEO_TYPES = {
    "video/mp4": "mp4",
    "video/webm": "webm",
}


@dataclass(frozen=True, slots=True)
class PreparedMarketingMedia:
    content: bytes | None
    content_type: str
    extension: str
    media_type: str
    width: int | None
    height: int | None
    duration_seconds: int | None
    original_name: str
    source_path: Path | None = None


@dataclass(frozen=True, slots=True)
class PreparedMarketingImageVariant:
    key: str
    content: bytes
    content_type: str
    extension: str
    width: int
    height: int
    aspect_ratio: str


_MARKETING_IMAGE_VARIANTS = (
    ("square_1_1", 1200, 1200, "1:1"),
    ("landscape_1_91_1", 1200, 628, "1.91:1"),
    ("portrait_4_5", 1080, 1350, "4:5"),
    ("vertical_9_16", 1080, 1920, "9:16"),
)


def build_marketing_image_variants(
    media: PreparedMarketingMedia,
) -> tuple[PreparedMarketingImageVariant, ...]:
    """
    Create deterministic platform derivatives without modifying the original.

    We use containment instead of cropping so uploaded artwork, logos and text
    cannot be silently cut off. JPEG derivatives use a neutral white canvas
    for broad advertising-platform compatibility.
    """
    if media.media_type != "image":
        return ()
    if media.content is None:
        raise MarketingValidationError("marketing_media_unreadable")

    try:
        with Image.open(BytesIO(media.content)) as opened:
            source = ImageOps.exif_transpose(opened).convert("RGB")
            source.load()
    except (OSError, ValueError):
        raise MarketingValidationError("marketing_media_unreadable") from None

    variants: list[PreparedMarketingImageVariant] = []

    for key, width, height, aspect_ratio in _MARKETING_IMAGE_VARIANTS:
        rendered = ImageOps.pad(
            source,
            (width, height),
            method=Image.Resampling.LANCZOS,
            color=(255, 255, 255),
            centering=(0.5, 0.5),
        )
        output = BytesIO()
        rendered.save(
            output,
            format="JPEG",
            quality=92,
            optimize=True,
            progressive=True,
        )
        content = output.getvalue()

        if not content:
            raise MarketingValidationError("marketing_media_unreadable")

        variants.append(
            PreparedMarketingImageVariant(
                key=key,
                content=content,
                content_type="image/jpeg",
                extension="jpg",
                width=width,
                height=height,
                aspect_ratio=aspect_ratio,
            )
        )

    return tuple(variants)


async def read_marketing_media(
    upload: UploadFile,
    *,
    duration_seconds: int | None = None,
) -> PreparedMarketingMedia:
    # Accepted only for compatibility with older callers. Browser-derived
    # duration is never authoritative; the background worker obtains it from
    # server-side ffprobe after the immutable source has been persisted.
    del duration_seconds

    declared_type = (upload.content_type or "").split(";", 1)[0].strip().casefold()
    original_name = _safe_original_name(upload.filename)

    video_extension = _VIDEO_TYPES.get(declared_type)
    if video_extension is not None:
        if Path(original_name).suffix.casefold() != f".{video_extension}":
            raise MarketingValidationError("marketing_media_unsupported")

        source_path = await _stream_video_upload_to_file(
            upload,
            extension=video_extension,
        )
        return PreparedMarketingMedia(
            content=None,
            content_type=declared_type,
            extension=video_extension,
            media_type="video",
            width=None,
            height=None,
            duration_seconds=None,
            original_name=original_name,
            source_path=source_path,
        )

    content = await _read_bounded_upload(
        upload,
        max_bytes=MAX_LOGO_UPLOAD_BYTES,
    )
    if declared_type.startswith("image/"):
        try:
            image = sanitize_logo_bytes(content)
        except LogoError:
            raise MarketingValidationError("marketing_media_unsupported") from None
        return PreparedMarketingMedia(
            content=image.content,
            content_type=image.content_type,
            extension=image.extension,
            media_type="image",
            width=image.width,
            height=image.height,
            duration_seconds=None,
            original_name=original_name,
        )

    raise MarketingValidationError("marketing_media_unsupported")


async def cleanup_prepared_marketing_media(
    media: PreparedMarketingMedia,
) -> None:
    """Remove the request-scoped video spool after persistence completes."""
    if media.source_path is None:
        return
    try:
        await asyncio.to_thread(media.source_path.unlink, missing_ok=True)
    except OSError:
        pass


async def _read_bounded_upload(
    upload: UploadFile,
    *,
    max_bytes: int,
) -> bytes:
    content = bytearray()
    try:
        while True:
            chunk = await upload.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > max_bytes:
                raise MarketingValidationError("marketing_media_too_large")
    except MarketingValidationError:
        raise
    except (OSError, ValueError):
        raise MarketingValidationError("marketing_media_unreadable") from None

    if not content:
        raise MarketingValidationError("marketing_media_empty")
    return bytes(content)


async def _stream_video_upload_to_file(
    upload: UploadFile,
    *,
    extension: str,
) -> Path:
    temporary = NamedTemporaryFile(
        mode="w+b",
        prefix="aibos-marketing-video-upload-",
        suffix=f".{extension}",
        delete=False,
    )
    source_path = Path(temporary.name)
    size = 0
    signature = bytearray()
    succeeded = False

    try:
        while True:
            chunk = await upload.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_MARKETING_VIDEO_BYTES:
                raise MarketingValidationError("marketing_media_too_large")
            if len(signature) < 12:
                signature.extend(chunk[: 12 - len(signature)])
            await asyncio.to_thread(temporary.write, chunk)

        if size == 0:
            raise MarketingValidationError("marketing_media_empty")
        if not _valid_video_signature(bytes(signature), extension):
            raise MarketingValidationError("marketing_media_unsupported")

        await asyncio.to_thread(temporary.flush)
        await asyncio.to_thread(temporary.close)
        succeeded = True
        return source_path
    except (OSError, ValueError):
        raise MarketingValidationError("marketing_media_unreadable") from None
    finally:
        if not succeeded:
            try:
                temporary.close()
            except (OSError, ValueError):
                pass
            try:
                source_path.unlink(missing_ok=True)
            except OSError:
                pass


def _safe_original_name(value: str | None) -> str:
    candidate = Path(value or "media").name.strip()
    if not candidate:
        return "media"
    return candidate[:180]


def _valid_video_signature(content: bytes, extension: str) -> bool:
    if extension == "mp4":
        return len(content) >= 12 and content[4:8] == b"ftyp"
    return len(content) >= 4 and content[:4] == b"\x1aE\xdf\xa3"
