from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.marketing import MarketingValidationError  # noqa: E402
from app.services.marketing_video import (  # noqa: E402
    ProbedMarketingVideo,
    RenderedMarketingVideoVariant,
)
from app.services.marketing_video_preparation import (  # noqa: E402
    prepare_marketing_video_derivatives,
)
from app.storage.base import StorageOperationError  # noqa: E402


class _Storage:
    def __init__(
        self,
        *,
        business_id,
        asset_id,
        extension: str = "mp4",
        fail_put_key: str | None = None,
    ) -> None:
        self.business_id = business_id
        self.asset_id = asset_id
        self.extension = extension
        self.fail_put_key = fail_put_key

        self.source_key = (
            f"businesses/{business_id}/marketing/uploads/{asset_id}/"
            f"source.{extension}"
        )
        self.get_file_calls: list[str] = []
        self.put_file_calls: list[str] = []
        self.put_file_content_types: list[str] = []
        self.deleted: list[str] = []

    def public_url(self, object_key: str) -> str:
        return f"https://cdn.example.test/{object_key}"

    def object_key_from_reference(
        self,
        storage_reference: str,
    ) -> str:
        prefix = "https://cdn.example.test/"
        if not storage_reference.startswith(prefix):
            raise StorageOperationError(
                "Invalid object storage reference"
            )
        return storage_reference[len(prefix):]

    async def get_file(
        self,
        object_key: str,
        destination_path: Path,
        *,
        max_bytes: int,
    ) -> None:
        del max_bytes
        self.get_file_calls.append(object_key)

        destination_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        destination_path.write_bytes(
            b"trusted-immutable-source"
        )

    async def put_file(
        self,
        object_key: str,
        source_path: Path,
        content_type: str,
        *,
        max_bytes: int,
    ) -> None:
        del max_bytes

        self.put_file_calls.append(object_key)
        self.put_file_content_types.append(content_type)

        if self.fail_put_key and object_key.endswith(
            self.fail_put_key
        ):
            raise StorageOperationError(
                "simulated storage failure"
            )

        if not source_path.is_file():
            raise AssertionError(
                "renderer output was not file-backed"
            )

    async def delete(self, object_key: str) -> None:
        self.deleted.append(object_key)


def _probe(
    *,
    width: int,
    height: int,
    duration: int = 30,
    video_codec: str = "h264",
    audio_codec: str | None = "aac",
    format_name: str = "mov,mp4,m4a,3gp,3g2,mj2",
) -> ProbedMarketingVideo:
    return ProbedMarketingVideo(
        width=width,
        height=height,
        duration_seconds=duration,
        video_codec=video_codec,
        audio_codec=audio_codec,
        format_name=format_name,
    )


async def _fake_render(
    source_path: Path,
    output_directory: Path,
    *,
    key: str,
    width: int,
    height: int,
    aspect_ratio: str,
    duration_seconds: int,
) -> RenderedMarketingVideoVariant:
    if not source_path.is_file():
        raise AssertionError("source was not downloaded to disk")

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    output_path = output_directory / f"{key}.mp4"
    output_path.write_bytes(b"normalized-video")

    return RenderedMarketingVideoVariant(
        key=key,
        path=output_path,
        content_type="video/mp4",
        extension="mp4",
        width=width,
        height=height,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
        transformation="contain_no_crop",
    )


class MarketingVideoPreparationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_prepares_both_verified_derivatives(self) -> None:
        business_id = uuid4()
        asset_id = uuid4()
        storage = _Storage(
            business_id=business_id,
            asset_id=asset_id,
        )

        source_reference = storage.public_url(
            storage.source_key
        )

        probes = [
            _probe(width=720, height=1280),
            _probe(width=1080, height=1920),
            _probe(width=1920, height=1080),
        ]

        with (
            patch(
                "app.services.marketing_video_preparation."
                "probe_marketing_video_file",
                new=AsyncMock(side_effect=probes),
            ),
            patch(
                "app.services.marketing_video_preparation."
                "render_marketing_video_variant",
                new=AsyncMock(side_effect=_fake_render),
            ),
        ):
            result = await prepare_marketing_video_derivatives(
                storage=storage,
                business_id=business_id,
                asset_id=asset_id,
                source_reference=source_reference,
                source_extension="mp4",
            )

        self.assertEqual(result.width, 720)
        self.assertEqual(result.height, 1280)
        self.assertEqual(result.duration_seconds, 30)

        self.assertEqual(
            [item.key for item in result.variants],
            [
                "vertical_9_16",
                "landscape_16_9",
            ],
        )

        self.assertEqual(
            storage.get_file_calls,
            [storage.source_key],
        )

        self.assertEqual(
            len(storage.put_file_calls),
            2,
        )

        vertical = result.variants[0]
        self.assertEqual(
            (
                vertical.width,
                vertical.height,
                vertical.aspect_ratio,
                vertical.video_codec,
                vertical.audio_codec,
                vertical.transformation,
            ),
            (
                1080,
                1920,
                "9:16",
                "h264",
                "aac",
                "contain_no_crop",
            ),
        )

        self.assertEqual(storage.deleted, [])

    async def test_mov_source_is_prepared_into_trusted_mp4_derivatives(
        self,
    ) -> None:
        business_id = uuid4()
        asset_id = uuid4()
        storage = _Storage(
            business_id=business_id,
            asset_id=asset_id,
            extension="mov",
        )
        probes = [
            _probe(width=720, height=1280),
            _probe(width=1080, height=1920),
            _probe(width=1920, height=1080),
        ]

        with (
            patch(
                "app.services.marketing_video_preparation."
                "probe_marketing_video_file",
                new=AsyncMock(side_effect=probes),
            ),
            patch(
                "app.services.marketing_video_preparation."
                "render_marketing_video_variant",
                new=AsyncMock(side_effect=_fake_render),
            ),
        ):
            result = await prepare_marketing_video_derivatives(
                storage=storage,
                business_id=business_id,
                asset_id=asset_id,
                source_reference=storage.public_url(storage.source_key),
                source_extension="mov",
            )

        self.assertEqual(
            storage.get_file_calls,
            [
                f"businesses/{business_id}/marketing/uploads/{asset_id}/"
                "source.mov"
            ],
        )
        self.assertTrue(
            all(key.endswith(".mp4") for key in storage.put_file_calls)
        )
        self.assertEqual(
            storage.put_file_content_types,
            ["video/mp4", "video/mp4"],
        )
        self.assertTrue(
            all(item.content_type == "video/mp4" for item in result.variants)
        )

    async def test_forged_tenant_source_is_rejected_before_read(
        self,
    ) -> None:
        business_id = uuid4()
        asset_id = uuid4()
        storage = _Storage(
            business_id=business_id,
            asset_id=asset_id,
        )

        forged = (
            "https://cdn.example.test/"
            f"businesses/{uuid4()}/marketing/uploads/"
            f"{asset_id}/source.mp4"
        )

        with self.assertRaisesRegex(
            MarketingValidationError,
            "marketing_media_reference_invalid",
        ):
            await prepare_marketing_video_derivatives(
                storage=storage,
                business_id=business_id,
                asset_id=asset_id,
                source_reference=forged,
                source_extension="mp4",
            )

        self.assertEqual(storage.get_file_calls, [])
        self.assertEqual(storage.put_file_calls, [])

    async def test_invalid_render_is_rejected_before_storage(
        self,
    ) -> None:
        business_id = uuid4()
        asset_id = uuid4()
        storage = _Storage(
            business_id=business_id,
            asset_id=asset_id,
        )

        source_reference = storage.public_url(
            storage.source_key
        )

        probes = [
            _probe(width=720, height=1280),
            _probe(
                width=999,
                height=1920,
            ),
        ]

        with (
            patch(
                "app.services.marketing_video_preparation."
                "probe_marketing_video_file",
                new=AsyncMock(side_effect=probes),
            ),
            patch(
                "app.services.marketing_video_preparation."
                "render_marketing_video_variant",
                new=AsyncMock(side_effect=_fake_render),
            ),
        ):
            with self.assertRaisesRegex(
                MarketingValidationError,
                "marketing_video_render_failed",
            ):
                await prepare_marketing_video_derivatives(
                    storage=storage,
                    business_id=business_id,
                    asset_id=asset_id,
                    source_reference=source_reference,
                    source_extension="mp4",
                )

        self.assertEqual(storage.put_file_calls, [])
        self.assertEqual(storage.deleted, [])

    async def test_partial_storage_failure_cleans_attempted_variants(
        self,
    ) -> None:
        business_id = uuid4()
        asset_id = uuid4()
        storage = _Storage(
            business_id=business_id,
            asset_id=asset_id,
            fail_put_key="landscape_16_9.mp4",
        )

        source_reference = storage.public_url(
            storage.source_key
        )

        probes = [
            _probe(width=720, height=1280),
            _probe(width=1080, height=1920),
            _probe(width=1920, height=1080),
        ]

        with (
            patch(
                "app.services.marketing_video_preparation."
                "probe_marketing_video_file",
                new=AsyncMock(side_effect=probes),
            ),
            patch(
                "app.services.marketing_video_preparation."
                "render_marketing_video_variant",
                new=AsyncMock(side_effect=_fake_render),
            ),
        ):
            with self.assertRaises(
                StorageOperationError
            ):
                await prepare_marketing_video_derivatives(
                    storage=storage,
                    business_id=business_id,
                    asset_id=asset_id,
                    source_reference=source_reference,
                    source_extension="mp4",
                )

        self.assertEqual(
            len(storage.put_file_calls),
            2,
        )

        self.assertEqual(
            storage.deleted,
            list(reversed(storage.put_file_calls)),
        )

        self.assertNotIn(
            storage.source_key,
            storage.deleted,
        )


if __name__ == "__main__":
    unittest.main()
