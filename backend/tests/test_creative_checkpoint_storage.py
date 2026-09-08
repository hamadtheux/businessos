from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.storage.base import ObjectNotFoundError
from app.storage.local import LocalObjectStorage


class CreativeCheckpointStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_object_has_distinct_not_found_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalObjectStorage(
                root_directory=Path(directory),
                public_path="/api/v1/media",
            )
            with self.assertRaises(ObjectNotFoundError):
                await storage.get(
                    "businesses/test/marketing/creatives/test/raw/"
                    "generation-1/attempt-1.png",
                    max_bytes=1024,
                )

    async def test_checkpoint_round_trip_is_bounded_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalObjectStorage(
                root_directory=Path(directory),
                public_path="/api/v1/media",
            )
            key = (
                "businesses/test/marketing/creatives/test/raw/"
                "generation-1/attempt-1.png"
            )
            payload = b"paid-provider-result"
            await storage.put(key, payload, "image/png")
            restored = await storage.get(key, max_bytes=1024)
            self.assertEqual(restored, payload)


if __name__ == "__main__":
    unittest.main()
