from io import BytesIO
import unittest

from PIL import Image

from app.services.marketing_media import (
    PreparedMarketingMedia,
    build_marketing_image_variants,
)


def _source_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (800, 600), (30, 70, 120)).save(
        output,
        format="PNG",
    )
    return output.getvalue()


class MarketingMediaVariantTests(unittest.TestCase):
    def test_image_variants_preserve_original_and_create_required_ratios(self) -> None:
        original = _source_png()
        media = PreparedMarketingMedia(
            content=original,
            content_type="image/png",
            extension="png",
            media_type="image",
            width=800,
            height=600,
            duration_seconds=None,
            original_name="campaign.png",
        )

        variants = build_marketing_image_variants(media)

        self.assertEqual(media.content, original)
        self.assertEqual(
            [(item.key, item.width, item.height, item.aspect_ratio) for item in variants],
            [
                ("square_1_1", 1200, 1200, "1:1"),
                ("landscape_1_91_1", 1200, 628, "1.91:1"),
                ("portrait_4_5", 1080, 1350, "4:5"),
                ("vertical_9_16", 1080, 1920, "9:16"),
            ],
        )

        for variant in variants:
            self.assertEqual(variant.content_type, "image/jpeg")
            self.assertEqual(variant.extension, "jpg")
            with Image.open(BytesIO(variant.content)) as rendered:
                self.assertEqual(rendered.size, (variant.width, variant.height))

    def test_video_is_not_fake_resized_by_image_pipeline(self) -> None:
        media = PreparedMarketingMedia(
            content=b"video",
            content_type="video/mp4",
            extension="mp4",
            media_type="video",
            width=None,
            height=None,
            duration_seconds=15,
            original_name="campaign.mp4",
        )

        self.assertEqual(build_marketing_image_variants(media), ())


if __name__ == "__main__":
    unittest.main()
