from __future__ import annotations

import os
from pathlib import Path
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects import postgresql

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.domain.background_jobs import (  # noqa: E402
    JOB_POLICIES,
    JOB_TYPES,
    initial_opportunity_analysis_job_key,
    initial_opportunity_analysis_request_key,
    require_job_policy,
)
from app.exceptions.background_jobs import BackgroundJobStateError, BackgroundJobValidationError  # noqa: E402
from app.models.automation import AutomationWorkflow  # noqa: E402
from app.models.background_job import BackgroundJob, WorkerInstance  # noqa: E402
from app.models.marketing import SocialSchedule  # noqa: E402
from app.models.commerce import CommerceSyncRun  # noqa: E402
from app.services.automation import _next_schedule  # noqa: E402
from app.schemas.automation import ScheduleDefinition  # noqa: E402
from app.schemas.background_jobs import BackgroundJobResponse  # noqa: E402
from app.services.background_jobs import (  # noqa: E402
    cancel_job,
    claim_jobs,
    dead_letter_exhausted_leases,
    enqueue_job,
    record_job_failure,
    record_job_success,
    retry_job,
    _synchronize_linked_run,
)
from app.services.job_scheduler import _scheduled_workflows  # noqa: E402
from app.services.marketing import mark_social_schedule_ready  # noqa: E402
from app.worker import build_instance_id  # noqa: E402


