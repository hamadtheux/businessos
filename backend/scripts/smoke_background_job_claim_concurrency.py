"""Real PostgreSQL claim smoke; committed fixtures are removed in finally."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select

from app.db.session import AsyncSessionFactory, engine
from app.models.automation import AutomationEvent
from app.models.background_job import BackgroundJob
from app.models.business import Business
from app.services.background_jobs import claim_jobs, enqueue_job


async def _claim(worker_id: str) -> set[object]:
    async with AsyncSessionFactory() as session:
        jobs = await claim_jobs(
            session,
            worker_id=worker_id,
            batch_size=4,
            lease_seconds=60,
        )
        await session.commit()
        return {job.id for job in jobs}


async def main() -> None:
    business_id = uuid4()
    slug = f"job-smoke-{business_id.hex}"
    try:
        async with AsyncSessionFactory() as session:
            session.add(Business(
                id=business_id,
                name="Background job concurrency smoke",
                slug=slug,
                business_type="testing",
                status="active",
                timezone="UTC",
                currency="USD",
                locale="en",
            ))
            await session.flush()
            for index in range(8):
                event = AutomationEvent(
                    business_id=business_id,
                    event_type="manual_test",
                    entity_type="test",
                    entity_id=None,
                    payload={"index": index},
                    occurred_at=datetime.now(UTC),
                    status="pending",
                    processed_at=None,
                    failure_code=None,
                )
                session.add(event)
                await session.flush()
                await enqueue_job(
                    session,
                    business_id=business_id,
                    job_type="process_automation_event",
                    idempotency_key=f"smoke:{business_id}:{index}",
                    automation_event_id=event.id,
                )
            await session.commit()

        first, second = await asyncio.gather(
            _claim("smoke-worker-a"),
            _claim("smoke-worker-b"),
        )
        overlap = first & second
        if overlap or len(first | second) != 8 or not first or not second:
            raise RuntimeError(
                f"claim smoke failed: first={len(first)} second={len(second)} overlap={len(overlap)}"
            )
        print(
            f"PASS: two workers claimed {len(first)} + {len(second)} jobs with zero overlap"
        )
    finally:
        async with AsyncSessionFactory() as session:
            await session.execute(delete(BackgroundJob).where(
                BackgroundJob.business_id == business_id,
            ))
            await session.execute(delete(AutomationEvent).where(
                AutomationEvent.business_id == business_id,
            ))
            await session.execute(delete(Business).where(Business.id == business_id))
            await session.commit()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
