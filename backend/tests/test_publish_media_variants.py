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
        metadata = _video_metadata() if media_type == "video" else {
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
        storage_reference=_reference(
            "source.mp4" if media_type == "video" else "source.jpg"
        ),
        creative_metadata=metadata,
    )


def _video_metadata() -> dict[str, object]:
    return {
        "variants": {
            "vertical_9_16": {
                "storage_reference": _reference(
                    "variants/vertical_9_16.mp4"
                ),
                "content_type": "video/mp4",
                "width": 1080,
                "height": 1920,
                "aspect_ratio": "9:16",
                "video_codec": "h264",
                "audio_codec": "aac",
                "transformation": "contain_no_crop",
            },
            "landscape_16_9": {
                "storage_reference": _reference(
                    "variants/landscape_16_9.mp4"
                ),
                "content_type": "video/mp4",
                "width": 1920,
                "height": 1080,
                "aspect_ratio": "16:9",
                "video_codec": "h264",
                "audio_codec": None,
                "transformation": "contain_no_crop",
            },
        }
    }


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


@pytest.mark.parametrize(
    ("platform", "expected_variant"),
    [
        ("instagram", "vertical_9_16"),
        ("facebook", "vertical_9_16"),
        ("linkedin", "landscape_16_9"),
        ("tiktok", "vertical_9_16"),
    ],
)
def test_video_uses_automatic_platform_derivative(
    platform: str,
    expected_variant: str,
) -> None:
    asset = _asset(media_type="video")

    reference, variant = _publish_media_reference(
        asset=asset,  # type: ignore[arg-type]
        payload=_payload(platform, "video"),
    )

    assert variant == expected_variant
    assert reference.endswith(f"/variants/{expected_variant}.mp4")


def test_video_derivative_metadata_must_match_server_profile() -> None:
    asset = _asset(media_type="video")
    asset.creative_metadata["variants"]["vertical_9_16"][
        "video_codec"
    ] = "vp9"

    with pytest.raises(
        IntegrationStateError,
        match="publish_media_variant_invalid",
    ):
        _publish_media_reference(
            asset=asset,  # type: ignore[arg-type]
            payload=_payload("instagram", "video"),
        )


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


def test_video_variant_path_is_exact_and_tenant_scoped() -> None:
    asset = _asset(media_type="video")
    good = (
        f"businesses/{BUSINESS_ID}/marketing/uploads/"
        f"{ASSET_ID}/variants/vertical_9_16.mp4"
    )
    forged = (
        f"businesses/{BUSINESS_ID}/marketing/uploads/"
        f"{ASSET_ID}/variants/vertical_9_16.jpg"
    )

    assert _trusted_publish_media_object_key(
        business_id=BUSINESS_ID,
        asset=asset,  # type: ignore[arg-type]
        object_key=good,
        expected_variant="vertical_9_16",
    )
    assert not _trusted_publish_media_object_key(
        business_id=BUSINESS_ID,
        asset=asset,  # type: ignore[arg-type]
        object_key=forged,
        expected_variant="vertical_9_16",
    )
