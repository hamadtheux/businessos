from app.services.social_media_profiles import (
    automatic_social_placement,
    automatic_social_profile,
    preferred_image_variant,
    preferred_video_variant,
    social_media_profile,
)


def test_image_defaults_are_fully_automatic() -> None:
    assert preferred_image_variant("instagram") == "portrait_4_5"
    assert preferred_image_variant("facebook") == "portrait_4_5"
    assert preferred_image_variant("linkedin") == "landscape_1_91_1"
    assert preferred_image_variant("tiktok") == "vertical_9_16"

    # YouTube has no standalone organic image-post destination.
    assert preferred_image_variant("youtube") is None


def test_video_defaults_match_each_platform_experience() -> None:
    assert automatic_social_placement(
        "instagram",
        media_type="video",
    ) == "reel"

    assert automatic_social_placement(
        "facebook",
        media_type="video",
    ) == "reel"

    assert automatic_social_placement(
        "linkedin",
        media_type="video",
    ) == "feed"

    assert automatic_social_placement(
        "tiktok",
        media_type="video",
    ) == "video"

    assert preferred_video_variant("instagram") == "vertical_9_16"
    assert preferred_video_variant("facebook") == "vertical_9_16"
    assert preferred_video_variant("linkedin") == "landscape_16_9"
    assert preferred_video_variant("tiktok") == "vertical_9_16"


def test_youtube_short_is_inferred_without_user_configuration() -> None:
    assert automatic_social_placement(
        "youtube",
        media_type="video",
        width=1080,
        height=1920,
        duration_seconds=60,
    ) == "short"

    assert automatic_social_placement(
        "youtube",
        media_type="video",
        width=1080,
        height=1080,
        duration_seconds=180,
    ) == "short"


def test_youtube_fails_safe_to_standard_video_when_short_is_not_proven() -> None:
    assert automatic_social_placement(
        "youtube",
        media_type="video",
        width=1920,
        height=1080,
        duration_seconds=60,
    ) == "standard_video"

    assert automatic_social_placement(
        "youtube",
        media_type="video",
        width=1080,
        height=1920,
        duration_seconds=181,
    ) == "standard_video"

    assert automatic_social_placement(
        "youtube",
        media_type="video",
    ) == "standard_video"


def test_unknown_destinations_never_guess() -> None:
    assert social_media_profile("unknown", "feed") is None
    assert automatic_social_profile(
        "unknown",
        media_type="image",
    ) is None
    assert automatic_social_profile(
        "instagram",
        media_type="audio",
    ) is None


def test_current_execution_capability_is_truthful() -> None:
    instagram = automatic_social_profile(
        "instagram",
        media_type="image",
    )
    facebook = automatic_social_profile(
        "facebook",
        media_type="image",
    )
    instagram_reel = automatic_social_profile(
        "instagram",
        media_type="video",
    )
    linkedin = automatic_social_profile(
        "linkedin",
        media_type="image",
    )

    assert instagram is not None
    assert instagram.provider_execution_supported is True

    assert facebook is not None
    assert facebook.provider_execution_supported is True

    assert instagram_reel is not None
    assert instagram_reel.provider_execution_supported is False

    assert linkedin is not None
    assert linkedin.provider_execution_supported is False
