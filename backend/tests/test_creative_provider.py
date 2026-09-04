from __future__ import annotations

import base64
import os
from io import BytesIO
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from openai import APITimeoutError, OpenAIError
from PIL import Image

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.core.config import Settings  # noqa: E402
from app.services.creative_provider import (  # noqa: E402
    CreativeGenerationRequest,
    CreativeProviderGenerationError,
    CreativeProviderInvalidOutputError,
    CreativeProviderNotConfiguredError,
    OpenAICreativeGenerationProvider,
    UnavailableCreativeGenerationProvider,
    _decode_image,
    _resolve_generation_size,
    create_creative_generation_provider,
)


def _png_bytes(width: int = 1024, height: int = 1024) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


class CreativeProviderTests(IsolatedAsyncioTestCase):
    async def test_unavailable_provider_remains_fail_closed(self) -> None:
        provider = UnavailableCreativeGenerationProvider()

        with self.assertRaises(CreativeProviderNotConfiguredError):
            await provider.generate_draft(
                CreativeGenerationRequest(
                    business_id=uuid4(),
                    creative_asset_id=uuid4(),
                    instructions="Premium product scene",
                    width=1024,
                    height=1024,
                    aspect_ratio="1:1",
                )
            )

    async def test_openai_provider_returns_transient_validated_png(self) -> None:
        business_id = uuid4()
        creative_asset_id = uuid4()
        image_bytes = _png_bytes()
        response = SimpleNamespace(
            data=[
                SimpleNamespace(
                    b64_json=base64.b64encode(image_bytes).decode("ascii")
                )
            ],
            _request_id="req_creative_123",
        )

        client = SimpleNamespace(
            images=SimpleNamespace(
                generate=AsyncMock(return_value=response),
            )
        )
        provider = OpenAICreativeGenerationProvider(
            client=client,
        )

        with self.assertLogs("aibos.creative_provider", level="INFO") as captured:
            result = await provider.generate_draft(
                CreativeGenerationRequest(
                    business_id=business_id,
                    creative_asset_id=creative_asset_id,
                    instructions=(
                        "A premium studio product scene with soft directional "
                        "lighting and generous negative space."
                    ),
                    width=1024,
                    height=1024,
                    aspect_ratio="1:1",
                )
            )

        self.assertEqual(result.width, 1024)
        self.assertEqual(result.height, 1024)
        self.assertEqual(result.provider_request_id, "req_creative_123")
        self.assertEqual(result.content, image_bytes)
        self.assertFalse(hasattr(result, "storage_reference"))

        generate = client.images.generate
        generate.assert_awaited_once()
        kwargs = generate.await_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-image-2")
        self.assertEqual(kwargs["quality"], "medium")
        self.assertEqual(kwargs["size"], "1024x1024")
        self.assertEqual(kwargs["output_format"], "png")
        self.assertEqual(kwargs["n"], 1)
        self.assertIn("Do not render logos", kwargs["prompt"])
        self.assertIn(
            "deterministic brand composition layer",
            kwargs["prompt"],
        )

        success_log = captured.records[-1]
        self.assertEqual(
            success_log.getMessage(),
            "creative_image_provider_succeeded provider=openai "
            "model=gpt-image-2 quality=medium size=1024x1024 "
            "width=1024 height=1024 request_id=req_creative_123",
        )
        self.assertEqual(success_log.provider, "openai")
        self.assertEqual(success_log.model, "gpt-image-2")
        self.assertEqual(success_log.quality, "medium")
        self.assertEqual(success_log.size, "1024x1024")
        self.assertEqual(success_log.validated_width, 1024)
        self.assertEqual(success_log.validated_height, 1024)
        self.assertEqual(success_log.provider_request_id, "req_creative_123")

    async def test_openai_provider_timeout_raises_safe_error(self) -> None:
        provider_error = APITimeoutError(
            request=SimpleNamespace(method="POST", url="provider.invalid")
        )
        client = SimpleNamespace(
            images=SimpleNamespace(
                generate=AsyncMock(side_effect=provider_error),
            )
        )
        provider = OpenAICreativeGenerationProvider(client=client)

        with self.assertLogs("aibos.creative_provider", level="WARNING") as captured:
            with self.assertRaises(CreativeProviderGenerationError) as raised:
                await provider.generate_draft(
                    CreativeGenerationRequest(
                        business_id=uuid4(),
                        creative_asset_id=uuid4(),
                        instructions="Sensitive campaign direction",
                        width=1024,
                        height=1024,
                        aspect_ratio="1:1",
                    )
                )

        self.assertEqual(
            str(raised.exception),
            "Creative image provider could not complete the request",
        )
        failure_log = captured.records[-1]
        self.assertEqual(
            failure_log.getMessage(),
            "creative_image_provider_failed provider=openai "
            "exception_type=APITimeoutError model=gpt-image-2 quality=medium "
            "size=1024x1024 status_code=none request_id=none "
            "error_code=none error_type=none",
        )
        self.assertEqual(failure_log.exception_type, "APITimeoutError")
        self.assertEqual(failure_log.provider, "openai")
        self.assertEqual(failure_log.model, "gpt-image-2")
        self.assertEqual(failure_log.quality, "medium")
        self.assertEqual(failure_log.size, "1024x1024")

    async def test_provider_failure_logs_only_safe_scalar_metadata(self) -> None:
        provider_error = OpenAIError(
            "raw provider body with sk-proj-sensitive-test-value"
        )
        provider_error.status_code = 429
        provider_error.request_id = "req_safe_123"
        provider_error.code = "rate_limit_exceeded"
        provider_error.type = "rate_limit_error"
        client = SimpleNamespace(
            images=SimpleNamespace(
                generate=AsyncMock(side_effect=provider_error),
            )
        )
        provider = OpenAICreativeGenerationProvider(client=client)

        with self.assertLogs("aibos.creative_provider", level="WARNING") as captured:
            with self.assertRaises(CreativeProviderGenerationError) as raised:
                await provider.generate_draft(
                    CreativeGenerationRequest(
                        business_id=uuid4(),
                        creative_asset_id=uuid4(),
                        instructions="Sensitive launch plan",
                        width=1024,
                        height=1024,
                        aspect_ratio=None,
                    )
                )

        self.assertEqual(
            str(raised.exception),
            "Creative image provider could not complete the request",
        )
        failure_log = captured.records[-1]
        failure_message = failure_log.getMessage()
        self.assertEqual(
            failure_message,
            "creative_image_provider_failed provider=openai "
            "exception_type=OpenAIError model=gpt-image-2 quality=medium "
            "size=1024x1024 status_code=429 request_id=req_safe_123 "
            "error_code=rate_limit_exceeded error_type=rate_limit_error",
        )
        self.assertEqual(failure_log.status_code, 429)
        self.assertEqual(failure_log.provider_request_id, "req_safe_123")
        self.assertEqual(failure_log.provider_error_code, "rate_limit_exceeded")
        self.assertEqual(failure_log.provider_error_type, "rate_limit_error")
        self.assertNotIn("raw provider body", failure_message)
        self.assertNotIn("sk-proj-sensitive-test-value", failure_message)
        self.assertNotIn("Sensitive launch plan", failure_message)

    async def test_provider_failure_omits_unsafe_identifiers_from_message(
        self,
    ) -> None:
        provider_error = OpenAIError("raw provider diagnostic")
        provider_error.status_code = 400
        provider_error.request_id = "unsafe request\nidentifier"
        provider_error.code = "invalid value"
        provider_error.type = "invalid\trequest"
        client = SimpleNamespace(
            images=SimpleNamespace(
                generate=AsyncMock(side_effect=provider_error),
            )
        )
        provider = OpenAICreativeGenerationProvider(client=client)

        with self.assertLogs("aibos.creative_provider", level="WARNING") as captured:
            with self.assertRaises(CreativeProviderGenerationError):
                await provider.generate_draft(
                    CreativeGenerationRequest(
                        business_id=uuid4(),
                        creative_asset_id=uuid4(),
                        instructions="Private campaign instructions",
                        width=1024,
                        height=1024,
                        aspect_ratio=None,
                    )
                )

        failure_log = captured.records[-1]
        failure_message = failure_log.getMessage()
        self.assertIn("status_code=400", failure_message)
        self.assertIn("request_id=none", failure_message)
        self.assertIn("error_code=none", failure_message)
        self.assertIn("error_type=none", failure_message)
        self.assertNotIn("unsafe request", failure_message)
        self.assertNotIn("invalid value", failure_message)
        self.assertNotIn("invalid\trequest", failure_message)
        self.assertNotIn("Private campaign instructions", failure_message)
        self.assertIsNone(failure_log.provider_request_id)
        self.assertIsNone(failure_log.provider_error_code)
        self.assertIsNone(failure_log.provider_error_type)

    def test_image_provider_uses_dedicated_timeout(self) -> None:
        config = SimpleNamespace(
            openai_api_key_value="test-provider-key",
            openai_timeout_seconds=45.0,
            openai_image_timeout_seconds=237.0,
            openai_max_retries=2,
            openai_image_model="gpt-image-2",
            openai_image_quality="medium",
        )

        with patch("app.services.creative_provider.AsyncOpenAI") as client_class:
            provider = create_creative_generation_provider(config)

        client_class.assert_called_once_with(
            api_key="test-provider-key",
            timeout=237.0,
            max_retries=2,
        )
        self.assertIsInstance(provider, OpenAICreativeGenerationProvider)
        self.assertEqual(provider.model, "gpt-image-2")
        self.assertEqual(provider.quality, "medium")

    def test_default_image_timeout_is_180_seconds(self) -> None:
        timeout_field = Settings.model_fields["openai_image_timeout_seconds"]

        self.assertEqual(timeout_field.default, 180.0)

    async def test_provider_rejects_malformed_base64(self) -> None:
        response = SimpleNamespace(
            data=[SimpleNamespace(b64_json="not valid base64%%%")],
            _request_id="req_bad",
        )
        client = SimpleNamespace(
            images=SimpleNamespace(
                generate=AsyncMock(return_value=response),
            )
        )
        provider = OpenAICreativeGenerationProvider(
            client=client,
        )

        with self.assertRaises(CreativeProviderInvalidOutputError):
            await provider.generate_draft(
                CreativeGenerationRequest(
                    business_id=uuid4(),
                    creative_asset_id=uuid4(),
                    instructions="A clean commercial product scene.",
                    width=1024,
                    height=1024,
                    aspect_ratio=None,
                )
            )

    async def test_provider_rejects_non_image_bytes(self) -> None:
        response = SimpleNamespace(
            data=[
                SimpleNamespace(
                    b64_json=base64.b64encode(b"not-an-image").decode("ascii")
                )
            ],
            _request_id=None,
        )
        client = SimpleNamespace(
            images=SimpleNamespace(
                generate=AsyncMock(return_value=response),
            )
        )
        provider = OpenAICreativeGenerationProvider(
            client=client,
        )

        with self.assertRaises(CreativeProviderInvalidOutputError):
            await provider.generate_draft(
                CreativeGenerationRequest(
                    business_id=uuid4(),
                    creative_asset_id=uuid4(),
                    instructions="A polished commercial visual.",
                    width=None,
                    height=None,
                    aspect_ratio="1:1",
                )
            )

    async def test_provider_rejects_unexpected_image_format(self) -> None:
        output = BytesIO()
        Image.new("RGB", (1024, 1024), "white").save(output, format="JPEG")
        response = SimpleNamespace(
            data=[
                SimpleNamespace(
                    b64_json=base64.b64encode(output.getvalue()).decode("ascii")
                )
            ],
            _request_id="req_jpeg",
        )
        client = SimpleNamespace(
            images=SimpleNamespace(generate=AsyncMock(return_value=response))
        )
        provider = OpenAICreativeGenerationProvider(client=client)

        with self.assertRaises(CreativeProviderInvalidOutputError):
            await provider.generate_draft(
                CreativeGenerationRequest(
                    business_id=uuid4(),
                    creative_asset_id=uuid4(),
                    instructions="A clean commercial product scene.",
                    width=1024,
                    height=1024,
                    aspect_ratio=None,
                )
            )

    def test_provider_rejects_oversized_decoded_output(self) -> None:
        encoded = base64.b64encode(b"x" * 33).decode("ascii")
        with patch("app.services.creative_provider._MAX_GENERATED_IMAGE_BYTES", 32):
            with self.assertRaises(CreativeProviderInvalidOutputError):
                _decode_image(encoded)

    def test_generation_size_normalizes_social_dimensions_for_provider(self) -> None:
        request = CreativeGenerationRequest(
            business_id=uuid4(),
            creative_asset_id=uuid4(),
            instructions="Instagram portrait creative.",
            width=1080,
            height=1350,
            aspect_ratio="4:5",
        )

        size = _resolve_generation_size(request)
        width, height = [int(value) for value in size.split("x")]

        self.assertEqual(width % 16, 0)
        self.assertEqual(height % 16, 0)
        self.assertGreater(height, width)
        self.assertLessEqual(max(width, height), 3840)

    def test_generation_size_supports_ratio_only_requests(self) -> None:
        request = CreativeGenerationRequest(
            business_id=uuid4(),
            creative_asset_id=uuid4(),
            instructions="Vertical story visual.",
            width=None,
            height=None,
            aspect_ratio="9:16",
        )

        size = _resolve_generation_size(request)
        width, height = [int(value) for value in size.split("x")]

        self.assertGreater(height, width)
        self.assertEqual(width % 16, 0)
        self.assertEqual(height % 16, 0)

    def test_generation_size_preserves_valid_extreme_ratio_at_minimum_area(self) -> None:
        request = CreativeGenerationRequest(
            business_id=uuid4(),
            creative_asset_id=uuid4(),
            instructions="Narrow final banner visual.",
            width=320,
            height=960,
            aspect_ratio="1:3",
        )

        size = _resolve_generation_size(request)
        width, height = [int(value) for value in size.split("x")]

        self.assertGreaterEqual(width * height, 655_360)
        self.assertLessEqual(max(width, height) / min(width, height), 3)
        self.assertEqual(width % 16, 0)
        self.assertEqual(height % 16, 0)
