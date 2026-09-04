from __future__ import annotations

import base64
import logging
import re
import warnings
from dataclasses import dataclass
from io import BytesIO
from math import ceil
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from openai import AsyncOpenAI, OpenAIError
from PIL import Image, UnidentifiedImageError

from app.core.config import Settings


DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-2"
DEFAULT_CREATIVE_QUALITY: Literal["low", "medium", "high", "auto"] = "medium"

logger = logging.getLogger("aibos.creative_provider")

_MAX_GENERATED_IMAGE_BYTES = 30 * 1024 * 1024
# GPT-Image-2 current Image API constraints.
_MIN_IMAGE_PIXELS = 655_360
_MAX_IMAGE_PIXELS = 8_294_400
_MAX_IMAGE_EDGE = 3840
_MAX_EDGE_RATIO = 3.0
_SAFE_PROVIDER_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass(frozen=True, slots=True)
class CreativeGenerationRequest:
    """
    Provider-neutral request for an internal draft creative.

    The generated provider asset is deliberately only the visual layer.
    Exact brand marks, marketing copy, CTA text, and other deterministic
    overlays are composed by the application in a later stage.
    """

    business_id: UUID
    creative_asset_id: UUID
    instructions: str
    width: int | None
    height: int | None
    aspect_ratio: str | None

    def __post_init__(self) -> None:
        instructions = self.instructions.strip()
        if not instructions or len(instructions) > 5000:
            raise ValueError("instructions must contain 1 to 5000 characters")

        if (self.width is None) != (self.height is None):
            raise ValueError("width and height must be supplied together")

        if self.width is not None and not 1 <= self.width <= 20000:
            raise ValueError("width must be between 1 and 20000")

        if self.height is not None and not 1 <= self.height <= 20000:
            raise ValueError("height must be between 1 and 20000")

        if self.aspect_ratio is not None:
            ratio = self.aspect_ratio.strip()
            if not ratio or len(ratio) > 16:
                raise ValueError("aspect_ratio must contain 1 to 16 characters")


@dataclass(frozen=True, slots=True)
class CreativeGenerationResult:
    """Transient validated visual bytes; callers must never persist them in SQL."""

    content: bytes
    width: int
    height: int
    provider_request_id: str | None = None


@runtime_checkable
class CreativeGenerationProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    async def generate_draft(
        self,
        request: CreativeGenerationRequest,
    ) -> CreativeGenerationResult: ...


class CreativeProviderError(RuntimeError):
    """Safe base error for creative-generation failures."""


class CreativeProviderNotConfiguredError(CreativeProviderError):
    pass


class CreativeProviderGenerationError(CreativeProviderError):
    """The external image provider could not return a usable draft."""


class CreativeProviderInvalidOutputError(CreativeProviderError):
    """The provider returned malformed or unsafe image output."""


class UnavailableCreativeGenerationProvider:
    """Intentional default: disabled creative generation is explicit and testable."""

    provider_name = "unconfigured"

    async def generate_draft(
        self,
        request: CreativeGenerationRequest,
    ) -> CreativeGenerationResult:
        del request
        raise CreativeProviderNotConfiguredError(
            "Creative generation provider is not configured"
        )


