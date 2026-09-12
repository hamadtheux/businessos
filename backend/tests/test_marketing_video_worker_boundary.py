from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

import app.worker as worker_module  # noqa: E402
from app.services.job_handlers import (  # noqa: E402
    handle_prepare_marketing_video,
)


class _OutcomeSession:
    def __init__(self) -> None:
        self.committed = False
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.exited = True

    async def commit(self) -> None:
        self.committed = True


class _OutcomeSessionFactory:
    def __init__(self) -> None:
        self.calls = 0
        self.sessions: list[_OutcomeSession] = []

    def __call__(self):
        self.calls += 1
        session = _OutcomeSession()
        self.sessions.append(session)
        return session


def _job():
    return SimpleNamespace(
        id=uuid4(),
        business_id=uuid4(),
        creative_asset_id=uuid4(),
        job_type="prepare_marketing_video",
        attempt_count=1,
    )


class MarketingVideoWorkerBoundaryTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_generic_handler_refuses_video_preparation(self) -> None:
        outcome = await handle_prepare_marketing_video(
            None,  # type: ignore[arg-type]
            _job(),  # type: ignore[arg-type]
        )

        self.assertFalse(outcome.succeeded)
        self.assertEqual(
            outcome.failure_code,
            "invalid_job_state",
        )
        self.assertFalse(outcome.retryable)

    async def test_worker_intercepts_before_generic_transaction(
        self,
    ) -> None:
        job = _job()
        factory = _OutcomeSessionFactory()

        generic_dispatch = AsyncMock()
        record_success = AsyncMock()
        record_failure = AsyncMock()

        async def special_dispatch(_job):
            # No generic worker transaction may exist while the long-running
            # video dispatcher is entered.
            self.assertEqual(factory.calls, 0)
            self.assertIs(_job, job)

            return SimpleNamespace(
                succeeded=True,
                failure_code=None,
                retryable=False,
            )

        with (
            patch.object(
                worker_module,
                "AsyncSessionFactory",
                new=factory,
            ),
            patch.object(
                worker_module,
                "_maintain_job_lease",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                worker_module,
                "dispatch_marketing_video_preparation_job",
                new=AsyncMock(side_effect=special_dispatch),
            ) as video_dispatch,
            patch.object(
                worker_module,
                "dispatch_job_handler",
                new=generic_dispatch,
            ),
            patch.object(
                worker_module,
                "record_job_success",
                new=record_success,
            ),
            patch.object(
                worker_module,
                "record_job_failure",
                new=record_failure,
            ),
        ):
            await worker_module.process_claimed_job(
                job,  # type: ignore[arg-type]
                worker_id="video-worker-test",
            )

        video_dispatch.assert_awaited_once_with(job)
        generic_dispatch.assert_not_awaited()

        # Exactly one worker-owned transaction is opened after dispatch:
        # persistence of the BackgroundJob outcome.
        self.assertEqual(factory.calls, 1)
        self.assertTrue(factory.sessions[0].committed)
        self.assertTrue(factory.sessions[0].exited)

        record_success.assert_awaited_once_with(
            factory.sessions[0],
            job_id=job.id,
            worker_id="video-worker-test",
        )
        record_failure.assert_not_awaited()

    async def test_retryable_dispatch_failure_reaches_job_retry_boundary(
        self,
    ) -> None:
        job = _job()
        factory = _OutcomeSessionFactory()

        record_success = AsyncMock()
        record_failure = AsyncMock()

        with (
            patch.object(
                worker_module,
                "AsyncSessionFactory",
                new=factory,
            ),
            patch.object(
                worker_module,
                "_maintain_job_lease",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                worker_module,
                "dispatch_marketing_video_preparation_job",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        succeeded=False,
                        failure_code="dependency_unavailable",
                        retryable=True,
                    )
                ),
            ),
            patch.object(
                worker_module,
                "dispatch_job_handler",
                new=AsyncMock(),
            ) as generic_dispatch,
            patch.object(
                worker_module,
                "record_job_success",
                new=record_success,
            ),
            patch.object(
                worker_module,
                "record_job_failure",
                new=record_failure,
            ),
        ):
            await worker_module.process_claimed_job(
                job,  # type: ignore[arg-type]
                worker_id="video-worker-test",
            )

        generic_dispatch.assert_not_awaited()
        record_success.assert_not_awaited()

        record_failure.assert_awaited_once()
        kwargs = record_failure.await_args.kwargs

        self.assertEqual(kwargs["job_id"], job.id)
        self.assertEqual(
            kwargs["worker_id"],
            "video-worker-test",
        )
        self.assertEqual(
            kwargs["failure_code"],
            "dependency_unavailable",
        )
        self.assertTrue(kwargs["retryable"])


if __name__ == "__main__":
    unittest.main()
