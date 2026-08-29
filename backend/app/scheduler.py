from __future__ import annotations

import asyncio
import logging
import signal

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import AsyncSessionFactory, engine
from app.services.background_jobs import upsert_worker_heartbeat
from app.services.job_scheduler import enqueue_due_work
from app.worker import build_instance_id


logger = logging.getLogger("aibos.scheduler")


async def run_scheduler() -> None:
    scheduler_id = build_instance_id("scheduler")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass
    configure_logging(role="scheduler")
    logger.info("scheduler_started", extra={"scheduler_id": scheduler_id})
    try:
        while not stop.is_set():
            try:
                async with AsyncSessionFactory() as session:
                    await upsert_worker_heartbeat(
                        session,
                        worker_id=scheduler_id,
                        role="scheduler",
                        version=settings.app_version,
                    )
                    counts = await enqueue_due_work(
                        session, batch_size=settings.scheduler_batch_size,
                    )
                    await session.commit()
                if any(counts.values()):
                    logger.info(
                        "scheduler_enqueued_due_work",
                        extra={
                            "scheduler_id": scheduler_id,
                            "enqueued_total": sum(counts.values()),
                        },
                    )
            except Exception as exc:
                logger.error(
                    "scheduler_iteration_failed",
                    extra={
                        "scheduler_id": scheduler_id,
                        "exception_type": type(exc).__name__,
                    },
                )
            if not stop.is_set():
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=settings.scheduler_poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
    finally:
        try:
            async with AsyncSessionFactory() as session:
                await upsert_worker_heartbeat(
                    session,
                    worker_id=scheduler_id,
                    role="scheduler",
                    version=settings.app_version,
                    status="stopped",
                )
                await session.commit()
        finally:
            await engine.dispose()
            logger.info("scheduler_stopped", extra={"scheduler_id": scheduler_id})


if __name__ == "__main__":
    asyncio.run(run_scheduler())
