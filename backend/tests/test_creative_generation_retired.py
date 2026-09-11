import os
import unittest
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault(
    "AIBOS_AUTH_SECRET_KEY",
    "x" * 32,
)

from app.api.v1.marketing import (  # noqa: E402
    create_creative_brief,
    create_video_creative_strategy,
    generate_creative_asset,
    regenerate_creative_asset,
    start_video_generation,
)
from app.services.job_handlers import (  # noqa: E402
    handle_generate_creative_asset,
)


class CreativeGenerationRetirementTests(
    unittest.IsolatedAsyncioTestCase
):
    async def _assert_retired(self, awaitable):
        with self.assertRaises(HTTPException) as captured:
            await awaitable

        self.assertEqual(
            captured.exception.status_code,
            410,
        )
        self.assertEqual(
            captured.exception.detail["code"],
            "creative_generation_retired",
        )

    async def test_image_brief_endpoint_is_retired(self):
        await self._assert_retired(
            create_creative_brief(
                data=None,
                access=None,
            )
        )

    async def test_video_strategy_endpoint_is_retired(self):
        await self._assert_retired(
            create_video_creative_strategy(
                data=None,
                access=None,
            )
        )

    async def test_image_generate_endpoint_is_retired(self):
        await self._assert_retired(
            generate_creative_asset(
                creative_asset_id=uuid4(),
                access=None,
            )
        )

    async def test_image_regenerate_endpoint_is_retired(self):
        await self._assert_retired(
            regenerate_creative_asset(
                creative_asset_id=uuid4(),
                access=None,
                data=None,
            )
        )

    async def test_video_generate_endpoint_is_retired(self):
        await self._assert_retired(
            start_video_generation(
                creative_asset_id=uuid4(),
                access=None,
            )
        )

    async def test_legacy_worker_job_never_runs_provider(self):
        result = await handle_generate_creative_asset(
            None,
            SimpleNamespace(
                creative_asset_id=uuid4(),
            ),
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(
            result.failure_code,
            "external_execution_disabled",
        )
        self.assertFalse(result.retryable)


if __name__ == "__main__":
    unittest.main()
