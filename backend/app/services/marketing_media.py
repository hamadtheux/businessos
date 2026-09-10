from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

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
    content: bytes
    content_type: str
    extension: str
    media_type: str
    width: int | None
    height: int | None
    duration_seconds: int | None
    original_name: str


async def read_marketing_media(
    upload: UploadFile,
    *,
    duration_seconds: int | None,
) -> PreparedMarketingMedia:
    declared_type = (upload.content_type or "").split(";", 1)[0].strip().casefold()
    max_bytes = (
        MAX_MARKETING_VIDEO_BYTES
        if declared_type in _VIDEO_TYPES
        else MAX_LOGO_UPLOAD_BYTES
    )
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

    original_name = _safe_original_name(upload.filename)
    if declared_type.startswith("image/"):
        try:
            image = sanitize_logo_bytes(bytes(content))
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

    extension = _VIDEO_TYPES.get(declared_type)
    if extension is None or Path(original_name).suffix.casefold() != f".{extension}":
        raise MarketingValidationError("marketing_media_unsupported")
    if duration_seconds is None or not 1 <= duration_seconds <= 3600:
        raise MarketingValidationError("marketing_video_duration_required")
    if not _valid_video_signature(bytes(content), extension):
        raise MarketingValidationError("marketing_media_unsupported")
    return PreparedMarketingMedia(
        content=bytes(content),
        content_type=declared_type,
        extension=extension,
        media_type="video",
        width=None,
        height=None,
        duration_seconds=duration_seconds,
        original_name=original_name,
    )


def _safe_original_name(value: str | None) -> str:
    candidate = Path(value or "media").name.strip()
    if not candidate:
        return "media"
    return candidate[:180]


def _valid_video_signature(content: bytes, extension: str) -> bool:
    if extension == "mp4":
        return len(content) >= 12 and content[4:8] == b"ftyp"
    return len(content) >= 4 and content[:4] == b"\x1aE\xdf\xa3"
