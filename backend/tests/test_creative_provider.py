from __future__ import annotations

import base64
import os
from io import BytesIO
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
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
    _resolve_generation_size,
)


def _png_bytes(width: int = 1024, height: int = 1024) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


class _FakeStorage:
    def __init__(self) -> None:
        self.put = AsyncMock()
        self.public_keys: list[str] = []

    async def delete(self, object_key: str) -> None:
        del object_key

    def public_url(self, object_key: str) -> str:
        self.public_keys.append(object_key)
        return f"https://media.example.com/{object_key}"


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

    async def test_openai_provider_generates_validates_and_stores_png(self) -> None:
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
        storage = _FakeStorage()
        provider = OpenAICreativeGenerationProvider(
            client=client,
            storage=storage,
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
        self.assertTrue(
            result.storage_reference.startswith(
                "https://media.example.com/businesses/"
                f"{business_id}/marketing/creatives/{creative_asset_id}/"
            )
        )
        self.assertTrue(result.storage_reference.endswith(".png"))

        generate = client.images.generate
        generate.assert_awaited_once()
        kwargs = generate.await_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-image-2")
        self.assertEqual(kwargs["quality"], "medium")
        self.assertEqual(kwargs["size"], "1024x1024")
        self.assertEqual(kwargs["n"], 1)
        self.assertIn("Do not render logos", kwargs["prompt"])
        self.assertIn(
            "deterministic brand composition layer",
            kwargs["prompt"],
        )

        storage.put.assert_awaited_once()
        object_key, stored_bytes, content_type = storage.put.await_args.args
        self.assertIn(f"businesses/{business_id}/marketing/creatives/", object_key)
        self.assertIn(str(creative_asset_id), object_key)
        self.assertEqual(stored_bytes, image_bytes)
        self.assertEqual(content_type, "image/png")

    async def test_provider_rejects_malformed_base64_before_storage(self) -> None:
        response = SimpleNamespace(
            data=[SimpleNamespace(b64_json="not valid base64%%%")],
            _request_id="req_bad",
        )
        client = SimpleNamespace(
            images=SimpleNamespace(
                generate=AsyncMock(return_value=response),
            )
        )
        storage = _FakeStorage()
        provider = OpenAICreativeGenerationProvider(
            client=client,
            storage=storage,
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

        storage.put.assert_not_awaited()

    async def test_provider_rejects_non_image_bytes_before_storage(self) -> None:
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
        storage = _FakeStorage()
        provider = OpenAICreativeGenerationProvider(
            client=client,
            storage=storage,
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

        storage.put.assert_not_awaited()

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
