import os
import unittest
from io import BytesIO

from PIL import Image
from PIL.PngImagePlugin import PngInfo


os.environ["AIBOS_DATABASE_URL"] = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = "x" * 32

from app.exceptions.logo import (  # noqa: E402
    LogoTooLargeError,
    LogoValidationError,
)
from app.services.logo_image import (  # noqa: E402
    MAX_LOGO_DIMENSION,
    MAX_LOGO_UPLOAD_BYTES,
    sanitize_logo_bytes,
)


class LogoImageSanitizationTests(unittest.TestCase):
    def test_png_jpeg_and_webp_are_decoded_and_reencoded(self) -> None:
        formats = {
            "PNG": ("image/png", "png"),
            "JPEG": ("image/jpeg", "jpg"),
            "WEBP": ("image/webp", "webp"),
        }
        for image_format, expected in formats.items():
            with self.subTest(image_format=image_format):
                source = _image_bytes(image_format)
                sanitized = sanitize_logo_bytes(source)

                self.assertEqual(
                    (sanitized.content_type, sanitized.extension),
                    expected,
                )
                self.assertEqual((sanitized.width, sanitized.height), (40, 20))
                self.assertNotEqual(sanitized.content, source)
                with Image.open(BytesIO(sanitized.content)) as decoded:
                    self.assertEqual(decoded.format, image_format)
                    self.assertEqual(decoded.size, (40, 20))

    def test_transparent_png_remains_transparent(self) -> None:
        image = Image.new("RGBA", (16, 12), (255, 0, 0, 0))
        image.putpixel((3, 3), (10, 20, 30, 180))
        source = BytesIO()
        image.save(source, format="PNG")

        sanitized = sanitize_logo_bytes(source.getvalue())

        with Image.open(BytesIO(sanitized.content)) as decoded:
            self.assertIn("A", decoded.getbands())
            self.assertEqual(decoded.getpixel((0, 0))[3], 0)
            self.assertEqual(decoded.getpixel((3, 3))[3], 180)

    def test_metadata_is_not_blindly_preserved(self) -> None:
        metadata = PngInfo()
        metadata.add_text("Comment", "private upload metadata")
        image = Image.new("RGB", (12, 8), (20, 40, 60))
        source = BytesIO()
        image.save(source, format="PNG", pnginfo=metadata)

        sanitized = sanitize_logo_bytes(source.getvalue())

        with Image.open(BytesIO(sanitized.content)) as decoded:
            self.assertNotIn("Comment", decoded.info)
            self.assertNotIn("exif", decoded.info)

    def test_fake_corrupt_and_unsupported_images_are_rejected(self) -> None:
        gif = BytesIO()
        Image.new("RGB", (8, 8)).save(gif, format="GIF")
        invalid_sources = (
            b"not an image",
            b"MZ" + b"executable" * 20,
            gif.getvalue(),
            _image_bytes("PNG")[:20],
        )
        for source in invalid_sources:
            with self.subTest(size=len(source)):
                with self.assertRaises(LogoValidationError):
                    sanitize_logo_bytes(source)

    def test_upload_byte_limit_is_enforced(self) -> None:
        with self.assertRaises(LogoTooLargeError):
            sanitize_logo_bytes(b"x" * (MAX_LOGO_UPLOAD_BYTES + 1))

    def test_absurd_dimensions_are_rejected_before_full_decode(self) -> None:
        image = Image.new("RGB", (MAX_LOGO_DIMENSION + 1, 1))
        source = BytesIO()
        image.save(source, format="PNG")

        with self.assertRaises(LogoValidationError):
            sanitize_logo_bytes(source.getvalue())


def _image_bytes(image_format: str) -> bytes:
    mode = "RGB"
    image = Image.new(mode, (40, 20), (32, 96, 160))
    output = BytesIO()
    save_options = {"quality": 80} if image_format in {"JPEG", "WEBP"} else {}
    image.save(output, format=image_format, **save_options)
    return output.getvalue()
