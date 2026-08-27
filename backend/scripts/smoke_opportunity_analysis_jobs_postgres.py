"""Real PostgreSQL acceptance for durable Opportunity-analysis jobs.

All fixtures use unique identifiers and are removed in ``finally``.
No AI provider or external action connector is invoked.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.domain.background_jobs import (
    initial_opportunity_analysis_job_key,
    initial_opportunity_analysis_request_key,
)
from app.exceptions.background_jobs import BackgroundJobValidationError
from app.models.action_execution_attempt import ActionExecutionAttempt
from app.models.background_job import BackgroundJob
from app.models.business import Business
from app.models.opportunity import Opportunity
from app.db.session import AsyncSessionFactory, engine
from app.services.background_jobs import enqueue_job
from app.services.job_handlers import handle_analyze_business_opportunity
from app.services.marketing_automation import _create_opportunity_if_missing


async def _persist_growth_signal(
    *, business_id: UUID, dedupe_key: str
) -> bool:
    async with AsyncSessionFactory() as session:
        created = await _create_opportunity_if_missing(
            session,
            business_id=business_id,
            dedupe_key=dedupe_key,
            title="PostgreSQL Opportunity-analysis job smoke",
            description="A deterministic local acceptance signal.",
            category="revenue_decline",
            source="commerce",
            source_entity_type=None,
            source_entity_id=None,
            reason="Local PostgreSQL concurrency acceptance.",
            confidence=Decimal("0.900"),
            recommendation="Analyze the persisted signal.",
            provenance=[{
                "classification": "first_party_observed",
                "detector": "postgresql_acceptance",
            }],
            suggested_action="analyze_business_opportunity",
            enqueue_initial_analysis=True,
        )
        await session.commit()
        return created


async def main() -> None:
    business_a_id = uuid4()
    business_b_id = uuid4()
    cross_opportunity_id = uuid4()
    dedupe_key = f"business-growth:postgres-smoke:{uuid4()}"
    try:
        async with AsyncSessionFactory() as session:
            for business_id, suffix in (
                (business_a_id, "a"),
                (business_b_id, "b"),
            ):
                session.add(Business(
                    id=business_id,
                    name=f"Opportunity job smoke {suffix}",
                    slug=f"opportunity-job-smoke-{business_id.hex}",
                    business_type="e-commerce",
                    status="active",
                    timezone="UTC",
                    currency="USD",
                    locale="en",
                ))
            session.add(Opportunity(
                id=cross_opportunity_id,
                business_id=business_b_id,
                title="Cross-tenant FK acceptance",
                description="Must never be referenced by Business A.",
                category="revenue_decline",
                source="commerce",
                priority="medium",
                status="open",
                reason="Tenant isolation acceptance.",
                confidence=Decimal("0.900"),
                recommendation="Reject cross-tenant use.",
                suggested_action="analyze_business_opportunity",
                provenance=[],
                dedupe_key=f"business-growth:cross-tenant:{uuid4()}",
            ))
            await session.commit()

        # Two concurrent detector transactions converge on one Opportunity;
        # only the INSERT ... RETURNING winner enqueues the analysis job.
        created = await asyncio.gather(
            _persist_growth_signal(
                business_id=business_a_id, dedupe_key=dedupe_key
            ),
            _persist_growth_signal(
                business_id=business_a_id, dedupe_key=dedupe_key
            ),
        )
        if sorted(created) != [False, True]:
            raise RuntimeError(f"atomic Opportunity convergence failed: {created}")

        async with AsyncSessionFactory() as session:
            opportunity = await session.scalar(select(Opportunity).where(
                Opportunity.business_id == business_a_id,
                Opportunity.dedupe_key == dedupe_key,
            ))
            if opportunity is None:
                raise RuntimeError("new Opportunity was not persisted")
            expected_job_key = initial_opportunity_analysis_job_key(opportunity.id)
            jobs = list((await session.scalars(select(BackgroundJob).where(
                BackgroundJob.business_id == business_a_id,
                BackgroundJob.opportunity_id == opportunity.id,
            ))).all())
            if len(jobs) != 1 or jobs[0].idempotency_key != expected_job_key:
                raise RuntimeError("new Opportunity did not converge on one analysis job")

            # The queue-level idempotency boundary returns that same row.
            repeated = await enqueue_job(
                session,
                business_id=business_a_id,
                job_type="analyze_business_opportunity",
                idempotency_key=expected_job_key,
                opportunity_id=opportunity.id,
            )
            await session.commit()
            if repeated.id != jobs[0].id:
                raise RuntimeError("duplicate enqueue returned a different job")

        # Service validation hides a cross-tenant Opportunity.
        async with AsyncSessionFactory() as session:
            try:
                await enqueue_job(
                    session,
                    business_id=business_a_id,
                    job_type="analyze_business_opportunity",
                    idempotency_key=f"opportunity-analysis:{uuid4()}:initial",
                    opportunity_id=cross_opportunity_id,
                )
            except BackgroundJobValidationError:
                await session.rollback()
            else:
                raise RuntimeError("service accepted a cross-tenant Opportunity")

        # PostgreSQL independently enforces the same composite ownership.
        async with AsyncSessionFactory() as session:
            session.add(BackgroundJob(
                business_id=business_a_id,
                job_type="analyze_business_opportunity",
                status="queued",
                priority=30,
                idempotency_key=f"opportunity-analysis:{uuid4()}:initial",
                attempt_count=0,
                max_attempts=3,
                available_at=datetime.now(UTC),
                opportunity_id=cross_opportunity_id,
            ))
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
            else:
                raise RuntimeError("PostgreSQL accepted a cross-tenant Opportunity job")

        async with AsyncSessionFactory() as session:
            loaded = await session.scalar(select(BackgroundJob).where(
                BackgroundJob.idempotency_key == expected_job_key
            ))
            if loaded is None or loaded.opportunity_id != opportunity.id:
                raise RuntimeError("worker ORM could not load typed opportunity_id")
            analyze = AsyncMock(return_value=SimpleNamespace(
                execution=SimpleNamespace(status="completed"),
                failure_code=None,
            ))
            with patch(
                "app.services.job_handlers.create_openai_provider",
                return_value=SimpleNamespace(provider_name="acceptance-provider"),
            ), patch(
                "app.services.job_handlers.analyze_business_opportunity",
                new=analyze,
            ):
                first = await handle_analyze_business_opportunity(session, loaded)
                second = await handle_analyze_business_opportunity(session, loaded)
            if not first.succeeded or not second.succeeded:
                raise RuntimeError("worker handler did not reach terminal service outcome")
            request_keys = [
                call.kwargs["analysis_request_key"]
                for call in analyze.await_args_list
            ]
            expected_request_key = initial_opportunity_analysis_request_key(
                opportunity.id
            )
            if request_keys != [expected_request_key, expected_request_key]:
                raise RuntimeError("job retry changed the analysis request identity")

            attempts = int(await session.scalar(
                select(func.count()).select_from(ActionExecutionAttempt).where(
                    ActionExecutionAttempt.business_id == business_a_id
                )
            ) or 0)
            dispatch_jobs = int(await session.scalar(
                select(func.count()).select_from(BackgroundJob).where(
                    BackgroundJob.business_id == business_a_id,
                    BackgroundJob.job_type == "dispatch_action_execution",
                )
            ) or 0)
            if attempts or dispatch_jobs:
                raise RuntimeError("analysis handoff crossed the execution boundary")

        print("Opportunity-analysis BackgroundJob PostgreSQL smoke test PASSED")
        print("  - concurrent detector insert: one Opportunity and one job")
        print("  - duplicate job key: one durable row")
        print("  - composite tenant FK: enforced by service and PostgreSQL")
        print("  - worker typed reference and retry identity: stable")
        print("  - ActionExecutionAttempt / dispatch jobs: zero")
    finally:
        async with AsyncSessionFactory() as session:
            await session.execute(delete(BackgroundJob).where(
                BackgroundJob.business_id.in_((business_a_id, business_b_id))
            ))
            await session.execute(delete(Opportunity).where(
                Opportunity.business_id.in_((business_a_id, business_b_id))
            ))
            await session.execute(delete(Business).where(
                Business.id.in_((business_a_id, business_b_id))
            ))
            await session.commit()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
