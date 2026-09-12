from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from uuid import uuid4

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.integration import IntegrationStateError  # noqa: E402
from app.integrations.action_boundary import (  # noqa: E402
    _materialize_publish_media_payload,
)
from app.schemas.ai_action_payload import PublishSocialPostPayload  # noqa: E402


class _Session:
    def __init__(self, values: list[object]) -> None:
        self.values = list(values)
        self.statements: list[object] = []

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.values.pop(0) if self.values else None


class _Storage:
    def __init__(self, *, resolved_key: str) -> None:
        self.resolved_key = resolved_key
        self.resolved: list[str] = []
        self.presented: list[tuple[str, int]] = []

    def object_key_from_reference(self, reference: str) -> str:
        self.resolved.append(reference)
        return self.resolved_key

    def presentation_url(
        self,
        object_key: str,
        *,
        expires_in_seconds: int,
    ) -> str:
        self.presented.append((object_key, expires_in_seconds))
        return (
            "https://objects.example.test/"
            + object_key
            + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&"
            + "X-Amz-Credential="
            + ("a" * 320)
        )


def _variant_reference(
    business_id,
    asset_id,
    variant: str,
) -> str:
    return (
        "https://media.example.test/"
        f"businesses/{business_id}/marketing/uploads/"
        f"{asset_id}/variants/{variant}.jpg"
    )


def _variant_metadata(
    business_id,
    asset_id,
) -> dict[str, object]:
    return {
        "variants": {
            "square_1_1": {
                "storage_reference": _variant_reference(
                    business_id,
                    asset_id,
                    "square_1_1",
                ),
                "content_type": "image/jpeg",
                "width": 1200,
                "height": 1200,
                "aspect_ratio": "1:1",
                "transformation": "contain_no_crop",
            },
            "landscape_1_91_1": {
                "storage_reference": _variant_reference(
                    business_id,
                    asset_id,
                    "landscape_1_91_1",
                ),
                "content_type": "image/jpeg",
                "width": 1200,
                "height": 628,
                "aspect_ratio": "1.91:1",
                "transformation": "contain_no_crop",
            },
            "portrait_4_5": {
                "storage_reference": _variant_reference(
                    business_id,
                    asset_id,
                    "portrait_4_5",
                ),
                "content_type": "image/jpeg",
                "width": 1080,
                "height": 1350,
                "aspect_ratio": "4:5",
                "transformation": "contain_no_crop",
            },
            "vertical_9_16": {
                "storage_reference": _variant_reference(
                    business_id,
                    asset_id,
                    "vertical_9_16",
                ),
                "content_type": "image/jpeg",
                "width": 1080,
                "height": 1920,
                "aspect_ratio": "9:16",
                "transformation": "contain_no_crop",
            },
        },
    }


def _source_reference(
    business_id,
    asset_id,
) -> str:
    return (
        "https://media.example.test/"
        f"businesses/{business_id}/marketing/uploads/"
        f"{asset_id}/source.jpg"
    )


class SocialMediaDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_approved_asset_handle_becomes_fresh_transient_signed_url(self) -> None:
        business_id = uuid4()
        action_id = uuid4()
        content_id = uuid4()
        asset_id = uuid4()
        source_key = (
            f"businesses/{business_id}/marketing/uploads/"
            f"{asset_id}/source.jpg"
        )
        variant_key = (
            f"businesses/{business_id}/marketing/uploads/"
            f"{asset_id}/variants/portrait_4_5.jpg"
        )
        durable = f"https://media.example.test/{source_key}"
        variant_reference = _variant_reference(
            business_id,
            asset_id,
            "portrait_4_5",
        )

        proposal = SimpleNamespace(
            entity_id=content_id,
            channel="facebook",
        )
        content = SimpleNamespace(
            id=content_id,
            business_id=business_id,
            root_content_id=content_id,
            proposal_key=None,
        )
        asset = SimpleNamespace(
            id=asset_id,
            business_id=business_id,
            content_id=content_id,
            source_type="import",
            generation_status="ready",
            media_type="image",
            width=1600,
            height=1200,
            creative_metadata=_variant_metadata(
                business_id,
                asset_id,
            ),
            storage_reference=durable,
        )
        storage = _Storage(resolved_key=variant_key)
        original = PublishSocialPostPayload(
            platform="facebook",
            content="Approved post",
            media_refs=[f"creative_asset:{asset_id}"],
            media_type="image",
        )

        transient = await _materialize_publish_media_payload(
            _Session([proposal, content, asset]),
            business_id=business_id,
            action_id=action_id,
            payload=original,
            storage=storage,  # type: ignore[arg-type]
        )

        self.assertEqual(
            original.media_refs,
            [f"creative_asset:{asset_id}"],
        )
        self.assertEqual(transient.media_type, "image")
        self.assertEqual(len(transient.media_refs), 1)
        self.assertTrue(transient.media_refs[0].startswith("https://"))
        self.assertGreater(len(transient.media_refs[0]), 255)
        # The persisted/approved handle remains the stable asset UUID.
        self.assertEqual(
            original.media_refs,
            [f"creative_asset:{asset_id}"],
        )

        # Execution resolves and signs only the server-selected derivative.
        self.assertEqual(
            storage.resolved,
            [variant_reference],
        )
        self.assertEqual(
            storage.presented,
            [(variant_key, 3600)],
        )

        # The original uploaded source remains unchanged and is not signed
        # for Facebook feed execution.
        self.assertNotIn(
            source_key,
            [item[0] for item in storage.presented],
        )

    async def test_meta_video_execution_remains_fail_closed(self) -> None:
        business_id = uuid4()
        action_id = uuid4()
        content_id = uuid4()
        asset_id = uuid4()
        proposal = SimpleNamespace(
            entity_id=content_id,
            channel="instagram",
        )
        content = SimpleNamespace(
            id=content_id,
            business_id=business_id,
            root_content_id=content_id,
            proposal_key=None,
        )
        asset = SimpleNamespace(
            id=asset_id,
            business_id=business_id,
            content_id=content_id,
            source_type="import",
            generation_status="ready",
            media_type="video",
            width=1080,
            height=1920,
            duration_seconds=30,
            creative_metadata={},
            storage_reference=(
                "https://media.example.test/"
                f"businesses/{business_id}/marketing/uploads/{asset_id}/"
                "source.mp4"
            ),
        )
        storage = _Storage(resolved_key="unused")

        with self.assertRaisesRegex(
            IntegrationStateError,
            "publish_video_provider_unsupported",
        ):
            await _materialize_publish_media_payload(
                _Session([proposal, content, asset]),
                business_id=business_id,
                action_id=action_id,
                payload=PublishSocialPostPayload(
                    platform="instagram",
                    content="Approved post",
                    media_refs=[f"creative_asset:{asset_id}"],
                    media_type="video",
                ),
                storage=storage,  # type: ignore[arg-type]
            )

        self.assertEqual(storage.resolved, [])
        self.assertEqual(storage.presented, [])

    async def test_create_publish_package_can_share_owner_media_across_platforms(self) -> None:
        business_id = uuid4()
        action_id = uuid4()
        facebook_content_id = uuid4()
        instagram_owner_id = uuid4()
        asset_id = uuid4()
        package_id = uuid4()
        source_key = (
            f"businesses/{business_id}/marketing/uploads/"
            f"{asset_id}/source.jpg"
        )
        variant_key = (
            f"businesses/{business_id}/marketing/uploads/"
            f"{asset_id}/variants/portrait_4_5.jpg"
        )

        proposal = SimpleNamespace(
            entity_id=facebook_content_id,
            channel="facebook",
        )
        content = SimpleNamespace(
            id=facebook_content_id,
            business_id=business_id,
            root_content_id=facebook_content_id,
            proposal_key=f"create-publish:{package_id}:facebook",
        )
        asset = SimpleNamespace(
            id=asset_id,
            business_id=business_id,
            content_id=instagram_owner_id,
            source_type="import",
            generation_status="ready",
            media_type="image",
            width=1600,
            height=1200,
            creative_metadata=_variant_metadata(
                business_id,
                asset_id,
            ),
            storage_reference=f"https://media.example.test/{source_key}",
        )
        storage = _Storage(resolved_key=variant_key)

        transient = await _materialize_publish_media_payload(
            _Session([proposal, content, asset, instagram_owner_id]),
            business_id=business_id,
            action_id=action_id,
            payload=PublishSocialPostPayload(
                platform="facebook",
                content="Approved post",
                media_refs=[f"creative_asset:{asset_id}"],
                media_type="image",
            ),
            storage=storage,  # type: ignore[arg-type]
        )

        self.assertEqual(transient.media_type, "image")
        self.assertEqual(
            storage.presented,
            [(variant_key, 3600)],
        )
        self.assertTrue(
            transient.media_refs[0].startswith("https://")
        )

    async def test_direct_external_url_cannot_replace_approved_asset_handle(self) -> None:
        with self.assertRaisesRegex(
            IntegrationStateError,
            "publish_media_handle_invalid",
        ):
            await _materialize_publish_media_payload(
                _Session([]),
                business_id=uuid4(),
                action_id=uuid4(),
                payload=PublishSocialPostPayload(
                    platform="instagram",
                    content="Approved post",
                    media_refs=["https://attacker.example/media.jpg"],
                    media_type="image",
                ),
                storage=_Storage(resolved_key="unused"),  # type: ignore[arg-type]
            )

    async def test_asset_from_unrelated_content_cannot_be_signed(self) -> None:
        business_id = uuid4()
        action_id = uuid4()
        content_id = uuid4()
        foreign_content_id = uuid4()
        asset_id = uuid4()
        object_key = (
            f"businesses/{business_id}/marketing/uploads/"
            f"{asset_id}/source.jpg"
        )

        proposal = SimpleNamespace(entity_id=content_id, channel="facebook")
        content = SimpleNamespace(
            id=content_id,
            business_id=business_id,
            root_content_id=content_id,
            proposal_key=None,
        )
        asset = SimpleNamespace(
            id=asset_id,
            business_id=business_id,
            content_id=foreign_content_id,
            source_type="import",
            generation_status="ready",
            media_type="image",
            width=1600,
            height=1200,
            creative_metadata=_variant_metadata(
                business_id,
                asset_id,
            ),
            storage_reference=f"https://media.example.test/{object_key}",
        )
        storage = _Storage(resolved_key=object_key)

        with self.assertRaisesRegex(
            IntegrationStateError,
            "publish_media_content_conflict",
        ):
            await _materialize_publish_media_payload(
                _Session([proposal, content, asset, None]),
                business_id=business_id,
                action_id=action_id,
                payload=PublishSocialPostPayload(
                    platform="facebook",
                    content="Approved post",
                    media_refs=[f"creative_asset:{asset_id}"],
                    media_type="image",
                ),
                storage=storage,  # type: ignore[arg-type]
            )

        self.assertEqual(storage.presented, [])

    async def test_forged_storage_namespace_is_rejected_before_signing(self) -> None:
        business_id = uuid4()
        action_id = uuid4()
        content_id = uuid4()
        asset_id = uuid4()
        other_business = uuid4()

        proposal = SimpleNamespace(entity_id=content_id, channel="facebook")
        content = SimpleNamespace(
            id=content_id,
            business_id=business_id,
            root_content_id=content_id,
            proposal_key=None,
        )
        asset = SimpleNamespace(
            id=asset_id,
            business_id=business_id,
            content_id=content_id,
            source_type="import",
            generation_status="ready",
            media_type="image",
            width=1600,
            height=1200,
            creative_metadata=_variant_metadata(
                business_id,
                asset_id,
            ),
            storage_reference="https://media.example.test/claimed.jpg",
        )
        forged_key = (
            f"businesses/{other_business}/marketing/uploads/"
            f"{asset_id}/variants/portrait_4_5.jpg"
        )
        storage = _Storage(resolved_key=forged_key)

        with self.assertRaisesRegex(
            IntegrationStateError,
            "publish_media_reference_invalid",
        ):
            await _materialize_publish_media_payload(
                _Session([proposal, content, asset]),
                business_id=business_id,
                action_id=action_id,
                payload=PublishSocialPostPayload(
                    platform="facebook",
                    content="Approved post",
                    media_refs=[f"creative_asset:{asset_id}"],
                    media_type="image",
                ),
                storage=storage,  # type: ignore[arg-type]
            )

        self.assertEqual(storage.presented, [])


if __name__ == "__main__":
    unittest.main()