class OpenAICreativeGenerationProvider:
    """
    Production GPT-Image provider for raw marketing visual generation.

    Responsibilities:
    - call the server-side OpenAI Image API;
    - prohibit generated overlay copy / invented branding in the prompt;
    - decode and validate returned image bytes;
    - return only transient visual bytes and verified dimensions.

    It intentionally does NOT compose the final logo, headline, CTA, or other
    precise brand elements. Those belong to the deterministic composition layer.
    """

    provider_name = "openai"

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str = DEFAULT_OPENAI_IMAGE_MODEL,
        quality: Literal["low", "medium", "high", "auto"] = DEFAULT_CREATIVE_QUALITY,
    ) -> None:
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("Creative image model cannot be blank")

        self.client = client
        self.model = normalized_model
        self.quality = quality

    async def generate_draft(
        self,
        request: CreativeGenerationRequest,
    ) -> CreativeGenerationResult:
        size = _resolve_generation_size(request)
        prompt = _build_visual_prompt(request)

        try:
            response = await self.client.images.generate(
                model=self.model,
                prompt=prompt,
                size=size,
                quality=self.quality,
                output_format="png",
                n=1,
            )
        except OpenAIError as exc:
            failure_metadata: dict[str, str | int | None] = {
                "provider": _safe_provider_identifier(self.provider_name),
                "exception_type": _safe_provider_identifier(type(exc).__name__),
                "model": _safe_provider_identifier(self.model),
                "quality": _safe_provider_identifier(self.quality),
                "size": _safe_provider_identifier(size),
                "status_code": _safe_http_status_code(exc),
                "provider_request_id": _safe_provider_exception_identifier(
                    exc,
                    "request_id",
                ),
                "provider_error_code": _safe_provider_exception_identifier(
                    exc,
                    "code",
                ),
                "provider_error_type": _safe_provider_exception_identifier(
                    exc,
                    "type",
                ),
            }
            logger.warning(
                _format_safe_provider_log_message(
                    "creative_image_provider_failed",
                    (
                        ("provider", failure_metadata["provider"]),
                        ("exception_type", failure_metadata["exception_type"]),
                        ("model", failure_metadata["model"]),
                        ("quality", failure_metadata["quality"]),
                        ("size", failure_metadata["size"]),
                        ("status_code", failure_metadata["status_code"]),
                        ("request_id", failure_metadata["provider_request_id"]),
                        ("error_code", failure_metadata["provider_error_code"]),
                        ("error_type", failure_metadata["provider_error_type"]),
                    ),
                ),
                extra=failure_metadata,
            )
            raise CreativeProviderGenerationError(
                "Creative image provider could not complete the request"
            ) from None

        encoded = _extract_base64_image(response)
        image_bytes = _decode_image(encoded)
        width, height = _validate_generated_png(image_bytes)

        request_id = getattr(response, "_request_id", None)
        if not isinstance(request_id, str) or not request_id.strip():
            request_id = None

        success_metadata: dict[str, str | int | None] = {
            "provider": _safe_provider_identifier(self.provider_name),
            "model": _safe_provider_identifier(self.model),
            "quality": _safe_provider_identifier(self.quality),
            "size": _safe_provider_identifier(size),
            "validated_width": width,
            "validated_height": height,
            "provider_request_id": _safe_provider_identifier(request_id),
        }
        logger.info(
            _format_safe_provider_log_message(
                "creative_image_provider_succeeded",
                (
                    ("provider", success_metadata["provider"]),
                    ("model", success_metadata["model"]),
                    ("quality", success_metadata["quality"]),
                    ("size", success_metadata["size"]),
                    ("width", success_metadata["validated_width"]),
                    ("height", success_metadata["validated_height"]),
                    ("request_id", success_metadata["provider_request_id"]),
                ),
            ),
            extra=success_metadata,
        )

        return CreativeGenerationResult(
            content=image_bytes,
            width=width,
            height=height,
            provider_request_id=request_id,
        )


def _safe_provider_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not _SAFE_PROVIDER_IDENTIFIER.fullmatch(normalized):
        return None

    return normalized


def _format_safe_provider_log_message(
    event: str,
    fields: tuple[tuple[str, str | int | None], ...],
) -> str:
    rendered_fields = (
        f"{name}={value if value is not None else 'none'}"
        for name, value in fields
    )
    return " ".join((event, *rendered_fields))


def _safe_provider_exception_attribute(
    exception: OpenAIError,
    attribute: str,
) -> object | None:
    try:
        return getattr(exception, attribute, None)
    except Exception:
        # Diagnostics must never replace the original safe provider failure.
        return None


def _safe_provider_exception_identifier(
    exception: OpenAIError,
    attribute: str,
) -> str | None:
    return _safe_provider_identifier(
        _safe_provider_exception_attribute(exception, attribute)
    )


def _safe_http_status_code(exception: OpenAIError) -> int | None:
    status_code = _safe_provider_exception_attribute(exception, "status_code")
    if (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 100 <= status_code <= 599
    ):
        return status_code

    return None


def _build_visual_prompt(request: CreativeGenerationRequest) -> str:
    return (
        "Create one polished commercial marketing VISUAL LAYER based only on "
        "the following authorized creative direction.\n\n"
        f"{request.instructions.strip()}\n\n"
        "Important production constraints:\n"
        "- Generate the photographic, illustrative, environmental, product-scene, "
        "or abstract visual only.\n"
        "- Do not render logos, brand marks, watermarks, UI chrome, captions, "
        "headlines, CTA text, typography, letters, or invented packaging copy.\n"
        "- Do not invent certifications, awards, prices, claims, testimonials, "
        "people, products, or business facts not present in the creative direction.\n"
        "- Keep useful negative space for a later deterministic brand composition layer.\n"
        "- The application will place the real logo and exact marketing copy afterward."
    )


def _resolve_generation_size(request: CreativeGenerationRequest) -> str:
    if request.width is not None and request.height is not None:
        target_width = request.width
        target_height = request.height
    elif request.aspect_ratio:
        target_width, target_height = _dimensions_from_ratio(request.aspect_ratio)
    else:
        return "1024x1024"

    width, height = _normalize_image_api_dimensions(
        target_width,
        target_height,
    )
    return f"{width}x{height}"


