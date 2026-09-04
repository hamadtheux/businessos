from __future__ import annotations

import base64
import os
from io import BytesIO
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from PIL import Image

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.services.creative_provider import (  # noqa: E402
    CreativeGenerationRequest,
    CreativeProviderInvalidOutputError,
    CreativeProviderNotConfiguredError,
    OpenAICreativeGenerationProvider,
    UnavailableCreativeGenerationProvider,
    _decode_image,
    _resolve_generation_size,
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

        result = await provider.generate_draft(
            CreativeGenerationRequest(
                business_id=business_id,
                creative_asset_id=creative_asset_id,
                instructions=(
                    "A premium studio product scene with soft directional lighting "
                    "and generous negative space."
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
