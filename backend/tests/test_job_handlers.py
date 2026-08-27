from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.models.action_execution_attempt import ActionExecutionAttempt  # noqa: E402
from app.models.automation import AutomationNodeRun, AutomationWorkflow  # noqa: E402
from app.models.background_job import BackgroundJob  # noqa: E402
from app.services.approval import _enqueue_workflow_resume  # noqa: E402
from app.services.job_handlers import (  # noqa: E402
    JOB_HANDLERS,
    handle_process_automation_event,
    handle_process_integration_event,
    handle_process_scheduled_workflow,
    handle_reconcile_uncertain_attempt,
    handle_resume_workflow_run,
)


BUSINESS_ID = UUID("f1000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


class JobHandlerTests(unittest.IsolatedAsyncioTestCase):
    def test_handler_registry_is_immutable_and_exactly_matches_real_job_types(self) -> None:
        self.assertEqual(set(JOB_HANDLERS), {
            "process_automation_event", "resume_workflow_run", "process_scheduled_workflow",
            "process_integration_event", "reconcile_uncertain_attempt", "mark_social_schedule_ready",
            "customer_agent_response",
            "dispatch_action_execution",
            "maintain_subscription",
            "discover_competitors", "generate_content_plan", "analyze_campaign_opportunities",
            "commerce_initial_sync", "commerce_incremental_sync", "commerce_webhook_reconcile",
            "google_merchant_status_sync", "meta_catalog_status_sync",
            "google_ads_performance_sync", "meta_ads_performance_sync",
        })
        with self.assertRaises(TypeError):
            JOB_HANDLERS["dynamic"] = AsyncMock()  # type: ignore[index]

    async def test_automation_event_creates_durable_start_jobs_for_runs(self) -> None:
        run = SimpleNamespace(id=uuid4(), status="queued")
        job = _job(automation_event_id=uuid4())
        enqueue = AsyncMock()
        with patch("app.services.job_handlers.process_automation_event", new=AsyncMock(return_value=(SimpleNamespace(), [run]))), patch(
            "app.services.job_handlers.enqueue_job", new=enqueue,
        ):
            outcome = await handle_process_automation_event(_Session(), job)  # type: ignore[arg-type]
        self.assertTrue(outcome.succeeded)
        enqueue.assert_awaited_once()
        self.assertEqual(enqueue.await_args.kwargs["idempotency_key"], f"workflow-start:{run.id}")

    async def test_replayed_automation_handler_uses_same_idempotency_key(self) -> None:
        run = SimpleNamespace(id=uuid4(), status="queued")
        job = _job(automation_event_id=uuid4())
        enqueue = AsyncMock()
        with patch("app.services.job_handlers.process_automation_event", new=AsyncMock(return_value=(SimpleNamespace(), [run]))), patch(
            "app.services.job_handlers.enqueue_job", new=enqueue,
        ):
            await handle_process_automation_event(_Session(), job)  # type: ignore[arg-type]
            await handle_process_automation_event(_Session(), job)  # type: ignore[arg-type]
        keys = [call.kwargs["idempotency_key"] for call in enqueue.await_args_list]
        self.assertEqual(keys, [f"workflow-start:{run.id}", f"workflow-start:{run.id}"])

    async def test_resume_uses_existing_workflow_engine(self) -> None:
        run_id = uuid4()
        job = _job(job_type="resume_workflow_run", workflow_run_id=run_id)
        advance = AsyncMock(return_value=SimpleNamespace(status="waiting"))
        with patch("app.services.job_handlers.advance_workflow_run", new=advance):
            outcome = await handle_resume_workflow_run(_Session(), job)  # type: ignore[arg-type]
        self.assertTrue(outcome.succeeded)
        self.assertEqual(advance.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(advance.await_args.kwargs["run_id"], run_id)

    async def test_scheduled_workflow_run_and_resume_are_deterministic(self) -> None:
        workflow = AutomationWorkflow(
            id=uuid4(), business_id=BUSINESS_ID, name="Daily", description=None,
            status="active", current_version=1, trigger_type="scheduled_time", enabled=True,
            timezone="UTC", schedule_definition={"frequency": "daily", "at_time": "09:00"},
            next_run_at=NOW + timedelta(days=1), created_by_user_id=None,
        )
        run = SimpleNamespace(id=uuid4(), status="queued")
        job = _job(
            job_type="process_scheduled_workflow", workflow_id=workflow.id,
            scheduled_occurrence_at=NOW,
        )
        create = AsyncMock(return_value=run)
        enqueue = AsyncMock()
        with patch("app.services.job_handlers.create_workflow_run", new=create), patch(
            "app.services.job_handlers.enqueue_job", new=enqueue,
        ):
            outcome = await handle_process_scheduled_workflow(
                _Session(scalar_value=workflow), job,  # type: ignore[arg-type]
            )
        self.assertTrue(outcome.succeeded)
        expected = f"schedule:{workflow.id}:{NOW.isoformat()}"
        self.assertEqual(create.await_args.kwargs["idempotency_key"], expected)
        self.assertEqual(enqueue.await_args.kwargs["idempotency_key"], f"workflow-start:{run.id}")

    async def test_integration_handler_only_processes_persisted_verified_event(self) -> None:
        event_id = uuid4()
        job = _job(job_type="process_integration_event", integration_event_id=event_id)
        process = AsyncMock()
        with patch("app.services.job_handlers.process_integration_webhook_event", new=process):
            outcome = await handle_process_integration_event(_Session(), job)  # type: ignore[arg-type]
        self.assertTrue(outcome.succeeded)
        self.assertEqual(process.await_args.kwargs, {"business_id": BUSINESS_ID, "event_id": event_id})

    async def test_stale_dispatch_reconciliation_never_invokes_connector(self) -> None:
        attempt = ActionExecutionAttempt(
            id=uuid4(), business_id=BUSINESS_ID, action_id=uuid4(), attempt_number=1,
            idempotency_key="ai-action:test:attempt:1", action_type="send_customer_message",
            capability="customer_message.send", status="dispatching", queued_at=NOW - timedelta(minutes=2),
            dispatch_started_at=NOW - timedelta(minutes=1), completed_at=None,
            lease_acquired_at=NOW - timedelta(minutes=1), lease_expires_at=NOW - timedelta(seconds=1),
            external_reference_id=None, failure_code=None,
        )
        job = _job(job_type="reconcile_uncertain_attempt", action_execution_attempt_id=attempt.id)
        mark = AsyncMock(return_value=attempt)
        session = _Session(scalar_value=attempt)
        with patch("app.services.job_handlers.mark_stale_action_execution_attempt_uncertain", new=mark):
            outcome = await handle_reconcile_uncertain_attempt(session, job)  # type: ignore[arg-type]
        self.assertTrue(outcome.succeeded)
        mark.assert_awaited_once()
        self.assertEqual(len(session.added), 1)
        self.assertEqual(session.added[0].category, "action_uncertain")

    async def test_approval_decision_enqueues_durable_resume_not_dispatch(self) -> None:
        node = AutomationNodeRun(
            id=uuid4(), business_id=BUSINESS_ID, workflow_version_id=uuid4(),
            workflow_run_id=uuid4(), node_key=uuid4(), status="waiting", attempt=1,
            started_at=NOW, completed_at=None, branch_outcome=None, result_summary=None,
            failure_code=None, resume_at=None, action_id=None,
        )
        approval = SimpleNamespace(id=uuid4(), business_id=BUSINESS_ID, status="approved")
        enqueue = AsyncMock()
        with patch("app.services.approval.enqueue_job", new=enqueue):
            await _enqueue_workflow_resume(_Session(), approval=approval, node_run=node)  # type: ignore[arg-type]
        enqueue.assert_awaited_once()
        self.assertEqual(enqueue.await_args.kwargs["job_type"], "resume_workflow_run")
        self.assertNotIn("action_execution_attempt_id", enqueue.await_args.kwargs)


class _Session:
    def __init__(self, scalar_value=None):
        self.scalar_value = scalar_value
        self.added: list[object] = []

    async def scalar(self, _statement):
        return self.scalar_value

    def add(self, value: object) -> None:
        self.added.append(value)


def _job(
    *,
    job_type: str = "process_automation_event",
    automation_event_id=None,
    workflow_id=None,
    workflow_run_id=None,
    integration_event_id=None,
    action_execution_attempt_id=None,
    scheduled_occurrence_at=None,
) -> BackgroundJob:
    return BackgroundJob(
        id=uuid4(), business_id=BUSINESS_ID, job_type=job_type, status="processing",
        priority=80, idempotency_key=f"test:{uuid4()}", attempt_count=1, max_attempts=4,
        available_at=NOW, claimed_at=NOW, lease_expires_at=NOW + timedelta(minutes=1),
        worker_id="worker-a", completed_at=None, failure_code=None,
        automation_event_id=automation_event_id, workflow_id=workflow_id,
        workflow_run_id=workflow_run_id, node_run_id=None,
        integration_event_id=integration_event_id,
        action_execution_attempt_id=action_execution_attempt_id,
        social_schedule_id=None, scheduled_occurrence_at=scheduled_occurrence_at,
        created_at=NOW, updated_at=NOW,
    )
