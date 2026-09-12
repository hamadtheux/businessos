from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.storage.base import (  # noqa: E402
    InvalidStorageKeyError,
    StorageOperationError,
)
from app.storage.local import LocalObjectStorage  # noqa: E402
from app.storage.s3 import S3ObjectStorage  # noqa: E402


class _StreamingS3Client:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.uploaded: bytes | None = None
        self.body_was_bytes: bool | None = None

    def put_object(self, **kwargs: object) -> object:
        self.put_calls.append(kwargs)

        body = kwargs["Body"]
        self.body_was_bytes = isinstance(body, bytes)

        if isinstance(body, bytes):
            self.uploaded = body
        elif hasattr(body, "read"):
            content = body.read()
            if not isinstance(content, bytes):
                raise TypeError("file body did not return bytes")
            self.uploaded = content
        else:
            raise TypeError("unsupported S3 body")

        return {}


def _s3(client: _StreamingS3Client) -> S3ObjectStorage:
    return S3ObjectStorage(
        bucket="business-assets",
        public_base_url="https://cdn.example.test/assets",
        region="auto",
        endpoint_url="https://objects.example.test",
        access_key_id="not-printed",
        secret_access_key="not-printed",
        client=client,
    )


class LocalFileUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_put_file_atomically_copies_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "rendered.mp4"
            source.write_bytes(b"rendered-video" * 10)

            storage = LocalObjectStorage(
                root / "storage",
                "/api/v1/media",
            )
            key = (
                "businesses/business-id/marketing/uploads/asset-id/"
                "variants/vertical_9_16.mp4"
            )

            await storage.put_file(
                key,
                source,
                "video/mp4",
                max_bytes=1024,
            )

            self.assertEqual(
                (root / "storage" / key).read_bytes(),
                source.read_bytes(),
            )

    async def test_put_file_rejects_oversize_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "rendered.mp4"
            source.write_bytes(b"x" * 64)

            storage = LocalObjectStorage(
                root / "storage",
                "/api/v1/media",
            )
            key = (
                "businesses/business-id/marketing/uploads/asset-id/"
                "variants/vertical_9_16.mp4"
            )

            with self.assertRaisesRegex(
                StorageOperationError,
                "exceeds file limit",
            ):
                await storage.put_file(
                    key,
                    source,
                    "video/mp4",
                    max_bytes=32,
                )

            self.assertFalse(
                (root / "storage" / key).exists()
            )

    async def test_put_file_still_rejects_traversal_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "rendered.mp4"
            source.write_bytes(b"video")

            storage = LocalObjectStorage(
                root / "storage",
                "/api/v1/media",
            )

            with self.assertRaises(InvalidStorageKeyError):
                await storage.put_file(
                    "../outside.mp4",
                    source,
                    "video/mp4",
                    max_bytes=1024,
                )


class S3FileUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_put_file_streams_file_handle_to_trusted_s3_key(self) -> None:
        client = _StreamingS3Client()
        storage = _s3(client)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "rendered.mp4"
            content = b"normalized-video" * 20
            source.write_bytes(content)

            key = (
                "businesses/business-id/marketing/uploads/asset-id/"
                "variants/landscape_16_9.mp4"
            )

            await storage.put_file(
                key,
                source,
                "video/mp4",
                max_bytes=4096,
            )

        self.assertEqual(len(client.put_calls), 1)
        self.assertEqual(
            client.put_calls[0]["Bucket"],
            "business-assets",
        )
        self.assertEqual(
            client.put_calls[0]["Key"],
            key,
        )
        self.assertEqual(
            client.put_calls[0]["ContentType"],
            "video/mp4",
        )
        self.assertFalse(client.body_was_bytes)
        self.assertEqual(client.uploaded, content)

    async def test_put_file_rejects_oversize_before_s3_request(self) -> None:
        client = _StreamingS3Client()
        storage = _s3(client)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "rendered.mp4"
            source.write_bytes(b"x" * 64)

            with self.assertRaisesRegex(
                StorageOperationError,
                "exceeds file limit",
            ):
                await storage.put_file(
                    "businesses/business-id/video.mp4",
                    source,
                    "video/mp4",
                    max_bytes=32,
                )

        self.assertEqual(client.put_calls, [])


if __name__ == "__main__":
    unittest.main()


class _StreamingDownloadBody:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.offset = 0
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)

        if size < 0:
            raise AssertionError(
                "streaming download attempted an unbounded read"
            )

        chunk = self.content[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _StreamingDownloadS3Client(_StreamingS3Client):
    def __init__(self, content: bytes) -> None:
        super().__init__()
        self.content = content
        self.body: _StreamingDownloadBody | None = None
        self.get_calls: list[dict[str, object]] = []

    def get_object(self, **kwargs: object) -> object:
        self.get_calls.append(kwargs)
        self.body = _StreamingDownloadBody(self.content)

        return {
            "ContentLength": len(self.content),
            "Body": self.body,
        }


class LocalFileDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_file_copies_object_without_bytes_materialization(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            storage = LocalObjectStorage(
                root / "storage",
                "/api/v1/media",
            )
            key = (
                "businesses/business-id/marketing/uploads/asset-id/"
                "source.mp4"
            )
            content = b"trusted-video-source" * 50

            await storage.put(
                key,
                content,
                "video/mp4",
            )

            destination = root / "worker" / "source.mp4"

            await storage.get_file(
                key,
                destination,
                max_bytes=4096,
            )

            self.assertEqual(
                destination.read_bytes(),
                content,
            )

    async def test_get_file_rejects_oversize_without_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            storage = LocalObjectStorage(
                root / "storage",
                "/api/v1/media",
            )
            key = "businesses/business-id/video.mp4"

            await storage.put(
                key,
                b"x" * 64,
                "video/mp4",
            )

            destination = root / "worker" / "source.mp4"

            with self.assertRaisesRegex(
                StorageOperationError,
                "exceeds read limit",
            ):
                await storage.get_file(
                    key,
                    destination,
                    max_bytes=32,
                )

            self.assertFalse(destination.exists())


class S3FileDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_file_streams_bounded_s3_body_to_disk(self) -> None:
        content = b"remote-video-source" * 100
        client = _StreamingDownloadS3Client(content)
        storage = _s3(client)

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "source.mp4"

            key = (
                "businesses/business-id/marketing/uploads/asset-id/"
                "source.mp4"
            )

            await storage.get_file(
                key,
                destination,
                max_bytes=8192,
            )

            self.assertEqual(
                destination.read_bytes(),
                content,
            )

        self.assertEqual(
            client.get_calls[0]["Bucket"],
            "business-assets",
        )
        self.assertEqual(
            client.get_calls[0]["Key"],
            key,
        )

        self.assertIsNotNone(client.body)
        assert client.body is not None

        self.assertTrue(client.body.closed)
        self.assertTrue(client.body.read_sizes)
        self.assertTrue(
            all(size > 0 for size in client.body.read_sizes)
        )

    async def test_get_file_rejects_content_length_before_body_read(
        self,
    ) -> None:
        content = b"x" * 64
        client = _StreamingDownloadS3Client(content)
        storage = _s3(client)

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "source.mp4"

            with self.assertRaisesRegex(
                StorageOperationError,
                "exceeds read limit",
            ):
                await storage.get_file(
                    "businesses/business-id/video.mp4",
                    destination,
                    max_bytes=32,
                )

            self.assertFalse(destination.exists())

        self.assertIsNotNone(client.body)
        assert client.body is not None

        self.assertEqual(
            client.body.read_sizes,
            [],
        )
        self.assertTrue(client.body.closed)
