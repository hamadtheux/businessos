import warnings
from dataclasses import dataclass
from io import BytesIO

from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from app.exceptions.logo import LogoTooLargeError, LogoValidationError


MAX_LOGO_UPLOAD_BYTES = 5_000_000
MAX_LOGO_DIMENSION = 4096
MAX_LOGO_PIXELS = 16_000_000
_READ_CHUNK_BYTES = 64 * 1024
_SUPPORTED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})


@dataclass(frozen=True, slots=True)
class SanitizedLogo:
    content: bytes
    content_type: str
    extension: str
    width: int
    height: int


async def read_and_sanitize_logo(upload: UploadFile) -> SanitizedLogo:
    content = bytearray()
    try:
        while True:
            chunk = await upload.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_LOGO_UPLOAD_BYTES:
                raise LogoTooLargeError("Logo exceeds upload limit")
    except LogoTooLargeError:
        raise
    except (OSError, ValueError):
        raise LogoValidationError("Unable to read logo upload") from None

    if not content:
        raise LogoValidationError("Logo upload is empty")
    return sanitize_logo_bytes(bytes(content))


def sanitize_logo_bytes(content: bytes) -> SanitizedLogo:
    if len(content) > MAX_LOGO_UPLOAD_BYTES:
        raise LogoTooLargeError("Logo exceeds upload limit")
    if not content:
        raise LogoValidationError("Logo upload is empty")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as probe:
                source_format = (probe.format or "").upper()
                if source_format not in _SUPPORTED_FORMATS:
                    raise LogoValidationError("Unsupported logo format")
                _validate_dimensions(*probe.size)
                if getattr(probe, "is_animated", False):
                    raise LogoValidationError("Animated logos are unsupported")
                probe.verify()

            with Image.open(BytesIO(content)) as decoded:
                _validate_dimensions(*decoded.size)
                decoded.load()
                had_palette_transparency = (
                    decoded.mode == "P" and "transparency" in decoded.info
                )
                normalized = ImageOps.exif_transpose(decoded).copy()
    except LogoValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        raise LogoValidationError("Invalid logo image") from None

    normalized.info.clear()
    has_transparency = "A" in normalized.getbands() or (
        normalized.mode == "P" and had_palette_transparency
    )
    target_mode = "RGBA" if has_transparency else "RGB"
    if normalized.mode != target_mode:
        normalized = normalized.convert(target_mode)

    output = BytesIO()
    if source_format == "PNG":
        normalized.save(
            output,
            format="PNG",
            optimize=True,
            compress_level=9,
        )
        content_type = "image/png"
        extension = "png"
    elif source_format == "JPEG":
        normalized.convert("RGB").save(
            output,
            format="JPEG",
            quality=92,
            optimize=True,
            progressive=True,
        )
        content_type = "image/jpeg"
        extension = "jpg"
    else:
        normalized.save(
            output,
            format="WEBP",
            quality=92,
            method=6,
            lossless=has_transparency,
        )
        content_type = "image/webp"
        extension = "webp"

    width, height = normalized.size
    return SanitizedLogo(
        content=output.getvalue(),
        content_type=content_type,
        extension=extension,
        width=width,
        height=height,
    )


def _validate_dimensions(width: int, height: int) -> None:
    if (
        width <= 0
        or height <= 0
        or width > MAX_LOGO_DIMENSION
        or height > MAX_LOGO_DIMENSION
        or width * height > MAX_LOGO_PIXELS
    ):
        raise LogoValidationError("Logo dimensions exceed safe limits")