BUSINESS_ID = UUID("d1000000-0000-4000-8000-000000000001")
OTHER_BUSINESS_ID = UUID("d2000000-0000-4000-8000-000000000002")
USER_ID = UUID("d3000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


class BackgroundJobModelAndRegistryTests(unittest.TestCase):
    def test_models_are_durable_and_worker_identity_is_not_tenant_payload(self) -> None:
        self.assertEqual(BackgroundJob.__tablename__, "background_jobs")
        self.assertEqual(WorkerInstance.__tablename__, "worker_instances")
        self.assertIn("business_id", BackgroundJob.__table__.columns)
        self.assertNotIn("payload", BackgroundJob.__table__.columns)
        self.assertNotIn("handler", BackgroundJob.__table__.columns)
        self.assertIn("opportunity_id", BackgroundJob.__table__.columns)
        self.assertIn("conversation_message_id", BackgroundJob.__table__.columns)
        self.assertFalse(any(
            token in column.name
            for column in BackgroundJob.__table__.columns
            for token in ("api_key", "credential", "provider_secret")
        ))

    def test_lifecycle_lease_retry_and_failure_constraints_exist(self) -> None:
        names = {
            value.name for value in BackgroundJob.__table__.constraints
            if isinstance(value, CheckConstraint)
        }
        for expected in (
            "ck_background_jobs_consistent_lifecycle",
            "ck_background_jobs_consistent_lease",
            "ck_background_jobs_consistent_failure",
            "ck_background_jobs_valid_attempt_count",
            "ck_background_jobs_consistent_opportunity_reference",
            "ck_background_jobs_consistent_conversation_message_reference",
        ):
            self.assertIn(expected, names)

    def test_idempotency_is_permanently_unique(self) -> None:
        names = {
            value.name for value in BackgroundJob.__table__.constraints
            if isinstance(value, UniqueConstraint)
        }
        self.assertIn("uq_background_jobs_idempotency_key", names)

    def test_claim_indexes_cover_due_work_and_expired_leases(self) -> None:
        names = {value.name for value in BackgroundJob.__table__.indexes if isinstance(value, Index)}
        self.assertIn("ix_background_jobs_claim", names)
        self.assertIn("ix_background_jobs_expired_lease", names)
        self.assertIn("ix_background_jobs_business_status_created", names)
        self.assertIn(
            "ix_background_jobs_business_opportunity_status_created", names
        )

    def test_opportunity_reference_uses_composite_tenant_foreign_key(self) -> None:
        foreign_keys = [
            value for value in BackgroundJob.__table__.constraints
            if isinstance(value, ForeignKeyConstraint)
        ]
        constraint = next(
            value for value in foreign_keys
            if value.name == "fk_jobs_opportunity_business"
        )
        self.assertEqual(
            [column.name for column in constraint.columns],
            ["opportunity_id", "business_id"],
        )
        self.assertEqual(
            [element.target_fullname for element in constraint.elements],
            ["opportunities.id", "opportunities.business_id"],
        )
        self.assertIsNone(constraint.ondelete)

    def test_conversation_message_reference_uses_composite_tenant_foreign_key(self) -> None:
        foreign_keys = [
            value for value in BackgroundJob.__table__.constraints
            if isinstance(value, ForeignKeyConstraint)
        ]
        constraint = next(
            value for value in foreign_keys
            if value.name == "fk_jobs_conversation_message_business"
        )
        self.assertEqual(
            [column.name for column in constraint.columns],
            ["conversation_message_id", "business_id"],
        )
        self.assertEqual(
            [element.target_fullname for element in constraint.elements],
            ["conversation_messages.id", "conversation_messages.business_id"],
        )
        self.assertEqual(constraint.ondelete, "CASCADE")

    def test_manual_message_dispatch_policy_is_bounded_and_crash_recoverable(self) -> None:
        policy = require_job_policy("dispatch_conversation_message")
        self.assertEqual(policy.reference_field, "conversation_message_id")
        self.assertEqual(policy.priority, 100)
        self.assertEqual(policy.max_attempts, 3)
        self.assertTrue(policy.retryable)
        self.assertFalse(policy.manually_retryable)
        self.assertTrue(policy.lease_recoverable)

    def test_registry_is_immutable_bounded_and_has_no_dispatch_type(self) -> None:
        with self.assertRaises(TypeError):
            JOB_POLICIES["arbitrary"] = object()  # type: ignore[index]
        self.assertEqual(set(JOB_POLICIES), set(JOB_TYPES))
        self.assertNotIn("dispatch_action", JOB_TYPES)
        self.assertNotIn("send_message", JOB_TYPES)
        self.assertTrue(all(1 <= item.max_attempts <= 10 for item in JOB_POLICIES.values()))
        with self.assertRaises(ValueError):
            require_job_policy("user_supplied_handler")

    def test_opportunity_analysis_policy_and_keys_are_bounded_and_stable(self) -> None:
        opportunity_id = uuid4()
        policy = require_job_policy("analyze_business_opportunity")
        self.assertEqual(policy.reference_field, "opportunity_id")
        self.assertEqual(policy.max_attempts, 3)
        job_key = initial_opportunity_analysis_job_key(opportunity_id)
        request_key = initial_opportunity_analysis_request_key(opportunity_id)
        self.assertEqual(
            job_key, f"opportunity-analysis:{opportunity_id}:initial"
        )
        self.assertEqual(
            request_key, f"business-growth:{opportunity_id}:initial"
        )
        self.assertLessEqual(len(job_key), 200)
        self.assertLessEqual(len(request_key), 200)

    def test_opportunity_analysis_job_is_accepted_by_response_schema(self) -> None:
        opportunity_id = uuid4()
        response = BackgroundJobResponse.model_validate(
            _opportunity_job(opportunity_id=opportunity_id)
        )
        self.assertEqual(response.job_type, "analyze_business_opportunity")
        self.assertEqual(response.opportunity_id, opportunity_id)

    def test_migration_adds_tenant_safe_reference_and_job_type(self) -> None:
        source = (
            Path(__file__).parents[1]
            / "alembic/versions/8f3a2c1d4e90_add_opportunity_analysis_jobs.py"
        ).read_text()
        self.assertIn('down_revision: str | Sequence[str] | None = "6b6dc1e42c48"', source)
        self.assertIn('"analyze_business_opportunity"', source)
        self.assertIn('"fk_jobs_opportunity_business"', source)
        self.assertIn('["opportunity_id", "business_id"]', source)
        self.assertIn('["id", "business_id"]', source)
        self.assertNotIn("ondelete=\"SET NULL\"", source)

    def test_worker_id_is_bounded_and_contains_no_secret(self) -> None:
        value = build_instance_id("worker")
        self.assertLessEqual(len(value), 96)
        self.assertRegex(value, r"^worker-[A-Za-z0-9-]+-\d+-[0-9a-f]{8}$")


class BackgroundJobServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_page_job_does_not_finalize_parent_commerce_run(self) -> None:
        run = CommerceSyncRun(
            id=uuid4(), business_id=BUSINESS_ID, connection_id=uuid4(),
            mode="initial", idempotency_key="commerce-run-001",
            status="running", pages_processed=1, warnings=0, failures=0,
        )
        job = BackgroundJob(
            id=uuid4(), business_id=BUSINESS_ID,
            job_type="commerce_initial_sync", status="processing",
            priority=65, idempotency_key="commerce-page-001",
            attempt_count=1, max_attempts=5, available_at=NOW,
            commerce_sync_run_id=run.id,
        )
        await _synchronize_linked_run(
            _Session(scalar_values=[run]),  # type: ignore[arg-type]
            job=job, status="completed", instant=NOW, failure_code=None,
        )
        self.assertEqual(run.status, "running")
        self.assertIsNone(run.completed_at)

    async def test_enqueue_requires_exact_typed_reference(self) -> None:
        with self.assertRaises(BackgroundJobValidationError):
            await enqueue_job(
                _Session(),  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                job_type="process_automation_event",
                idempotency_key="missing-reference",
            )
        with self.assertRaises(BackgroundJobValidationError):
            await enqueue_job(
                _Session(),  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                job_type="process_automation_event",
                idempotency_key="wrong-reference",
                automation_event_id=uuid4(),
                social_schedule_id=uuid4(),
            )
        with self.assertRaises(BackgroundJobValidationError):
            await enqueue_job(
                _Session(),  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                job_type="analyze_business_opportunity",
                idempotency_key="opportunity-analysis:missing",
            )

    async def test_opportunity_enqueue_preserves_tenant_and_is_idempotent(self) -> None:
        opportunity_id = uuid4()
        existing = _opportunity_job(opportunity_id=opportunity_id)
        session = _Session(scalar_values=[None, existing])
        with patch(
            "app.services.background_jobs._require_tenant_reference", new=AsyncMock()
        ) as require_reference:
            result = await enqueue_job(
                session,  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                job_type="analyze_business_opportunity",
                idempotency_key=existing.idempotency_key,
                opportunity_id=opportunity_id,
            )
        self.assertIs(result, existing)
        self.assertEqual(result.business_id, BUSINESS_ID)
        self.assertEqual(result.opportunity_id, opportunity_id)
        require_reference.assert_awaited_once_with(
            session,
            field="opportunity_id",
            reference_id=opportunity_id,
            business_id=BUSINESS_ID,
        )

    async def test_cross_tenant_opportunity_reference_is_rejected(self) -> None:
        with self.assertRaises(BackgroundJobValidationError):
            await enqueue_job(
                _Session(scalar_values=[None]),  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                job_type="analyze_business_opportunity",
                idempotency_key="opportunity-analysis:cross-tenant:initial",
                opportunity_id=uuid4(),
            )

    async def test_opportunity_idempotency_conflict_cannot_change_reference(self) -> None:
        existing = _opportunity_job(opportunity_id=uuid4())
        with patch(
            "app.services.background_jobs._require_tenant_reference", new=AsyncMock()
        ), self.assertRaises(BackgroundJobValidationError):
            await enqueue_job(
                _Session(scalar_values=[None, existing]),  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                job_type="analyze_business_opportunity",
                idempotency_key=existing.idempotency_key,
                opportunity_id=uuid4(),
            )

    async def test_enqueue_is_idempotent_and_uses_registered_policy(self) -> None:
        event_id = uuid4()
        job = _job(automation_event_id=event_id)
        session = _Session(scalar_values=[job.id, job])
        with patch("app.services.background_jobs._require_tenant_reference", new=AsyncMock()):
            result = await enqueue_job(
                session,  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                job_type="process_automation_event",
                idempotency_key="automation-event:event-1",
                automation_event_id=event_id,
            )
        self.assertIs(result, job)
        self.assertEqual(result.max_attempts, 4)

    async def test_duplicate_enqueue_returns_the_persisted_job(self) -> None:
        event_id = uuid4()
        existing = _job(automation_event_id=event_id)
        session = _Session(scalar_values=[None, existing])
        with patch("app.services.background_jobs._require_tenant_reference", new=AsyncMock()):
            result = await enqueue_job(
                session,  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                job_type="process_automation_event",
                idempotency_key=existing.idempotency_key,
                automation_event_id=event_id,
            )
        self.assertIs(result, existing)

    async def test_reference_ownership_is_verified_before_enqueue(self) -> None:
        with self.assertRaises(BackgroundJobValidationError):
            await enqueue_job(
                _Session(scalar_values=[None]),  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                job_type="process_automation_event",
                idempotency_key="cross-tenant-reference",
                automation_event_id=uuid4(),
            )

    async def test_cross_tenant_idempotency_conflict_fails_closed(self) -> None:
        existing = _job(business_id=OTHER_BUSINESS_ID)
        session = _Session(scalar_values=[None, existing])
        with patch("app.services.background_jobs._require_tenant_reference", new=AsyncMock()):
            with self.assertRaises(BackgroundJobValidationError):
                await enqueue_job(
                    session,  # type: ignore[arg-type]
                    business_id=BUSINESS_ID,
                    job_type="process_automation_event",
                    idempotency_key=existing.idempotency_key,
                    automation_event_id=uuid4(),
                )

    async def test_claim_orders_with_skip_locked_and_creates_lease(self) -> None:
        high = _job(priority=100)
        low = _job(priority=40)
        session = _Session(scalar_items=[high, low])
        claimed = await claim_jobs(
            session,  # type: ignore[arg-type]
            worker_id="worker-a",
            batch_size=2,
            lease_seconds=60,
            now=NOW,
        )
        statement = session.last_scalars_statement.compile(dialect=postgresql.dialect())
        sql = str(statement)
        self.assertIn("SKIP LOCKED", sql)
        self.assertIn("priority DESC", sql)
        self.assertIn("available_at <=", sql)
        self.assertEqual(claimed, [high, low])
        self.assertTrue(all(item.status == "processing" for item in claimed))
        self.assertTrue(all(item.attempt_count == 1 for item in claimed))
        self.assertTrue(all(item.lease_expires_at == NOW + timedelta(seconds=60) for item in claimed))

    async def test_expired_internal_lease_is_reclaimable(self) -> None:
        job = _job(status="processing", attempt_count=1, claimed_at=NOW - timedelta(minutes=2), lease_expires_at=NOW - timedelta(minutes=1), worker_id="dead-worker")
        session = _Session(scalar_items=[job])
        result = await claim_jobs(session, worker_id="worker-b", batch_size=1, lease_seconds=30, now=NOW)  # type: ignore[arg-type]
        self.assertEqual(result[0].worker_id, "worker-b")
        self.assertEqual(result[0].attempt_count, 2)

    async def test_success_is_terminal_and_owned_by_claiming_worker(self) -> None:
        job = _processing_job()
        result = await record_job_success(_Session(scalar_values=[job]), job_id=job.id, worker_id="worker-a")  # type: ignore[arg-type]
        self.assertEqual(result.status, "succeeded")
        self.assertIsNotNone(result.completed_at)
        wrong = _processing_job()
        with self.assertRaises(BackgroundJobStateError):
            await record_job_success(_Session(scalar_values=[wrong]), job_id=wrong.id, worker_id="worker-b")  # type: ignore[arg-type]

    async def test_transient_failure_requeues_with_bounded_backoff(self) -> None:
        job = _processing_job(attempt_count=1)
        result = await record_job_failure(
            _Session(scalar_values=[job]),  # type: ignore[arg-type]
            job_id=job.id,
            worker_id="worker-a",
            failure_code="dependency_unavailable",
            retryable=True,
        )
        self.assertEqual(result.status, "queued")
        self.assertIsNone(result.worker_id)
        self.assertGreater(result.available_at, NOW)

    async def test_provider_retry_after_is_honored_when_longer_than_backoff(self) -> None:
        job = _processing_job(attempt_count=1)
        before = datetime.now(UTC)
        result = await record_job_failure(
            _Session(scalar_values=[job]),  # type: ignore[arg-type]
            job_id=job.id,
            worker_id="worker-a",
            failure_code="provider_unavailable",
            retryable=True,
            retry_after_seconds=37,
        )
        self.assertEqual(result.status, "queued")
        self.assertGreaterEqual(result.available_at, before + timedelta(seconds=37))

    async def test_retry_exhaustion_dead_letters_and_notifies_once(self) -> None:
        job = _processing_job(attempt_count=4)
        session = _Session(scalar_values=[job])
        result = await record_job_failure(
            session,  # type: ignore[arg-type]
            job_id=job.id,
            worker_id="worker-a",
            failure_code="dependency_unavailable",
            retryable=True,
        )
        self.assertEqual((result.status, result.failure_code), ("dead_letter", "retry_exhausted"))
        self.assertEqual(len(session.added), 1)
        self.assertEqual(session.added[0].category, "processing_failure")

    async def test_manual_message_transient_failure_requeues_only_before_boundary(self) -> None:
        job = _conversation_dispatch_job(attempt_count=1)
        message = SimpleNamespace(
            id=job.conversation_message_id,
            business_id=BUSINESS_ID,
            delivery_status="queued",
        )
        session = _Session(scalar_values=[job, message])

        result = await record_job_failure(
            session,  # type: ignore[arg-type]
            job_id=job.id,
            worker_id="worker-a",
            failure_code="dependency_unavailable",
            retryable=True,
        )

        self.assertEqual(result.status, "queued")
        self.assertIsNone(result.worker_id)
        self.assertEqual(message.delivery_status, "queued")
        # The linked message was intentionally not consumed/finalized because
        # another bounded attempt is still safe before the provider boundary.
        self.assertEqual(session.scalar_values, [message])

    async def test_manual_message_retry_exhaustion_finalizes_delivery_truthfully(self) -> None:
        for initial, expected in (
            ("queued", "failed"),
            ("dispatching", "uncertain"),
        ):
            with self.subTest(initial=initial):
                job = _conversation_dispatch_job(attempt_count=3)
                message = SimpleNamespace(
                    id=job.conversation_message_id,
                    business_id=BUSINESS_ID,
                    delivery_status=initial,
                )
                session = _Session(scalar_values=[job, message])

                result = await record_job_failure(
                    session,  # type: ignore[arg-type]
                    job_id=job.id,
                    worker_id="worker-a",
                    failure_code="dependency_unavailable",
                    retryable=True,
                )

                self.assertEqual(
                    (result.status, result.failure_code),
                    ("dead_letter", "retry_exhausted"),
                )
                self.assertEqual(message.delivery_status, expected)
                self.assertEqual(len(session.added), 1)
                self.assertEqual(
                    session.added[0].category,
                    "processing_failure",
                )

    async def test_exhausted_manual_message_lease_cannot_leave_message_stuck(self) -> None:
        for initial, expected in (
            ("queued", "failed"),
            ("dispatching", "uncertain"),
        ):
            with self.subTest(initial=initial):
                job = _conversation_dispatch_job(
                    attempt_count=3,
                    claimed_at=NOW - timedelta(minutes=2),
                    lease_expires_at=NOW - timedelta(minutes=1),
                    worker_id="dead-worker",
                )
                message = SimpleNamespace(
                    id=job.conversation_message_id,
                    business_id=BUSINESS_ID,
                    delivery_status=initial,
                )
                session = _Session(
                    scalar_values=[message],
                    scalar_items=[job],
                )

                count = await dead_letter_exhausted_leases(
                    session,  # type: ignore[arg-type]
                    now=NOW,
                    limit=10,
                )

                self.assertEqual(count, 1)
                self.assertEqual(job.status, "dead_letter")
                self.assertEqual(job.failure_code, "retry_exhausted")
                self.assertEqual(message.delivery_status, expected)

    async def test_permanent_and_uncertain_failures_do_not_retry(self) -> None:
        for code in ("invalid_job_state", "uncertain_external_outcome"):
            job = _processing_job()
            result = await record_job_failure(
                _Session(scalar_values=[job]),  # type: ignore[arg-type]
                job_id=job.id,
                worker_id="worker-a",
                failure_code=code,
                retryable=False,
            )
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failure_code, code)

    async def test_only_queued_job_can_be_canceled(self) -> None:
        queued = _job()
        result = await cancel_job(
            _Session(scalar_values=[queued]),  # type: ignore[arg-type]
            business_id=BUSINESS_ID,
            job_id=queued.id,
            actor_user_id=USER_ID,
        )
        self.assertEqual(result.status, "canceled")
        processing = _processing_job()
        with self.assertRaises(BackgroundJobStateError):
            await cancel_job(
                _Session(scalar_values=[processing]),  # type: ignore[arg-type]
                business_id=BUSINESS_ID,
                job_id=processing.id,
                actor_user_id=USER_ID,
            )

    async def test_manual_retry_resets_only_terminal_safe_job(self) -> None:
        job = _processing_job(status="dead_letter", attempt_count=4, completed_at=NOW, failure_code="retry_exhausted")
        result = await retry_job(
            _Session(scalar_values=[job]),  # type: ignore[arg-type]
            business_id=BUSINESS_ID,
            job_id=job.id,
            actor_user_id=USER_ID,
        )
        self.assertEqual(result.status, "queued")
        self.assertEqual(result.attempt_count, 0)
        self.assertIsNone(result.failure_code)


