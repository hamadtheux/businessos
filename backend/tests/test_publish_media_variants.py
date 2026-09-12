from types import SimpleNamespace
from uuid import UUID

import pytest

from app.exceptions.integration import IntegrationStateError
from app.integrations.action_boundary import (
    _publish_media_reference,
    _trusted_publish_media_object_key,
)
from app.schemas.ai_action_payload import PublishSocialPostPayload


BUSINESS_ID = UUID("d1000000-0000-4000-8000-000000000001")
ASSET_ID = UUID("d2000000-0000-4000-8000-000000000001")


def _reference(key: str) -> str:
    return (
        "https://storage.example.test/"
        f"businesses/{BUSINESS_ID}/marketing/uploads/"
        f"{ASSET_ID}/{key}"
    )


def _asset(
    *,
    media_type: str = "image",
    metadata: dict[str, object] | None = None,
):
    if metadata is None:
        metadata = {
            "variants": {
                "square_1_1": {
                    "storage_reference": _reference(
                        "variants/square_1_1.jpg"
                    ),
                    "content_type": "image/jpeg",
                    "width": 1200,
                    "height": 1200,
                    "aspect_ratio": "1:1",
                    "transformation": "contain_no_crop",
                },
                "landscape_1_91_1": {
                    "storage_reference": _reference(
                        "variants/landscape_1_91_1.jpg"
                    ),
                    "content_type": "image/jpeg",
                    "width": 1200,
                    "height": 628,
                    "aspect_ratio": "1.91:1",
                    "transformation": "contain_no_crop",
                },
                "portrait_4_5": {
                    "storage_reference": _reference(
                        "variants/portrait_4_5.jpg"
                    ),
                    "content_type": "image/jpeg",
                    "width": 1080,
                    "height": 1350,
                    "aspect_ratio": "4:5",
                    "transformation": "contain_no_crop",
                },
                "vertical_9_16": {
                    "storage_reference": _reference(
                        "variants/vertical_9_16.jpg"
                    ),
                    "content_type": "image/jpeg",
                    "width": 1080,
                    "height": 1920,
                    "aspect_ratio": "9:16",
                    "transformation": "contain_no_crop",
                },
            }
        }

    return SimpleNamespace(
        id=ASSET_ID,
        business_id=BUSINESS_ID,
        source_type="import",
        media_type=media_type,
        width=1600,
        height=1200,
        duration_seconds=30 if media_type == "video" else None,
        storage_reference=_reference("source.jpg"),
        creative_metadata=metadata,
    )


def _payload(platform: str, media_type: str = "image"):
    return PublishSocialPostPayload(
        platform=platform,
        content="Approved post content",
        media_refs=[f"creative_asset:{ASSET_ID}"],
        media_type=media_type,
    )


@pytest.mark.parametrize(
    ("platform", "expected_variant"),
    [
        ("instagram", "portrait_4_5"),
        ("facebook", "portrait_4_5"),
        ("linkedin", "landscape_1_91_1"),
        ("tiktok", "vertical_9_16"),
    ],
)
def test_publish_image_uses_automatic_platform_variant(
    platform: str,
    expected_variant: str,
) -> None:
    reference, variant = _publish_media_reference(
        asset=_asset(),  # type: ignore[arg-type]
        payload=_payload(platform),
    )

    assert variant == expected_variant
    assert reference.endswith(
        f"/variants/{expected_variant}.jpg"
    )




def test_variant_metadata_must_match_server_profile() -> None:
    asset = _asset()
    asset.creative_metadata["variants"]["portrait_4_5"]["width"] = 999

    with pytest.raises(
        IntegrationStateError,
        match="publish_media_variant_invalid",
    ):
        _publish_media_reference(
            asset=asset,  # type: ignore[arg-type]
            payload=_payload("instagram"),
        )


def test_video_keeps_original_until_video_pipeline_is_available() -> None:
    asset = _asset(media_type="video")

    reference, variant = _publish_media_reference(
        asset=asset,  # type: ignore[arg-type]
        payload=_payload("instagram", "video"),
    )

    assert reference == asset.storage_reference
    assert variant is None


def test_trusted_variant_path_is_exact_and_tenant_scoped() -> None:
    asset = _asset()

    good = (
        f"businesses/{BUSINESS_ID}/marketing/uploads/"
        f"{ASSET_ID}/variants/portrait_4_5.jpg"
    )

    wrong_variant = (
        f"businesses/{BUSINESS_ID}/marketing/uploads/"
        f"{ASSET_ID}/variants/square_1_1.jpg"
    )

    traversal = (
        f"businesses/{BUSINESS_ID}/marketing/uploads/"
        f"{ASSET_ID}/variants/portrait_4_5.jpg/../../source.jpg"
    )

    assert _trusted_publish_media_object_key(
        business_id=BUSINESS_ID,
        asset=asset,  # type: ignore[arg-type]
        object_key=good,
        expected_variant="portrait_4_5",
    )

    assert not _trusted_publish_media_object_key(
        business_id=BUSINESS_ID,
        asset=asset,  # type: ignore[arg-type]
        object_key=wrong_variant,
        expected_variant="portrait_4_5",
    )

    assert not _trusted_publish_media_object_key(
        business_id=BUSINESS_ID,
        asset=asset,  # type: ignore[arg-type]
        object_key=traversal,
        expected_variant="portrait_4_5",
    )