def _dimensions_from_ratio(value: str) -> tuple[int, int]:
    normalized = value.strip()

    try:
        left, right = normalized.split(":", 1)
        ratio_width = float(left)
        ratio_height = float(right)
    except (ValueError, TypeError):
        raise ValueError("aspect_ratio must use WIDTH:HEIGHT format") from None

    if ratio_width <= 0 or ratio_height <= 0:
        raise ValueError("aspect_ratio values must be positive")

    ratio = ratio_width / ratio_height
    if ratio > _MAX_EDGE_RATIO or ratio < 1 / _MAX_EDGE_RATIO:
        raise ValueError("aspect_ratio exceeds the image provider limit")

    short_edge = 1024

    if ratio >= 1:
        width = round((short_edge * ratio) / 16) * 16
        height = short_edge
    else:
        width = short_edge
        height = round((short_edge / ratio) / 16) * 16

    return width, height


def _normalize_image_api_dimensions(
    width: int,
    height: int,
) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("creative dimensions must be positive")

    ratio = max(width, height) / min(width, height)
    if ratio > _MAX_EDGE_RATIO:
        raise ValueError("creative dimensions exceed the image provider aspect limit")

    normalized_width = max(16, round(width / 16) * 16)
    normalized_height = max(16, round(height / 16) * 16)

    longest = max(normalized_width, normalized_height)
    if longest > _MAX_IMAGE_EDGE:
        scale = _MAX_IMAGE_EDGE / longest
        normalized_width = max(
            16,
            int((normalized_width * scale) // 16) * 16,
        )
        normalized_height = max(
            16,
            int((normalized_height * scale) // 16) * 16,
        )

    pixels = normalized_width * normalized_height

    if pixels < _MIN_IMAGE_PIXELS:
        scale = (_MIN_IMAGE_PIXELS / pixels) ** 0.5
        normalized_width = max(
            16,
            ceil((normalized_width * scale) / 16) * 16,
        )
        normalized_height = max(
            16,
            ceil((normalized_height * scale) / 16) * 16,
        )

    pixels = normalized_width * normalized_height
    if pixels > _MAX_IMAGE_PIXELS:
        scale = (_MAX_IMAGE_PIXELS / pixels) ** 0.5
        normalized_width = max(
            16,
            int((normalized_width * scale) // 16) * 16,
        )
        normalized_height = max(
            16,
            int((normalized_height * scale) // 16) * 16,
        )

    if (
        normalized_width > _MAX_IMAGE_EDGE
        or normalized_height > _MAX_IMAGE_EDGE
        or normalized_width % 16
        or normalized_height % 16
        or max(normalized_width, normalized_height)
        / min(normalized_width, normalized_height)
        > _MAX_EDGE_RATIO
    ):
        raise ValueError("creative dimensions cannot be normalized safely")

    pixels = normalized_width * normalized_height
    if not _MIN_IMAGE_PIXELS <= pixels <= _MAX_IMAGE_PIXELS:
        raise ValueError("creative dimensions are outside image provider limits")

    return normalized_width, normalized_height


def _extract_base64_image(response: object) -> str:
    data = getattr(response, "data", None)

    if not isinstance(data, list) or not data:
        raise CreativeProviderInvalidOutputError(
            "Creative image provider returned no image"
        )

    encoded = getattr(data[0], "b64_json", None)

    if not isinstance(encoded, str) or not encoded:
        raise CreativeProviderInvalidOutputError(
            "Creative image provider returned invalid image data"
        )

    return encoded


def _decode_image(encoded: str) -> bytes:
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise CreativeProviderInvalidOutputError(
            "Creative image provider returned malformed image data"
        ) from None

    if not content or len(content) > _MAX_GENERATED_IMAGE_BYTES:
        raise CreativeProviderInvalidOutputError(
            "Creative image provider returned an invalid image size"
        )

    return content


def _validate_generated_png(content: bytes) -> tuple[int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                width, height = image.size
                image_format = image.format
                image.verify()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ):
        raise CreativeProviderInvalidOutputError(
            "Creative image provider returned an unreadable image"
        ) from None

    if image_format != "PNG":
        raise CreativeProviderInvalidOutputError(
            "Creative image provider returned an unexpected image format"
        )

    if (
        width < 1
        or height < 1
        or width > _MAX_IMAGE_EDGE
        or height > _MAX_IMAGE_EDGE
        or width * height > _MAX_IMAGE_PIXELS
    ):
        raise CreativeProviderInvalidOutputError(
            "Creative image provider returned unsupported dimensions"
        )

    return width, height


def create_creative_generation_provider(
    config: Settings,
) -> CreativeGenerationProvider:
    """
    Build the production image provider from backend-only configuration.

    Unlike the text-agent dependency, absence of an API key intentionally
    returns an explicit unavailable provider. This lets the marketing service
    persist generation_status='provider_required' rather than losing that
    truthful state to an HTTP transaction rollback.
    """
    api_key = config.openai_api_key_value

    if api_key is None or not api_key.strip():
        return UnavailableCreativeGenerationProvider()

    client = AsyncOpenAI(
        api_key=api_key,
        timeout=config.openai_image_timeout_seconds,
        max_retries=config.openai_max_retries,
    )

    return OpenAICreativeGenerationProvider(
        client=client,
        model=config.openai_image_model,
        quality=config.openai_image_quality,
    )
