from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import socket
from secrets import token_hex

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import AsyncSessionFactory, engine
from app.models.background_job import BackgroundJob
from app.services.background_jobs import (
    claim_jobs,
    record_job_failure,
    record_job_success,
    upsert_worker_heartbeat,
)
from app.services.job_handlers import HandlerOutcome, dispatch_job_handler
from app.services.action_dispatcher import dispatch_action_execution_job
from app.services.conversation_message_dispatcher import (
    dispatch_conversation_message_job,
)


logger = logging.getLogger("aibos.worker")


def build_instance_id(role: str) -> str:
    host = re.sub(r"[^a-zA-Z0-9-]", "-", socket.gethostname()).strip("-")[:24] or "host"
    return f"{role}-{host}-{os.getpid()}-{token_hex(4)}"[:96]


async def process_claimed_job(job: BackgroundJob, *, worker_id: str) -> None:
    outcome: HandlerOutcome
    try:
        if job.job_type == "dispatch_action_execution":
            dispatched = await dispatch_action_execution_job(job)
            outcome = HandlerOutcome(
                dispatched.succeeded,
                dispatched.failure_code,
                dispatched.retryable,
            )
        elif job.job_type == "dispatch_conversation_message":
            dispatched = await dispatch_conversation_message_job(job)
            outcome = HandlerOutcome(
                dispatched.succeeded,
                dispatched.failure_code,
                dispatched.retryable,
            )
        else:
            async with AsyncSessionFactory() as session:
                outcome = await dispatch_job_handler(session, job)
                await session.commit()
    except Exception as exc:
        logger.error(
            "job_handler_failed",
            extra={
                "job_id": str(job.id),
                "business_id": str(job.business_id),
                "job_type": job.job_type,
                "worker_id": worker_id,
                "attempt": job.attempt_count,
                "exception_type": type(exc).__name__,
            },
        )
        outcome = HandlerOutcome(False, "dependency_unavailable", True)
    try:
        async with AsyncSessionFactory() as session:
            if outcome.succeeded:
                await record_job_success(session, job_id=job.id, worker_id=worker_id)
            else:
                await record_job_failure(
                    session,
                    job_id=job.id,
                    worker_id=worker_id,
                    failure_code=outcome.failure_code or "invalid_job_state",
                    retryable=outcome.retryable,
                    retry_after_seconds=outcome.retry_after_seconds,
                )
            await session.commit()
    except Exception as exc:
        # A lost lease is expected when a replacement worker has reclaimed it.
        # The committed domain work remains safe to replay idempotently.
        logger.error(
            "job_outcome_persist_failed",
            extra={
                "job_id": str(job.id),
                "business_id": str(job.business_id),
                "job_type": job.job_type,
                "worker_id": worker_id,
                "attempt": job.attempt_count,
                "exception_type": type(exc).__name__,
            },
        )


async def run_worker() -> None:
    worker_id = build_instance_id("worker")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass
    configure_logging(role="worker")
    logger.info("worker_started", extra={"worker_id": worker_id})
    try:
        while not stop.is_set():
            try:
                async with AsyncSessionFactory() as session:
                    await upsert_worker_heartbeat(
                        session,
                        worker_id=worker_id,
                        role="worker",
                        version=settings.app_version,
                    )
                    jobs = await claim_jobs(
                        session,
                        worker_id=worker_id,
                        batch_size=settings.job_batch_size,
                        lease_seconds=settings.job_lease_seconds,
                    )
                    await session.commit()
            except Exception as exc:
                logger.error(
                    "worker_iteration_failed",
                    extra={
                        "worker_id": worker_id,
                        "exception_type": type(exc).__name__,
                    },
                )
                if not stop.is_set():
                    try:
                        await asyncio.wait_for(
                            stop.wait(),
                            timeout=settings.job_poll_interval_seconds,
                        )
                    except TimeoutError:
                        pass
                continue

            for job in jobs:
                await process_claimed_job(job, worker_id=worker_id)
                try:
                    async with AsyncSessionFactory() as session:
                        await upsert_worker_heartbeat(
                            session,
                            worker_id=worker_id,
                            role="worker",
                            version=settings.app_version,
                        )
                        await session.commit()
                except Exception as exc:
                    logger.error(
                        "worker_heartbeat_failed",
                        extra={
                            "worker_id": worker_id,
                            "job_id": str(job.id),
                            "business_id": str(job.business_id),
                            "exception_type": type(exc).__name__,
                        },
                    )
                if stop.is_set():
                    break

            if not jobs and not stop.is_set():
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=settings.job_poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
    finally:
        try:
            async with AsyncSessionFactory() as session:
                await upsert_worker_heartbeat(
                    session,
                    worker_id=worker_id,
                    role="worker",
                    version=settings.app_version,
                    status="stopped",
                )
                await session.commit()
        finally:
            await engine.dispose()
            logger.info("worker_stopped", extra={"worker_id": worker_id})


if __name__ == "__main__":
    asyncio.run(run_worker())
