"""Real PostgreSQL acceptance for governed external-action dispatch.

Verifies:
- queued durable attempt is committed before provider invocation
- provider receives the stable attempt idempotency key
- success reconciles ActionExecutionAttempt + AIAction
- definite rejection reconciles to failed
- ambiguous provider outcome reconciles to uncertain
- replay after succeeded/failed/uncertain never invokes provider again

No real provider or connector credential is used.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import delete, select

from app.db.session import AsyncSessionFactory, engine
from app.integrations.action_adapters import (
    ConnectorActionAdapterRegistry,
    ConnectorActionResult,
    ConnectorRejectedError,
)
from app.integrations.action_boundary import ConnectorDispatchContext
from app.models.action_execution_attempt import ActionExecutionAttempt
from app.models.ai_action import AIAction
from app.models.ai_agent_execution import AIAgentExecution
from app.models.ai_workforce import AIAgentConfig
from app.models.approval_request import ApprovalRequest
from app.models.background_job import BackgroundJob
from app.models.business import Business
from app.schemas.ai_action_payload import SendEmailPayload
from app.services.action_dispatcher import dispatch_action_execution_job
from app.services.action_execution_attempt import prepare_action_execution_attempt
from app.services.action_policy import canonical_action_payload_hash
from app.services.action_registry import ACTION_REGISTRY
from app.services.ai_capabilities import ROLE_CAPABILITIES


BUSINESS_ID = uuid4()


class FakeCredentials:
    def __init__(self) -> None:
        self.references: list[str] = []

    async def retrieve(
        self,
        reference: str,
        *,
        business_id: UUID,
        connector_type: str,
        purpose: str,
    ):
        if business_id != BUSINESS_ID:
            raise RuntimeError("credential tenant mismatch")
        if connector_type != "gmail":
            raise RuntimeError("unexpected connector")
        if purpose != "oauth_credentials":
            raise RuntimeError("unexpected credential purpose")
        self.references.append(reference)
        return SimpleNamespace(
            access_token="not-a-real-token",
            refresh_token=None,
            expires_at=None,
        )


class FakeEmailAdapter:
    connector_type = "gmail"
    supported_action_types = frozenset({"send_email"})

    def __init__(
        self,
        *,
        attempt_id: UUID,
        action_id: UUID,
        mode: str,
    ) -> None:
        self.attempt_id = attempt_id
        self.action_id = action_id
        self.mode = mode
        self.calls = 0
        self.idempotency_keys: list[str] = []

    async def execute(
        self,
        *,
        credentials,
        action_type: str,
        payload,
        selected_resources,
        delivery_target,
        idempotency_key: str,
    ) -> ConnectorActionResult:
        self.calls += 1
        self.idempotency_keys.append(idempotency_key)

        if action_type != "send_email":
            raise RuntimeError("unexpected action type")

        # Critical transaction boundary:
        # another DB transaction must already see the durable dispatch claim.
        async with AsyncSessionFactory() as session:
            attempt = await session.scalar(
                select(ActionExecutionAttempt).where(
                    ActionExecutionAttempt.id == self.attempt_id,
                    ActionExecutionAttempt.business_id == BUSINESS_ID,
                )
            )
            action = await session.scalar(
                select(AIAction).where(
                    AIAction.id == self.action_id,
                    AIAction.business_id == BUSINESS_ID,
                )
            )

            if attempt is None or attempt.status != "dispatching":
                raise RuntimeError(
                    "provider entered before durable dispatch claim committed"
                )

            if action is None or action.status != "executing":
                raise RuntimeError(
                    "provider entered before AIAction executing state committed"
                )

        if self.mode == "success":
            return ConnectorActionResult(
                succeeded=True,
                external_reference_id="gmail-smoke-message-1",
                safe_metadata={"delivery_status": "submitted"},
            )

        if self.mode == "rejected":
            raise ConnectorRejectedError("acceptance rejection")

        if self.mode == "uncertain":
            # Any unknown exception after provider invocation must become
            # uncertain and must never be blindly retried.
            raise TimeoutError("simulated lost provider response")

        raise RuntimeError("invalid fake adapter mode")


def build_action(
    *,
    execution_id: UUID,
    proposal_index: int,
    subject: str,
) -> tuple[AIAction, ApprovalRequest]:
    payload = SendEmailPayload(
        recipient_ref=str(uuid4()),
        subject=subject,
        body="Governed dispatcher PostgreSQL acceptance.",
    )
    payload_hash = canonical_action_payload_hash(payload)
    now = datetime.now(UTC)

    action = AIAction(
        id=uuid4(),
        business_id=BUSINESS_ID,
        execution_id=execution_id,
        proposal_index=proposal_index,
        action_type="send_email",
        description="Governed dispatcher PostgreSQL acceptance.",
        risk_level="medium",
        proposed_requires_approval=True,
        status="ready",
        action_payload=payload.model_dump(mode="json"),
        policy_decision="require_approval",
        policy_reason_code="external_communication",
        authorized_payload_hash=payload_hash,
        policy_evaluated_at=now,
        execution_started_at=None,
        execution_completed_at=None,
        result_summary=None,
        failure_code=None,
        external_reference_id=None,
    )

    approval = ApprovalRequest(
        id=uuid4(),
        business_id=BUSINESS_ID,
        action_id=action.id,
        workflow_node_run_id=None,
        requested_by_user_id=None,
        status="approved",
        reason_code="external_communication",
        action_type_snapshot="send_email",
        authorized_payload_hash_snapshot=payload_hash,
        requested_at=now - timedelta(minutes=2),
        expires_at=None,
        decided_at=now - timedelta(minutes=1),
        decided_by_user_id=None,
        decision_actor_id=uuid4(),
        decision_note="PostgreSQL dispatcher acceptance.",
    )

    return action, approval


async def prepare_case(
    *,
    execution_id: UUID,
    proposal_index: int,
    subject: str,
) -> tuple[UUID, UUID, UUID]:
    action, approval = build_action(
        execution_id=execution_id,
        proposal_index=proposal_index,
        subject=subject,
    )

    async with AsyncSessionFactory() as session:
        session.add_all([action, approval])
        await session.flush()

        attempt = await prepare_action_execution_attempt(
            session,
            business_id=BUSINESS_ID,
            action_id=action.id,
        )
        await session.commit()

        job = await session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.business_id == BUSINESS_ID,
                BackgroundJob.job_type == "dispatch_action_execution",
                BackgroundJob.action_execution_attempt_id == attempt.id,
            )
        )

        if job is None:
            raise RuntimeError("durable dispatch job missing")

        return action.id, attempt.id, job.id


async def load_job(job_id: UUID) -> BackgroundJob:
    async with AsyncSessionFactory() as session:
        job = await session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.id == job_id,
                BackgroundJob.business_id == BUSINESS_ID,
            )
        )
        if job is None:
            raise RuntimeError("dispatch job disappeared")
        session.expunge(job)
        return job


async def assert_terminal(
    *,
    action_id: UUID,
    attempt_id: UUID,
    expected_status: str,
    expected_failure: str | None,
    expected_reference: str | None,
) -> None:
    async with AsyncSessionFactory() as session:
        attempt = await session.scalar(
            select(ActionExecutionAttempt).where(
                ActionExecutionAttempt.id == attempt_id,
                ActionExecutionAttempt.business_id == BUSINESS_ID,
            )
        )
        action = await session.scalar(
            select(AIAction).where(
                AIAction.id == action_id,
                AIAction.business_id == BUSINESS_ID,
            )
        )

        if attempt is None or action is None:
            raise RuntimeError("execution records disappeared")

        if attempt.status != expected_status:
            raise RuntimeError(
                f"attempt status mismatch: {attempt.status} != {expected_status}"
            )

        if action.status != expected_status:
            raise RuntimeError(
                f"action status mismatch: {action.status} != {expected_status}"
            )

        if attempt.failure_code != expected_failure:
            raise RuntimeError(
                f"attempt failure mismatch: {attempt.failure_code}"
            )

        if action.failure_code != expected_failure:
            raise RuntimeError(
                f"action failure mismatch: {action.failure_code}"
            )

        if attempt.external_reference_id != expected_reference:
            raise RuntimeError(
                "attempt external reference mismatch"
            )

        if action.external_reference_id != expected_reference:
            raise RuntimeError(
                "action external reference mismatch"
            )


async def dispatch_case(
    *,
    action_id: UUID,
    attempt_id: UUID,
    job_id: UUID,
    approval_id: UUID,
    mode: str,
) -> FakeEmailAdapter:
    async with AsyncSessionFactory() as session:
        attempt = await session.scalar(
            select(ActionExecutionAttempt).where(
                ActionExecutionAttempt.id == attempt_id,
                ActionExecutionAttempt.business_id == BUSINESS_ID,
            )
        )
        action = await session.scalar(
            select(AIAction).where(
                AIAction.id == action_id,
                AIAction.business_id == BUSINESS_ID,
            )
        )

        if attempt is None or action is None:
            raise RuntimeError("dispatch fixture unavailable")

        payload = ACTION_REGISTRY.validate_payload(
            action.action_type,
            action.action_payload,
        )

        context = ConnectorDispatchContext(
            business_id=BUSINESS_ID,
            action_id=action_id,
            approval_id=approval_id,
            attempt_id=attempt_id,
            connection_id=uuid4(),
            action_type="send_email",
            connector_type="gmail",
            idempotency_key=attempt.idempotency_key,
            credential_reference="vault/postgres-dispatch-smoke",
            selected_resources=(),
            payload=payload,
            delivery_target="customer@example.test",
        )

        expected_key = attempt.idempotency_key

    adapter = FakeEmailAdapter(
        attempt_id=attempt_id,
        action_id=action_id,
        mode=mode,
    )
    registry = ConnectorActionAdapterRegistry({"gmail": adapter})
    credentials = FakeCredentials()
    job = await load_job(job_id)

    async def fake_preflight(
        session,
        *,
        business_id,
        attempt_id,
        adapters,
        configuration,
        connection_id=None,
    ):
        if business_id != BUSINESS_ID:
            raise RuntimeError("preflight tenant mismatch")
        if attempt_id != context.attempt_id:
            raise RuntimeError("preflight attempt mismatch")
        return context

    with patch(
        "app.services.action_dispatcher.prepare_connector_dispatch_context",
        new=fake_preflight,
    ):
        outcome = await dispatch_action_execution_job(
            job,
            adapters=registry,
            credentials=credentials,
            configuration=SimpleNamespace(
                job_lease_seconds=120,
                connector_dispatch_timeout_seconds=5,
            ),
        )

    if not outcome.succeeded:
        raise RuntimeError(
            f"dispatcher did not reach authoritative terminal outcome: {outcome}"
        )

    if adapter.calls != 1:
        raise RuntimeError(
            f"provider call count mismatch: {adapter.calls}"
        )

    if adapter.idempotency_keys != [expected_key]:
        raise RuntimeError(
            "stable provider idempotency key was not preserved"
        )

    if credentials.references != ["vault/postgres-dispatch-smoke"]:
        raise RuntimeError("credential reference flow changed")

    # Replay the exact same durable job.
    # Terminal action state MUST prevent a second provider invocation.
    with patch(
        "app.services.action_dispatcher.prepare_connector_dispatch_context",
        new=fake_preflight,
    ):
        replay = await dispatch_action_execution_job(
            job,
            adapters=registry,
            credentials=credentials,
            configuration=SimpleNamespace(
                job_lease_seconds=120,
                connector_dispatch_timeout_seconds=5,
            ),
        )

    if not replay.succeeded:
        raise RuntimeError("terminal dispatch replay was not idempotent")

    if adapter.calls != 1:
        raise RuntimeError(
            "terminal dispatch replay invoked the provider again"
        )

    return adapter


async def main() -> None:
    execution_id = uuid4()

    try:
        async with AsyncSessionFactory() as session:
            session.add(
                Business(
                    id=BUSINESS_ID,
                    name="Governed dispatcher smoke",
                    slug=f"governed-dispatch-smoke-{BUSINESS_ID.hex}",
                    business_type="e-commerce",
                    status="active",
                    timezone="UTC",
                    currency="USD",
                    locale="en",
                )
            )
            await session.flush()

            session.add(
                AIAgentExecution(
                    id=execution_id,
                    business_id=BUSINESS_ID,
                    requested_by_user_id=None,
                    command_id=None,
                    parent_execution_id=None,
                    delegation_role=None,
                    delegation_sequence=0,
                    delegation_depth=0,
                    role="business_manager",
                    trigger_type="automation",
                    status="completed",
                    task="Governed external-write dispatcher acceptance.",
                    provider_name="acceptance",
                    model_name="acceptance",
                    context_revision=None,
                    context_source_count=0,
                    business_brain_source_count=0,
                    memory_source_count=0,
                    output_summary="Acceptance fixture.",
                    recommendations=[],
                    proposed_actions=[],
                    failure_code=None,
                    provider_request_id=None,
                    duration_ms=0,
                    input_tokens=0,
                    output_tokens=0,
                    estimated_cost_usd=None,
                    completed_at=datetime.now(UTC),
                )
            )

            session.add(
                AIAgentConfig(
                    id=uuid4(),
                    business_id=BUSINESS_ID,
                    role="business_manager",
                    display_name="Business Manager",
                    enabled=True,
                    autonomy_mode="manual",
                    custom_instructions=None,
                    capability_config=sorted(
                        ROLE_CAPABILITIES["business_manager"]
                    ),
                )
            )
            await session.commit()

        cases: dict[str, tuple[UUID, UUID, UUID, UUID]] = {}

        for index, mode in enumerate(
            ("success", "rejected", "uncertain")
        ):
            action_id, attempt_id, job_id = await prepare_case(
                execution_id=execution_id,
                proposal_index=index,
                subject=f"Dispatcher acceptance: {mode}",
            )

            async with AsyncSessionFactory() as session:
                approval_id = await session.scalar(
                    select(ApprovalRequest.id).where(
                        ApprovalRequest.business_id == BUSINESS_ID,
                        ApprovalRequest.action_id == action_id,
                        ApprovalRequest.status == "approved",
                    )
                )
                if approval_id is None:
                    raise RuntimeError("approved request missing")

            cases[mode] = (
                action_id,
                attempt_id,
                job_id,
                approval_id,
            )

        # ----------------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------------
        action_id, attempt_id, job_id, approval_id = cases["success"]
        await dispatch_case(
            action_id=action_id,
            attempt_id=attempt_id,
            job_id=job_id,
            approval_id=approval_id,
            mode="success",
        )
        await assert_terminal(
            action_id=action_id,
            attempt_id=attempt_id,
            expected_status="succeeded",
            expected_failure=None,
            expected_reference="gmail-smoke-message-1",
        )

        # ----------------------------------------------------------
        # DEFINITE PROVIDER REJECTION
        # ----------------------------------------------------------
        action_id, attempt_id, job_id, approval_id = cases["rejected"]
        await dispatch_case(
            action_id=action_id,
            attempt_id=attempt_id,
            job_id=job_id,
            approval_id=approval_id,
            mode="rejected",
        )
        await assert_terminal(
            action_id=action_id,
            attempt_id=attempt_id,
            expected_status="failed",
            expected_failure="connector_rejected",
            expected_reference=None,
        )

        # ----------------------------------------------------------
        # AMBIGUOUS PROVIDER OUTCOME
        # ----------------------------------------------------------
        action_id, attempt_id, job_id, approval_id = cases["uncertain"]
        await dispatch_case(
            action_id=action_id,
            attempt_id=attempt_id,
            job_id=job_id,
            approval_id=approval_id,
            mode="uncertain",
        )
        await assert_terminal(
            action_id=action_id,
            attempt_id=attempt_id,
            expected_status="uncertain",
            expected_failure="external_outcome_uncertain",
            expected_reference=None,
        )

        print("Governed AIAction PostgreSQL dispatcher smoke PASSED")
        print("  - provider entered only after durable dispatch claim")
        print("  - stable provider idempotency key preserved")
        print("  - success reconciled Attempt + AIAction")
        print("  - definite rejection reconciled to failed")
        print("  - ambiguous provider outcome reconciled to uncertain")
        print("  - terminal replay invoked provider zero additional times")
        print("  - real external provider calls: zero")

    finally:
        async with AsyncSessionFactory() as session:
            await session.execute(
                delete(BackgroundJob).where(
                    BackgroundJob.business_id == BUSINESS_ID
                )
            )
            await session.execute(
                delete(Business).where(Business.id == BUSINESS_ID)
            )
            await session.commit()

        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