class SchedulerAndTruthfulDomainTests(unittest.IsolatedAsyncioTestCase):
    async def test_due_schedule_enqueues_one_occurrence_then_jumps_future(self) -> None:
        workflow = AutomationWorkflow(
            id=uuid4(), business_id=BUSINESS_ID, name="Daily", description=None,
            status="active", current_version=1, trigger_type="scheduled_time", enabled=True,
            timezone="America/New_York", schedule_definition={"frequency": "daily", "at_time": "09:00"},
            next_run_at=NOW - timedelta(days=3), created_by_user_id=None,
        )
        enqueue = AsyncMock()
        with patch("app.services.job_scheduler.enqueue_job", new=enqueue):
            count = await _scheduled_workflows(
                _Session(scalar_items=[workflow]), instant=NOW, limit=100,  # type: ignore[arg-type]
            )
        self.assertEqual(count, 1)
        enqueue.assert_awaited_once()
        self.assertEqual(enqueue.await_args.kwargs["scheduled_occurrence_at"], NOW - timedelta(days=3))
        self.assertGreater(workflow.next_run_at, NOW)

    async def test_not_yet_due_schedule_is_ignored(self) -> None:
        with patch("app.services.job_scheduler.enqueue_job", new=AsyncMock()) as enqueue:
            count = await _scheduled_workflows(_Session(scalar_items=[]), instant=NOW, limit=100)  # type: ignore[arg-type]
        self.assertEqual(count, 0)
        enqueue.assert_not_awaited()

    def test_schedule_timezone_and_one_time_semantics(self) -> None:
        daily = ScheduleDefinition(frequency="daily", at_time="09:00")
        value = _next_schedule(daily, "America/New_York", NOW)
        self.assertEqual(value, datetime(2026, 8, 23, 13, tzinfo=UTC))
        once = ScheduleDefinition(frequency="one_time", at=NOW + timedelta(hours=2))
        self.assertEqual(_next_schedule(once, "Asia/Karachi", NOW), NOW + timedelta(hours=2))
        elapsed = ScheduleDefinition(frequency="one_time", at=NOW - timedelta(seconds=1))
        self.assertIsNone(_next_schedule(elapsed, "UTC", NOW))

    async def test_due_social_content_becomes_ready_never_published(self) -> None:
        schedule = SocialSchedule(
            id=uuid4(), business_id=BUSINESS_ID, content_id=uuid4(), campaign_id=None,
            channel="instagram", scheduled_for=NOW - timedelta(minutes=1), timezone="UTC",
            status="scheduled",
        )
        with patch("app.services.marketing._get", new=AsyncMock(return_value=schedule)), patch(
            "app.services.marketing._flush", new=AsyncMock()
        ), patch("app.services.marketing._notify") as notify:
            result = await mark_social_schedule_ready(
                _Session(), business_id=BUSINESS_ID, schedule_id=schedule.id, now=NOW,  # type: ignore[arg-type]
            )
        self.assertEqual(result.status, "ready_to_publish")
        self.assertNotEqual(result.status, "published")
        self.assertIn("not published", notify.call_args.kwargs["message"].lower())


