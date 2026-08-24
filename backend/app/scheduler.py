from __future__ import annotations

import asyncio
import json
import logging
import signal

from app.core.config import settings
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
    logging.basicConfig(level=logging.INFO)
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
                    logger.info(json.dumps({
                        "event": "scheduler_enqueued_due_work",
                        "scheduler_id": scheduler_id,
                        "counts": counts,
                    }))
            except Exception:
                logger.exception(json.dumps({
                    "event": "scheduler_iteration_failed",
                    "scheduler_id": scheduler_id,
                }))
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


if __name__ == "__main__":
    asyncio.run(run_scheduler())
