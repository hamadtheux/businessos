from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.marketing import MarketingValidationError  # noqa: E402
from app.services.marketing_video_dispatcher import (  # noqa: E402
    dispatch_marketing_video_preparation_job,
)
from app.services.marketing_video_preparation import (  # noqa: E402
    PreparedMarketingVideoDerivative,
    PreparedMarketingVideoPackage,
)
from app.storage.base import StorageOperationError  # noqa: E402


class _Session:
    def __init__(self, value=None) -> None:
        self.value = value
        self.committed = False
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.exited = True

    async def scalar(self, *_args):
        return self.value

    async def commit(self):
        self.committed = True


class _SessionFactory:
    def __init__(self, sessions: list[_Session]) -> None:
        self.sessions = list(sessions)

    def __call__(self):
        if not self.sessions:
            raise AssertionError("unexpected DB session")
        return self.sessions.pop(0)


class _Storage:
    def public_url(self, object_key: str) -> str:
        return f"https://cdn.example.test/{object_key}"

    def object_key_from_reference(self, reference: str) -> str:
        prefix = "https://cdn.example.test/"
        if not reference.startswith(prefix):
            raise StorageOperationError("invalid reference")
        return reference[len(prefix):]


def _asset(*, business_id, asset_id, status="processing"):
    source_key = (
        f"businesses/{business_id}/marketing/uploads/{asset_id}/"
        "source.mp4"
    )
    return SimpleNamespace(
        id=asset_id,
        business_id=business_id,
        media_type="video",
        source_type="import",
        generation_status=status,
        storage_reference=f"https://cdn.example.test/{source_key}",
        width=None,
        height=None,
        duration_seconds=None,
        aspect_ratio=None,
        asset_type="video_source",
        creative_metadata={
            "upload_content_type": "video/mp4",
            "original_immutable": True,
            "variants": {},
        },
    )


def _package(*, business_id, asset_id):
    variants = []
    for key, width, height, ratio in (
        ("vertical_9_16", 1080, 1920, "9:16"),
        ("landscape_16_9", 1920, 1080, "16:9"),
    ):
        variants.append(
            PreparedMarketingVideoDerivative(
                key=key,
                storage_reference=(
                    "https://cdn.example.test/"
                    f"businesses/{business_id}/marketing/uploads/{asset_id}/"
                    f"variants/{key}.mp4"
                ),
                content_type="video/mp4",
                width=width,
                height=height,
                aspect_ratio=ratio,
                duration_seconds=30,
                video_codec="h264",
                audio_codec="aac",
                transformation="contain_no_crop",
            )
        )

    return PreparedMarketingVideoPackage(
        width=720,
        height=1280,
        duration_seconds=30,
        video_codec="h264",
        audio_codec="aac",
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        variants=tuple(variants),
    )


def _job(*, business_id, asset_id, attempt=1, maximum=3):
    return SimpleNamespace(
        business_id=business_id,
        creative_asset_id=asset_id,
        attempt_count=attempt,
        max_attempts=maximum,
    )


class MarketingVideoDispatcherTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_processing_runs_after_preflight_transaction_closes(
        self,
    ) -> None:
        business_id = uuid4()
        asset_id = uuid4()
        asset = _asset(
            business_id=business_id,
            asset_id=asset_id,
        )

        preflight = _Session(asset)
        finalize = _Session(asset)
        sessions = [preflight, finalize]

        async def prepare(**_kwargs):
            self.assertTrue(preflight.exited)
            self.assertTrue(preflight.committed)
            self.assertFalse(finalize.committed)
            return _package(
                business_id=business_id,
                asset_id=asset_id,
            )

        with (
            patch(
                "app.services.marketing_video_dispatcher.AsyncSessionFactory",
                new=_SessionFactory(sessions),
            ),
            patch(
                "app.services.marketing_video_dispatcher."
                "prepare_marketing_video_derivatives",
                new=AsyncMock(side_effect=prepare),
            ) as preparation,
        ):
            outcome = await dispatch_marketing_video_preparation_job(
                _job(
                    business_id=business_id,
                    asset_id=asset_id,
                ),
                storage=_Storage(),
            )

        self.assertTrue(outcome.succeeded)
        preparation.assert_awaited_once()
        self.assertTrue(finalize.exited)
        self.assertTrue(finalize.committed)

        self.assertEqual(asset.generation_status, "ready")
        self.assertEqual(asset.width, 720)
        self.assertEqual(asset.height, 1280)
        self.assertEqual(asset.aspect_ratio, "9:16")
        self.assertEqual(asset.asset_type, "video_vertical")

        variants = asset.creative_metadata["variants"]
        self.assertEqual(
            set(variants),
            {"vertical_9_16", "landscape_16_9"},
        )

    async def test_invalid_customer_media_becomes_terminal_failed(
        self,
    ) -> None:
        business_id = uuid4()
        asset_id = uuid4()
        asset = _asset(
            business_id=business_id,
            asset_id=asset_id,
        )

        preflight = _Session(asset)
        failure = _Session(asset)

        with (
            patch(
                "app.services.marketing_video_dispatcher.AsyncSessionFactory",
                new=_SessionFactory([preflight, failure]),
            ),
            patch(
                "app.services.marketing_video_dispatcher."
                "prepare_marketing_video_derivatives",
                new=AsyncMock(
                    side_effect=MarketingValidationError(
                        "marketing_media_unreadable"
                    )
                ),
            ),
        ):
            outcome = await dispatch_marketing_video_preparation_job(
                _job(
                    business_id=business_id,
                    asset_id=asset_id,
                ),
                storage=_Storage(),
            )

        self.assertTrue(outcome.succeeded)
        self.assertFalse(outcome.retryable)
        self.assertEqual(asset.generation_status, "failed")
        self.assertEqual(
            asset.creative_metadata["video_preparation"]["failure_code"],
            "marketing_media_unreadable",
        )

    async def test_storage_failure_is_retryable_without_failing_asset_early(
        self,
    ) -> None:
        business_id = uuid4()
        asset_id = uuid4()
        asset = _asset(
            business_id=business_id,
            asset_id=asset_id,
        )

        with (
            patch(
                "app.services.marketing_video_dispatcher.AsyncSessionFactory",
                new=_SessionFactory([_Session(asset)]),
            ),
            patch(
                "app.services.marketing_video_dispatcher."
                "prepare_marketing_video_derivatives",
                new=AsyncMock(
                    side_effect=StorageOperationError(
                        "private storage detail"
                    )
                ),
            ),
        ):
            outcome = await dispatch_marketing_video_preparation_job(
                _job(
                    business_id=business_id,
                    asset_id=asset_id,
                    attempt=1,
                    maximum=3,
                ),
                storage=_Storage(),
            )

        self.assertFalse(outcome.succeeded)
        self.assertEqual(
            outcome.failure_code,
            "dependency_unavailable",
        )
        self.assertTrue(outcome.retryable)
        self.assertEqual(
            asset.generation_status,
            "processing",
        )

    async def test_final_infrastructure_attempt_marks_asset_failed(
        self,
    ) -> None:
        business_id = uuid4()
        asset_id = uuid4()
        asset = _asset(
            business_id=business_id,
            asset_id=asset_id,
        )

        with (
            patch(
                "app.services.marketing_video_dispatcher.AsyncSessionFactory",
                new=_SessionFactory(
                    [
                        _Session(asset),
                        _Session(asset),
                    ]
                ),
            ),
            patch(
                "app.services.marketing_video_dispatcher."
                "prepare_marketing_video_derivatives",
                new=AsyncMock(
                    side_effect=TimeoutError()
                ),
            ),
        ):
            outcome = await dispatch_marketing_video_preparation_job(
                _job(
                    business_id=business_id,
                    asset_id=asset_id,
                    attempt=3,
                    maximum=3,
                ),
                storage=_Storage(),
            )

        self.assertFalse(outcome.succeeded)
        self.assertTrue(outcome.retryable)
        self.assertEqual(asset.generation_status, "failed")
        self.assertEqual(
            asset.creative_metadata["video_preparation"]["failure_code"],
            "marketing_video_preparation_unavailable",
        )

    async def test_ready_asset_is_idempotent_and_does_not_render_again(
        self,
    ) -> None:
        business_id = uuid4()
        asset_id = uuid4()
        asset = _asset(
            business_id=business_id,
            asset_id=asset_id,
            status="ready",
        )

        package = _package(
            business_id=business_id,
            asset_id=asset_id,
        )
        asset.creative_metadata["variants"] = {
            item.key: {
                "storage_reference": item.storage_reference,
                "content_type": item.content_type,
                "width": item.width,
                "height": item.height,
                "aspect_ratio": item.aspect_ratio,
                "duration_seconds": item.duration_seconds,
                "video_codec": item.video_codec,
                "audio_codec": item.audio_codec,
                "transformation": item.transformation,
            }
            for item in package.variants
        }

        preparation = AsyncMock()

        with (
            patch(
                "app.services.marketing_video_dispatcher.AsyncSessionFactory",
                new=_SessionFactory([_Session(asset)]),
            ),
            patch(
                "app.services.marketing_video_dispatcher."
                "prepare_marketing_video_derivatives",
                new=preparation,
            ),
        ):
            outcome = await dispatch_marketing_video_preparation_job(
                _job(
                    business_id=business_id,
                    asset_id=asset_id,
                    attempt=2,
                ),
                storage=_Storage(),
            )

        self.assertTrue(outcome.succeeded)
        preparation.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