class _ScalarItems:
    def __init__(self, items: list[object]):
        self.items = items

    def all(self) -> list[object]:
        return self.items


class _Session:
    def __init__(self, *, scalar_values: list[object] | None = None, scalar_items: list[object] | None = None):
        self.scalar_values = list(scalar_values or [])
        self.scalar_items = list(scalar_items or [])
        self.added: list[object] = []
        self.flush_calls = 0
        self.last_scalars_statement = None

    async def scalar(self, _statement):
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def scalars(self, statement):
        self.last_scalars_statement = statement
        return _ScalarItems(self.scalar_items)

    async def execute(self, _statement):
        return SimpleNamespace(all=lambda: [])

    async def flush(self, *_args, **_kwargs) -> None:
        self.flush_calls += 1

    def add(self, value: object) -> None:
        self.added.append(value)


def _job(
    *,
    business_id: UUID = BUSINESS_ID,
    status: str = "queued",
    priority: int = 80,
    attempt_count: int = 0,
    claimed_at: datetime | None = None,
    lease_expires_at: datetime | None = None,
    worker_id: str | None = None,
    completed_at: datetime | None = None,
    failure_code: str | None = None,
    automation_event_id: UUID | None = None,
) -> BackgroundJob:
    return BackgroundJob(
        id=uuid4(), business_id=business_id, job_type="process_automation_event",
        status=status, priority=priority, idempotency_key=f"automation-event:{uuid4()}",
        attempt_count=attempt_count, max_attempts=4, available_at=NOW - timedelta(seconds=1),
        claimed_at=claimed_at, lease_expires_at=lease_expires_at, worker_id=worker_id,
        completed_at=completed_at, failure_code=failure_code,
        automation_event_id=automation_event_id or uuid4(), workflow_id=None,
        workflow_run_id=None, node_run_id=None, integration_event_id=None,
        action_execution_attempt_id=None, social_schedule_id=None,
        scheduled_occurrence_at=None, created_at=NOW, updated_at=NOW,
    )


