from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal


SocialPublishPlatform = Literal[
    "instagram",
    "facebook",
    "linkedin",
    "tiktok",
    "youtube",
]

SocialPlacement = Literal[
    "feed",
    "story",
    "reel",
    "photo",
    "video",
    "short",
    "standard_video",
]

SocialMediaType = Literal["image", "video"]

ImageVariant = Literal[
    "square_1_1",
    "landscape_1_91_1",
    "portrait_4_5",
    "vertical_9_16",
]


@dataclass(frozen=True, slots=True)
class SocialMediaProfile:
    """
    Server-owned media requirements for one publishing destination.

    Users choose a platform and upload media. They never need to understand
    pixel sizes, derivative names, or aspect-ratio rules.

    provider_execution_supported describes CURRENT 9D Brain publishing
    capability, not the social network's general capabilities.
    """

    platform: SocialPublishPlatform
    placement: SocialPlacement
    supported_media_types: frozenset[SocialMediaType]

    image_variant: ImageVariant | None

    target_width: int
    target_height: int
    aspect_ratio: str

    provider_execution_supported: bool


_PROFILES: Final = MappingProxyType(
    {
        # Instagram
        ("instagram", "feed"): SocialMediaProfile(
            platform="instagram",
            placement="feed",
            supported_media_types=frozenset({"image"}),
            image_variant="portrait_4_5",
            target_width=1080,
            target_height=1350,
            aspect_ratio="4:5",
            provider_execution_supported=True,
        ),
        ("instagram", "story"): SocialMediaProfile(
            platform="instagram",
            placement="story",
            supported_media_types=frozenset({"image", "video"}),
            image_variant="vertical_9_16",
            target_width=1080,
            target_height=1920,
            aspect_ratio="9:16",
            provider_execution_supported=False,
        ),
        ("instagram", "reel"): SocialMediaProfile(
            platform="instagram",
            placement="reel",
            supported_media_types=frozenset({"video"}),
            image_variant=None,
            target_width=1080,
            target_height=1920,
            aspect_ratio="9:16",
            provider_execution_supported=False,
        ),

        # Facebook
        ("facebook", "feed"): SocialMediaProfile(
            platform="facebook",
            placement="feed",
            supported_media_types=frozenset({"image"}),
            image_variant="portrait_4_5",
            target_width=1080,
            target_height=1350,
            aspect_ratio="4:5",
            provider_execution_supported=True,
        ),
        ("facebook", "story"): SocialMediaProfile(
            platform="facebook",
            placement="story",
            supported_media_types=frozenset({"image", "video"}),
            image_variant="vertical_9_16",
            target_width=1080,
            target_height=1920,
            aspect_ratio="9:16",
            provider_execution_supported=False,
        ),
        ("facebook", "reel"): SocialMediaProfile(
            platform="facebook",
            placement="reel",
            supported_media_types=frozenset({"video"}),
            image_variant=None,
            target_width=1080,
            target_height=1920,
            aspect_ratio="9:16",
            provider_execution_supported=False,
        ),

        # LinkedIn
        ("linkedin", "feed"): SocialMediaProfile(
            platform="linkedin",
            placement="feed",
            supported_media_types=frozenset({"image", "video"}),
            image_variant="landscape_1_91_1",
            target_width=1200,
            target_height=628,
            aspect_ratio="1.91:1",
            provider_execution_supported=False,
        ),

        # TikTok
        ("tiktok", "photo"): SocialMediaProfile(
            platform="tiktok",
            placement="photo",
            supported_media_types=frozenset({"image"}),
            image_variant="vertical_9_16",
            target_width=1080,
            target_height=1920,
            aspect_ratio="9:16",
            provider_execution_supported=False,
        ),
        ("tiktok", "video"): SocialMediaProfile(
            platform="tiktok",
            placement="video",
            supported_media_types=frozenset({"video"}),
            image_variant=None,
            target_width=1080,
            target_height=1920,
            aspect_ratio="9:16",
            provider_execution_supported=False,
        ),

        # YouTube
        ("youtube", "short"): SocialMediaProfile(
            platform="youtube",
            placement="short",
            supported_media_types=frozenset({"video"}),
            image_variant=None,
            target_width=1080,
            target_height=1920,
            aspect_ratio="9:16",
            provider_execution_supported=False,
        ),
        ("youtube", "standard_video"): SocialMediaProfile(
            platform="youtube",
            placement="standard_video",
            supported_media_types=frozenset({"video"}),
            image_variant=None,
            target_width=1920,
            target_height=1080,
            aspect_ratio="16:9",
            provider_execution_supported=False,
        ),
    }
)


def social_media_profile(
    platform: str,
    placement: str,
) -> SocialMediaProfile | None:
    normalized_platform = platform.strip().casefold()
    normalized_placement = placement.strip().casefold()

    return _PROFILES.get(
        (normalized_platform, normalized_placement)
    )


def automatic_social_placement(
    platform: str,
    *,
    media_type: str,
    width: int | None = None,
    height: int | None = None,
    duration_seconds: int | None = None,
) -> SocialPlacement | None:
    """
    Choose the least-confusing default publishing intent.

    This function never invents support:
    - Images default to each platform's normal photo/feed experience.
    - Instagram/Facebook videos default to Reels.
    - TikTok videos default to native video.
    - YouTube automatically becomes a Short only when we have enough
      authoritative metadata to know it is vertical/square and <= 3 minutes.
      Otherwise it remains a standard video.
    """

    normalized_platform = platform.strip().casefold()
    normalized_media_type = media_type.strip().casefold()

    if normalized_media_type not in {"image", "video"}:
        return None

    if normalized_platform == "instagram":
        return "feed" if normalized_media_type == "image" else "reel"

    if normalized_platform == "facebook":
        return "feed" if normalized_media_type == "image" else "reel"

    if normalized_platform == "linkedin":
        return "feed"

    if normalized_platform == "tiktok":
        return "photo" if normalized_media_type == "image" else "video"

    if normalized_platform == "youtube":
        if normalized_media_type != "video":
            return None

        is_vertical_or_square = (
            width is not None
            and height is not None
            and width > 0
            and height > 0
            and height >= width
        )

        short_duration = (
            duration_seconds is not None
            and 0 < duration_seconds <= 180
        )

        if is_vertical_or_square and short_duration:
            return "short"

        return "standard_video"

    return None


def automatic_social_profile(
    platform: str,
    *,
    media_type: str,
    width: int | None = None,
    height: int | None = None,
    duration_seconds: int | None = None,
) -> SocialMediaProfile | None:
    placement = automatic_social_placement(
        platform,
        media_type=media_type,
        width=width,
        height=height,
        duration_seconds=duration_seconds,
    )

    if placement is None:
        return None

    profile = social_media_profile(platform, placement)

    if (
        profile is None
        or media_type.strip().casefold()
        not in profile.supported_media_types
    ):
        return None

    return profile


def preferred_image_variant(
    platform: str,
) -> ImageVariant | None:
    profile = automatic_social_profile(
        platform,
        media_type="image",
    )

    return profile.image_variant if profile else None
