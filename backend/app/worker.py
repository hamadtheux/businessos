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
    renew_job_lease,
    upsert_worker_heartbeat,
)
from app.services.job_handlers import HandlerOutcome, dispatch_job_handler
from app.services.action_dispatcher import dispatch_action_execution_job
from app.services.conversation_message_dispatcher import (
    dispatch_conversation_message_job,
)
from app.services.marketing_video_dispatcher import (
    dispatch_marketing_video_preparation_job,
)


logger = logging.getLogger("aibos.worker")


async def _maintain_job_lease(
    job: BackgroundJob,
    *,
    worker_id: str,
    stopped: asyncio.Event,
) -> None:
    interval = max(5.0, settings.job_lease_seconds / 3)
    while not stopped.is_set():
        try:
            await asyncio.wait_for(stopped.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        try:
            async with AsyncSessionFactory() as session:
                await renew_job_lease(
                    session,
                    job_id=job.id,
                    worker_id=worker_id,
                    lease_seconds=settings.job_lease_seconds,
                )
                await session.commit()
        except Exception as exc:
            logger.error(
                "job_lease_renewal_failed",
                extra={
                    "job_id": str(job.id),
                    "business_id": str(job.business_id),
                    "job_type": job.job_type,
                    "worker_id": worker_id,
                    "exception_type": type(exc).__name__,
                },
            )


def build_instance_id(role: str) -> str:
    host = re.sub(r"[^a-zA-Z0-9-]", "-", socket.gethostname()).strip("-")[:24] or "host"
    return f"{role}-{host}-{os.getpid()}-{token_hex(4)}"[:96]


async def process_claimed_job(job: BackgroundJob, *, worker_id: str) -> None:
    outcome: HandlerOutcome
    lease_stopped = asyncio.Event()
    lease_task = asyncio.create_task(
        _maintain_job_lease(job, worker_id=worker_id, stopped=lease_stopped)
    )
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
        elif job.job_type == "prepare_marketing_video":
            dispatched = await dispatch_marketing_video_preparation_job(job)
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
        # Render's default log presentation does not surface structured ``extra``
        # fields. Emit only safe exception diagnostics in the visible message:
        # exception class and sanitized Python frame locations. Never emit
        # ``str(exc)``, provider payloads, business context, credentials, or
        # source-line contents.
        frames: list[str] = []
        traceback_value = exc.__traceback__
        while traceback_value is not None:
            frame = traceback_value.tb_frame
            frames.append(
                f"{os.path.basename(frame.f_code.co_filename)}:"
                f"{traceback_value.tb_lineno}:{frame.f_code.co_name}"
            )
            traceback_value = traceback_value.tb_next
        safe_stack = ">".join(frames[-10:]) or "unavailable"
        logger.error(
            "job_handler_failed exception_type=%s stack=%s",
            type(exc).__name__,
            safe_stack,
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
    finally:
        lease_stopped.set()
        await lease_task
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
