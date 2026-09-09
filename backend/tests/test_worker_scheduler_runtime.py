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
from app.services.job_handlers import HandlerOutcome  # noqa: E402


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        return None


class _WorkerSession(_Session):
    def __init__(self):
        self.failed_transaction = False
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.failed_transaction:
            raise AssertionError("PendingRollbackError would mask the primary failure")

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self.failed_transaction = False


class _SessionFactory:
    def __call__(self):
        return _Session()


class _WorkerSessionFactory:
    def __init__(self):
        self.sessions = []

    def __call__(self):
        session = _WorkerSession()
        self.sessions.append(session)
        return session


class WorkerSchedulerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_handler_failure_is_clean_before_worker_commit(self) -> None:
        factory = _WorkerSessionFactory()
        job = SimpleNamespace(
            id=uuid4(),
            business_id=uuid4(),
            job_type="generate_creative_asset",
            attempt_count=1,
        )

        async def failed_dispatch(session, _job):
            session.failed_transaction = True
            await session.rollback()
            return HandlerOutcome(False, "dependency_unavailable", True)

        with (
            patch.object(worker_module, "AsyncSessionFactory", factory),
            patch.object(worker_module, "dispatch_job_handler", new=AsyncMock(side_effect=failed_dispatch)),
            patch.object(worker_module, "record_job_failure", new=AsyncMock()),
            patch.object(worker_module, "record_job_success", new=AsyncMock()),
        ):
            await worker_module.process_claimed_job(job, worker_id="worker-test")

        self.assertGreaterEqual(len(factory.sessions), 2)
        self.assertEqual(factory.sessions[0].rollback_calls, 1)
        self.assertEqual(factory.sessions[0].commit_calls, 1)

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
