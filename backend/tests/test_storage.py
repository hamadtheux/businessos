import os
import tempfile
from io import BytesIO
import unittest
from pathlib import Path


os.environ["AIBOS_DATABASE_URL"] = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = "x" * 32

from app.storage.base import InvalidStorageKeyError  # noqa: E402
from app.storage.local import LocalObjectStorage  # noqa: E402
from app.storage.s3 import S3ObjectStorage  # noqa: E402


class LocalObjectStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_put_public_url_and_idempotent_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = LocalObjectStorage(root, "/api/v1/media")
            key = "businesses/business-id/branding/logo/generated.png"

            await storage.put(key, b"sanitized", "image/png")

            self.assertEqual(
                (root / key).read_bytes(),
                b"sanitized",
            )
            self.assertEqual(
                await storage.get(key, max_bytes=1024),
                b"sanitized",
            )
            self.assertEqual(
                storage.public_url(key),
                "/api/v1/media/businesses/business-id/branding/logo/generated.png",
            )
            await storage.delete(key)
            await storage.delete(key)
            self.assertFalse((root / key).exists())

    async def test_get_fails_closed_when_local_object_exceeds_read_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalObjectStorage(
                Path(directory),
                "/api/v1/media",
            )
            key = "businesses/business-id/marketing/creatives/large.png"

            await storage.put(
                key,
                b"x" * 32,
                "image/png",
            )

            with self.assertRaisesRegex(
                Exception,
                "exceeds read limit",
            ):
                await storage.get(
                    key,
                    max_bytes=16,
                )

    async def test_path_traversal_and_absolute_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalObjectStorage(Path(directory), "/api/v1/media")
            invalid_keys = (
                "../outside.png",
                "businesses/../../outside.png",
                "/absolute.png",
                "businesses\\outside.png",
            )
            for key in invalid_keys:
                with self.subTest(key=key):
                    with self.assertRaises(InvalidStorageKeyError):
                        await storage.put(key, b"data", "image/png")
                    with self.assertRaises(InvalidStorageKeyError):
                        await storage.get(key, max_bytes=1024)
                    with self.assertRaises(InvalidStorageKeyError):
                        storage.public_url(key)


class _FakeS3Client:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kwargs: object) -> object:
        self.put_calls.append(kwargs)
        key = kwargs["Key"]
        body = kwargs["Body"]
        assert isinstance(key, str)
        assert isinstance(body, bytes)
        self.objects[key] = body
        return {}

    def get_object(self, **kwargs: object) -> object:
        self.get_calls.append(kwargs)
        key = kwargs["Key"]
        assert isinstance(key, str)
        content = self.objects[key]
        return {
            "ContentLength": len(content),
            "Body": BytesIO(content),
        }

    def delete_object(self, **kwargs: object) -> object:
        self.delete_calls.append(kwargs)
        return {}


class S3ObjectStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_uses_trusted_bucket_key_and_public_base(self) -> None:
        client = _FakeS3Client()
        storage = S3ObjectStorage(
            bucket="business-assets",
            public_base_url="https://cdn.example.test/assets/",
            region="auto",
            endpoint_url="https://objects.example.test",
            access_key_id="not-printed",
            secret_access_key="not-printed",
            client=client,
        )
        key = "businesses/business-id/branding/logo/generated.webp"

        await storage.put(key, b"sanitized", "image/webp")
        self.assertEqual(
            await storage.get(key, max_bytes=1024),
            b"sanitized",
        )
        await storage.delete(key)

        self.assertEqual(client.put_calls[0]["Bucket"], "business-assets")
        self.assertEqual(client.put_calls[0]["Key"], key)
        self.assertEqual(client.put_calls[0]["ContentType"], "image/webp")
        self.assertEqual(client.get_calls[0]["Bucket"], "business-assets")
        self.assertEqual(client.get_calls[0]["Key"], key)
        self.assertEqual(client.delete_calls[0]["Key"], key)
        self.assertEqual(
            storage.public_url(key),
            f"https://cdn.example.test/assets/{key}",
        )


    async def test_get_fails_closed_when_s3_object_exceeds_read_limit(self) -> None:
        client = _FakeS3Client()
        storage = S3ObjectStorage(
            bucket="business-assets",
            public_base_url="https://cdn.example.test/assets",
            region="auto",
            endpoint_url="https://objects.example.test",
            access_key_id="not-printed",
            secret_access_key="not-printed",
            client=client,
        )
        key = "businesses/business-id/marketing/creatives/large.png"

        await storage.put(
            key,
            b"x" * 32,
            "image/png",
        )

        with self.assertRaisesRegex(
            Exception,
            "exceeds read limit",
        ):
            await storage.get(
                key,
                max_bytes=16,
            )
