from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.marketing import MarketingNotFoundError  # noqa: E402
from app.models.marketing import CreativeAsset  # noqa: E402
from app.services.marketing import materialize_creative_asset_response  # noqa: E402
from app.storage.base import StorageOperationError  # noqa: E402
from app.storage.local import LocalObjectStorage  # noqa: E402


BUSINESS_ID = UUID("71000000-0000-4000-8000-000000000001")
OTHER_BUSINESS_ID = UUID("72000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


class _PresentationStorage:
    def __init__(self) -> None:
        self.resolved: list[str] = []
        self.presented: list[tuple[str, int]] = []

    def object_key_from_reference(self, storage_reference: str) -> str:
        self.resolved.append(storage_reference)
        prefix = "https://media.example.test/"
        if not storage_reference.startswith(prefix):
            raise StorageOperationError("invalid")
        return storage_reference.removeprefix(prefix)

    def presentation_url(
        self,
        object_key: str,
        *,
        expires_in_seconds: int,
    ) -> str:
        self.presented.append((object_key, expires_in_seconds))
        return (
            f"https://objects.example.test/{object_key}"
            f"?X-Amz-Expires={expires_in_seconds}&signature=fresh"
        )


def _asset(
    *,
    business_id: UUID = BUSINESS_ID,
    generation_status: str = "ready",
    storage_reference: str | None = None,
) -> CreativeAsset:
    asset_id = uuid4()
    reference = storage_reference
    if reference is None:
        reference = (
            "https://media.example.test/"
            f"businesses/{business_id}/marketing/creatives/{asset_id}/"
            "final/generation-5.png"
        )
    return CreativeAsset(
        id=asset_id,
        business_id=business_id,
        campaign_id=None,
        content_id=None,
        asset_type="social_square",
        media_type="image",
        source_type="future_provider",
        instructions="Create a grounded visual.",
        visual_direction="Trusted direction",
        generation_status=generation_status,
        storage_reference=reference,
        width=1080,
        height=1080,
        aspect_ratio="1:1",
        alt_text="Grounded campaign creative",
        duration_seconds=None,
        creative_metadata={"raw_checkpoint_keys": ["private/raw/attempt-1.png"]},
        created_at=NOW,
        updated_at=NOW,
    )


class CreativePresentationTests(unittest.TestCase):
    def test_existing_ready_asset_gets_temporary_url_without_mutation(self) -> None:
        storage = _PresentationStorage()
        asset = _asset()
        durable_reference = asset.storage_reference

        response = materialize_creative_asset_response(
            asset,
            business_id=BUSINESS_ID,
            storage=storage,  # type: ignore[arg-type]
            signed_url_ttl_seconds=900,
        )

        self.assertEqual(asset.storage_reference, durable_reference)
        self.assertNotEqual(response.storage_reference, durable_reference)
        self.assertIn("X-Amz-Expires=900", response.storage_reference or "")
        self.assertEqual(len(storage.presented), 1)
        self.assertNotIn("creative_metadata", response.model_dump())

    def test_cross_tenant_asset_is_not_projected_or_resolved(self) -> None:
        storage = _PresentationStorage()
        asset = _asset(business_id=OTHER_BUSINESS_ID)

        with self.assertRaises(MarketingNotFoundError):
            materialize_creative_asset_response(
                asset,
                business_id=BUSINESS_ID,
                storage=storage,  # type: ignore[arg-type]
                signed_url_ttl_seconds=900,
            )

        self.assertEqual(storage.resolved, [])
        self.assertEqual(storage.presented, [])

    def test_raw_and_other_asset_namespaces_are_never_signed(self) -> None:
        for namespace in (
            "raw/attempt-1.png",
            "final/nested/generation-5.png",
            "final/generation-5.png",
        ):
            with self.subTest(namespace=namespace):
                storage = _PresentationStorage()
                asset = _asset()
                asset.storage_reference = (
                    "https://media.example.test/"
                    f"businesses/{BUSINESS_ID}/marketing/creatives/"
                    f"{uuid4()}/{namespace}"
                )

                response = materialize_creative_asset_response(
                    asset,
                    business_id=BUSINESS_ID,
                    storage=storage,  # type: ignore[arg-type]
                    signed_url_ttl_seconds=900,
                )

                self.assertIsNone(response.storage_reference)
                self.assertEqual(storage.presented, [])

    def test_invalid_reference_fails_closed_without_signing(self) -> None:
        storage = _PresentationStorage()
        asset = _asset(storage_reference="https://attacker.example/final.png")

        response = materialize_creative_asset_response(
            asset,
            business_id=BUSINESS_ID,
            storage=storage,  # type: ignore[arg-type]
            signed_url_ttl_seconds=900,
        )

        self.assertIsNone(response.storage_reference)
        self.assertEqual(storage.presented, [])

    def test_non_ready_asset_never_receives_presentation_url(self) -> None:
        storage = _PresentationStorage()
        asset = _asset(generation_status="reviewing")

        response = materialize_creative_asset_response(
            asset,
            business_id=BUSINESS_ID,
            storage=storage,  # type: ignore[arg-type]
            signed_url_ttl_seconds=900,
        )

        self.assertIsNone(response.storage_reference)
        self.assertEqual(storage.resolved, [])
        self.assertEqual(storage.presented, [])

    def test_manual_import_and_brief_cannot_claim_server_owned_final_files(self) -> None:
        # There is no manual/import final-file upload route. Only successful
        # provider generation writes /final/ and sets source_type=future_provider.
        for source_type in ("manual", "import", "ai_brief"):
            with self.subTest(source_type=source_type):
                storage = _PresentationStorage()
                asset = _asset()
                asset.source_type = source_type
                response = materialize_creative_asset_response(
                    asset, business_id=BUSINESS_ID, storage=storage,
                    signed_url_ttl_seconds=900,
                )
                self.assertIsNone(response.storage_reference)
                self.assertEqual(storage.resolved, [])
                self.assertEqual(storage.presented, [])

    def test_local_ready_asset_keeps_browser_loadable_media_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalObjectStorage(Path(directory), "/api/v1/media")
            asset = _asset()
            key = (
                f"businesses/{BUSINESS_ID}/marketing/creatives/{asset.id}/"
                "final/generation-5.png"
            )
            asset.storage_reference = storage.public_url(key)

            response = materialize_creative_asset_response(
                asset,
                business_id=BUSINESS_ID,
                storage=storage,
                signed_url_ttl_seconds=900,
            )

            self.assertEqual(response.storage_reference, storage.public_url(key))
            self.assertEqual(asset.storage_reference, storage.public_url(key))

    def test_ready_import_video_presents_prepared_landscape_mp4_not_original_mov(self) -> None:
        storage = _PresentationStorage()
        asset = _asset()
        asset.source_type = "import"
        asset.media_type = "video"
        asset.asset_type = "video_landscape"
        asset.storage_reference = (
            "https://media.example.test/"
            f"businesses/{BUSINESS_ID}/marketing/uploads/{asset.id}/source.mov"
        )
        variant_key = (
            f"businesses/{BUSINESS_ID}/marketing/uploads/{asset.id}/"
            "variants/landscape_16_9.mp4"
        )
        asset.creative_metadata = {
            "video_preparation": {"status": "ready", "version": 1},
            "variants": {
                "landscape_16_9": {
                    "storage_reference": f"https://media.example.test/{variant_key}",
                    "content_type": "video/mp4",
                    "width": 1920,
                    "height": 1080,
                    "aspect_ratio": "16:9",
                    "duration_seconds": 6,
                    "video_codec": "h264",
                    "audio_codec": None,
                    "transformation": "contain_no_crop",
                }
            },
        }

        response = materialize_creative_asset_response(
            asset,
            business_id=BUSINESS_ID,
            storage=storage,  # type: ignore[arg-type]
            signed_url_ttl_seconds=900,
        )

        self.assertIsNotNone(response.storage_reference)
        self.assertIn("landscape_16_9.mp4", response.storage_reference or "")
        self.assertNotIn("source.mov", response.storage_reference or "")
        self.assertEqual(
            storage.presented,
            [(variant_key, 900)],
        )

    def test_ready_import_video_without_valid_prepared_variant_fails_closed(self) -> None:
        storage = _PresentationStorage()
        asset = _asset()
        asset.source_type = "import"
        asset.media_type = "video"
        asset.asset_type = "video_landscape"
        asset.storage_reference = (
            "https://media.example.test/"
            f"businesses/{BUSINESS_ID}/marketing/uploads/{asset.id}/source.mov"
        )
        asset.creative_metadata = {
            "video_preparation": {"status": "ready", "version": 1},
            "variants": {},
        }

        response = materialize_creative_asset_response(
            asset,
            business_id=BUSINESS_ID,
            storage=storage,  # type: ignore[arg-type]
            signed_url_ttl_seconds=900,
        )

        self.assertIsNone(response.storage_reference)
        self.assertEqual(storage.presented, [])


    def test_ready_vertical_import_video_presents_vertical_mp4(self) -> None:
        storage = _PresentationStorage()
        asset = _asset()
        asset.source_type = "import"
        asset.media_type = "video"
        asset.asset_type = "video_vertical"
        asset.width = 1080
        asset.height = 1920
        asset.storage_reference = (
            "https://media.example.test/"
            f"businesses/{BUSINESS_ID}/marketing/uploads/{asset.id}/source.mov"
        )
        variant_key = (
            f"businesses/{BUSINESS_ID}/marketing/uploads/{asset.id}/"
            "variants/vertical_9_16.mp4"
        )
        asset.creative_metadata = {
            "video_preparation": {"status": "ready", "version": 1},
            "variants": {
                "vertical_9_16": {
                    "storage_reference": f"https://media.example.test/{variant_key}",
                    "content_type": "video/mp4",
                    "width": 1080,
                    "height": 1920,
                    "aspect_ratio": "9:16",
                    "duration_seconds": 6,
                    "video_codec": "h264",
                    "audio_codec": "aac",
                    "transformation": "contain_no_crop",
                }
            },
        }

        original_reference = asset.storage_reference

        response = materialize_creative_asset_response(
            asset,
            business_id=BUSINESS_ID,
            storage=storage,  # type: ignore[arg-type]
            signed_url_ttl_seconds=900,
        )

        self.assertIsNotNone(response.storage_reference)
        self.assertIn("vertical_9_16.mp4", response.storage_reference or "")
        self.assertEqual(asset.storage_reference, original_reference)
        self.assertEqual(storage.presented, [(variant_key, 900)])

    def test_ready_import_video_rejects_foreign_variant_object(self) -> None:
        storage = _PresentationStorage()
        asset = _asset()
        asset.source_type = "import"
        asset.media_type = "video"
        asset.asset_type = "video_landscape"
        asset.width = 2560
        asset.height = 1440
        asset.storage_reference = (
            "https://media.example.test/"
            f"businesses/{BUSINESS_ID}/marketing/uploads/{asset.id}/source.mov"
        )

        foreign_asset_id = uuid4()
        foreign_key = (
            f"businesses/{BUSINESS_ID}/marketing/uploads/{foreign_asset_id}/"
            "variants/landscape_16_9.mp4"
        )
        asset.creative_metadata = {
            "video_preparation": {"status": "ready", "version": 1},
            "variants": {
                "landscape_16_9": {
                    "storage_reference": f"https://media.example.test/{foreign_key}",
                    "content_type": "video/mp4",
                    "width": 1920,
                    "height": 1080,
                    "aspect_ratio": "16:9",
                    "duration_seconds": 6,
                    "video_codec": "h264",
                    "audio_codec": None,
                    "transformation": "contain_no_crop",
                }
            },
        }

        response = materialize_creative_asset_response(
            asset,
            business_id=BUSINESS_ID,
            storage=storage,  # type: ignore[arg-type]
            signed_url_ttl_seconds=900,
        )

        self.assertIsNone(response.storage_reference)
        self.assertEqual(storage.presented, [])

    def test_import_video_is_not_presented_until_preparation_is_ready(self) -> None:
        storage = _PresentationStorage()
        asset = _asset()
        asset.source_type = "import"
        asset.media_type = "video"
        asset.asset_type = "video_landscape"
        asset.width = 2560
        asset.height = 1440
        asset.storage_reference = (
            "https://media.example.test/"
            f"businesses/{BUSINESS_ID}/marketing/uploads/{asset.id}/source.mov"
        )
        variant_key = (
            f"businesses/{BUSINESS_ID}/marketing/uploads/{asset.id}/"
            "variants/landscape_16_9.mp4"
        )
        asset.creative_metadata = {
            "video_preparation": {"status": "processing", "version": 1},
            "variants": {
                "landscape_16_9": {
                    "storage_reference": f"https://media.example.test/{variant_key}",
                    "content_type": "video/mp4",
                    "width": 1920,
                    "height": 1080,
                    "aspect_ratio": "16:9",
                    "duration_seconds": 6,
                    "video_codec": "h264",
                    "audio_codec": None,
                    "transformation": "contain_no_crop",
                }
            },
        }

        response = materialize_creative_asset_response(
            asset,
            business_id=BUSINESS_ID,
            storage=storage,  # type: ignore[arg-type]
            signed_url_ttl_seconds=900,
        )

        self.assertIsNone(response.storage_reference)
        self.assertEqual(storage.resolved, [])
        self.assertEqual(storage.presented, [])


    def test_tenant_owned_import_gets_signed_presentation_url(self) -> None:
        storage = _PresentationStorage()
        asset = _asset()
        asset.source_type = "import"
        asset.storage_reference = (
            "https://media.example.test/"
            f"businesses/{BUSINESS_ID}/marketing/uploads/{asset.id}/source.png"
        )

        response = materialize_creative_asset_response(
            asset,
            business_id=BUSINESS_ID,
            storage=storage,  # type: ignore[arg-type]
            signed_url_ttl_seconds=900,
        )

        self.assertIn("X-Amz-Expires=900", response.storage_reference or "")
        self.assertEqual(
            storage.presented,
            [
                (
                    f"businesses/{BUSINESS_ID}/marketing/uploads/"
                    f"{asset.id}/source.png",
                    900,
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
