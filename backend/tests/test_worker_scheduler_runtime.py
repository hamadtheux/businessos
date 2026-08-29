from __future__ import annotations

import asyncio
import os
import signal
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app import scheduler as scheduler_module  # noqa: E402
from app import worker as worker_module  # noqa: E402


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        return None


class _SessionFactory:
    def __call__(self):
        return _Session()


class WorkerSchedulerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_claim_failure_does_not_kill_process_loop(self) -> None:
        stop_event = asyncio.Event()
        iterations = 0

        async def heartbeat(*args, **kwargs):
            return None

        async def claim(*args, **kwargs):
            nonlocal iterations
            iterations += 1
            if iterations == 1:
                raise RuntimeError("database temporarily unavailable")
            stop_event.set()
            return []

        with (
            patch.object(worker_module, "AsyncSessionFactory", _SessionFactory()),
            patch.object(worker_module, "upsert_worker_heartbeat", new=AsyncMock(side_effect=heartbeat)),
            patch.object(worker_module, "claim_jobs", new=AsyncMock(side_effect=claim)),
            patch.object(worker_module, "build_instance_id", return_value="worker-test"),
            patch.object(worker_module, "configure_logging"),
            patch.object(
                worker_module,
                "engine",
                SimpleNamespace(dispose=AsyncMock()),
            ),
            patch.object(asyncio, "Event", return_value=stop_event),
        ):
            await worker_module.run_worker()

        self.assertGreaterEqual(iterations, 2)

    async def test_scheduler_iteration_failure_recovers(self) -> None:
        stop_event = asyncio.Event()
        iterations = 0

        async def enqueue(*args, **kwargs):
            nonlocal iterations
            iterations += 1
            if iterations == 1:
                raise RuntimeError("database temporarily unavailable")
            stop_event.set()
            return {}

        with (
            patch.object(scheduler_module, "AsyncSessionFactory", _SessionFactory()),
            patch.object(scheduler_module, "upsert_worker_heartbeat", new=AsyncMock()),
            patch.object(scheduler_module, "enqueue_due_work", new=AsyncMock(side_effect=enqueue)),
            patch.object(scheduler_module, "build_instance_id", return_value="scheduler-test"),
            patch.object(scheduler_module, "configure_logging"),
            patch.object(
                scheduler_module,
                "engine",
                SimpleNamespace(dispose=AsyncMock()),
            ),
            patch.object(asyncio, "Event", return_value=stop_event),
        ):
            await scheduler_module.run_scheduler()

        self.assertGreaterEqual(iterations, 2)


if __name__ == "__main__":
    unittest.main()