def _processing_job(
    *,
    status: str = "processing",
    attempt_count: int = 1,
    completed_at: datetime | None = None,
    failure_code: str | None = None,
) -> BackgroundJob:
    return _job(
        status=status,
        attempt_count=attempt_count,
        claimed_at=NOW - timedelta(seconds=10),
        lease_expires_at=NOW + timedelta(seconds=50),
        worker_id="worker-a",
        completed_at=completed_at,
        failure_code=failure_code,
    )


def _conversation_dispatch_job(
    *,
    status: str = "processing",
    attempt_count: int = 1,
    claimed_at: datetime | None = NOW - timedelta(seconds=10),
    lease_expires_at: datetime | None = NOW + timedelta(seconds=50),
    worker_id: str | None = "worker-a",
    conversation_message_id: UUID | None = None,
) -> BackgroundJob:
    message_id = conversation_message_id or uuid4()
    return BackgroundJob(
        id=uuid4(),
        business_id=BUSINESS_ID,
        job_type="dispatch_conversation_message",
        status=status,
        priority=100,
        idempotency_key=f"dispatch-conversation-message:{message_id}",
        attempt_count=attempt_count,
        max_attempts=3,
        available_at=NOW - timedelta(seconds=1),
        claimed_at=claimed_at,
        lease_expires_at=lease_expires_at,
        worker_id=worker_id,
        completed_at=None,
        failure_code=None,
        conversation_message_id=message_id,
        created_at=NOW,
        updated_at=NOW,
    )


def _opportunity_job(*, opportunity_id: UUID) -> BackgroundJob:
    return BackgroundJob(
        id=uuid4(), business_id=BUSINESS_ID,
        job_type="analyze_business_opportunity", status="queued",
        priority=30,
        idempotency_key=initial_opportunity_analysis_job_key(opportunity_id),
        attempt_count=0, max_attempts=3, available_at=NOW,
        opportunity_id=opportunity_id,
        created_at=NOW, updated_at=NOW,
    )
