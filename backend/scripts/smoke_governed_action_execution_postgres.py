"""Real PostgreSQL acceptance for governed AIAction execution preparation.

This smoke test verifies the durable authorization boundary only:

approved AIAction
→ concurrent preparation
→ exactly one ActionExecutionAttempt
→ exactly one dispatch BackgroundJob

No connector or external provider is invoked.

All fixtures use unique identifiers and are removed in ``finally``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionFactory, engine
from app.exceptions.action_execution_attempt import (
    ActionExecutionAttemptConflictError,
    ActionExecutionAttemptNotFoundError,
    ActionExecutionAttemptStateError,
)
from app.models.action_execution_attempt import ActionExecutionAttempt
from app.models.ai_action import AIAction
from app.models.ai_agent_execution import AIAgentExecution
from app.models.ai_workforce import AIAgentConfig
from app.models.approval_request import ApprovalRequest
from app.models.background_job import BackgroundJob
from app.models.business import Business
from app.services.action_execution_attempt import (
    prepare_action_execution_attempt,
)
from app.services.action_policy import canonical_action_payload_hash
from app.services.action_registry import ACTION_REGISTRY
from app.services.ai_capabilities import ROLE_CAPABILITIES


async def _prepare(
    *,
    business_id: UUID,
    action_id: UUID,
) -> tuple[str, UUID | str]:
    """Run one independent transaction against the real PostgreSQL row lock."""
    async with AsyncSessionFactory() as session:
        try:
            attempt = await prepare_action_execution_attempt(
                session,
                business_id=business_id,
                action_id=action_id,
            )
            await session.commit()
            return ("created", attempt.id)
        except ActionExecutionAttemptStateError as exc:
            await session.rollback()
            return ("denied", type(exc).__name__)


def _approved_send_email_action(
    *,
    business_id: UUID,
    execution_id: UUID,
    proposal_index: int,
    subject: str,
) -> tuple[AIAction, ApprovalRequest]:
    payload = {
        "recipient_ref": "customer-acceptance-fixture",
        "subject": subject,
        "body": "Governed PostgreSQL execution acceptance.",
    }
    validated = ACTION_REGISTRY.validate_payload("send_email", payload)
    payload_hash = canonical_action_payload_hash(validated)
    now = datetime.now(UTC)

    action = AIAction(
        id=uuid4(),
        business_id=business_id,
        execution_id=execution_id,
        proposal_index=proposal_index,
        action_type="send_email",
        description="Governed PostgreSQL execution acceptance.",
        risk_level="medium",
        proposed_requires_approval=True,
        status="ready",
        action_payload=validated.model_dump(mode="json"),
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
        business_id=business_id,
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
        decision_note="PostgreSQL execution acceptance.",
    )
    return action, approval


async def main() -> None:
    business_a_id = uuid4()
    business_b_id = uuid4()
    execution_id = uuid4()
    action_id: UUID | None = None
    stale_action_id: UUID | None = None

    try:
        # --------------------------------------------------------------
        # Create isolated production-shaped fixtures.
        # --------------------------------------------------------------
        async with AsyncSessionFactory() as session:
            session.add_all([
                Business(
                    id=business_a_id,
                    name="Governed action smoke A",
                    slug=f"governed-action-smoke-a-{business_a_id.hex}",
                    business_type="e-commerce",
                    status="active",
                    timezone="UTC",
                    currency="USD",
                    locale="en",
                ),
                Business(
                    id=business_b_id,
                    name="Governed action smoke B",
                    slug=f"governed-action-smoke-b-{business_b_id.hex}",
                    business_type="e-commerce",
                    status="active",
                    timezone="UTC",
                    currency="USD",
                    locale="en",
                ),
            ])
            await session.flush()

            execution = AIAgentExecution(
                id=execution_id,
                business_id=business_a_id,
                requested_by_user_id=None,
                command_id=None,
                parent_execution_id=None,
                delegation_role=None,
                delegation_sequence=0,
                delegation_depth=0,
                role="business_manager",
                trigger_type="automation",
                status="completed",
                task="Prepare governed PostgreSQL acceptance actions.",
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
            session.add(execution)

            session.add(
                AIAgentConfig(
                    id=uuid4(),
                    business_id=business_a_id,
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
            await session.flush()

            action, approval = _approved_send_email_action(
                business_id=business_a_id,
                execution_id=execution_id,
                proposal_index=0,
                subject="Concurrent execution acceptance",
            )
            stale_action, stale_approval = _approved_send_email_action(
                business_id=business_a_id,
                execution_id=execution_id,
                proposal_index=1,
                subject="Original approved subject",
            )
            action_id = action.id
            stale_action_id = stale_action.id

            session.add_all([
                action,
                approval,
                stale_action,
                stale_approval,
            ])
            await session.commit()

        assert action_id is not None
        assert stale_action_id is not None

        # --------------------------------------------------------------
        # No connector/dispatcher may be reached while preparing intent.
        # --------------------------------------------------------------
        connector_preflight = AsyncMock(
            side_effect=RuntimeError(
                "connector preflight must not run during preparation"
            )
        )
        dispatcher = AsyncMock(
            side_effect=RuntimeError(
                "dispatcher must not run inline during preparation"
            )
        )

        with patch(
            "app.integrations.action_boundary.prepare_connector_dispatch_context",
            new=connector_preflight,
        ), patch(
            "app.services.action_dispatcher.dispatch_action_execution_job",
            new=dispatcher,
        ):
            # Two independent transactions race for the same governed action.
            results = await asyncio.gather(
                _prepare(
                    business_id=business_a_id,
                    action_id=action_id,
                ),
                _prepare(
                    business_id=business_a_id,
                    action_id=action_id,
                ),
            )

        created = [result for result in results if result[0] == "created"]
        denied = [result for result in results if result[0] == "denied"]

        if len(created) != 1 or len(denied) != 1:
            raise RuntimeError(
                "concurrent preparation did not converge: "
                f"results={results}"
            )

        if connector_preflight.await_count != 0:
            raise RuntimeError(
                "connector preflight was invoked during attempt preparation"
            )
        if dispatcher.await_count != 0:
            raise RuntimeError(
                "external dispatcher was invoked inline during preparation"
            )

        # --------------------------------------------------------------
        # Verify one attempt + one durable dispatch job.
        # --------------------------------------------------------------
        async with AsyncSessionFactory() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(ActionExecutionAttempt).where(
                            ActionExecutionAttempt.business_id
                            == business_a_id,
                            ActionExecutionAttempt.action_id == action_id,
                        )
                    )
                ).all()
            )

            if len(attempts) != 1:
                raise RuntimeError(
                    "expected exactly one ActionExecutionAttempt, "
                    f"found {len(attempts)}"
                )

            attempt = attempts[0]

            if attempt.status != "queued":
                raise RuntimeError(
                    f"attempt was not queued: {attempt.status}"
                )

            expected_attempt_key = (
                f"ai-action:{action_id}:attempt:1"
            )
            if attempt.idempotency_key != expected_attempt_key:
                raise RuntimeError(
                    "attempt idempotency identity changed: "
                    f"{attempt.idempotency_key}"
                )

            jobs = list(
                (
                    await session.scalars(
                        select(BackgroundJob).where(
                            BackgroundJob.business_id == business_a_id,
                            BackgroundJob.job_type
                            == "dispatch_action_execution",
                            BackgroundJob.action_execution_attempt_id
                            == attempt.id,
                        )
                    )
                ).all()
            )

            if len(jobs) != 1:
                raise RuntimeError(
                    "expected exactly one durable dispatch job, "
                    f"found {len(jobs)}"
                )

            expected_job_key = f"dispatch-action:{attempt.id}"
            if jobs[0].idempotency_key != expected_job_key:
                raise RuntimeError(
                    "dispatch job idempotency identity changed: "
                    f"{jobs[0].idempotency_key}"
                )

            persisted_action = await session.scalar(
                select(AIAction).where(
                    AIAction.id == action_id,
                    AIAction.business_id == business_a_id,
                )
            )
            if persisted_action is None or persisted_action.status != "queued":
                raise RuntimeError(
                    "AIAction did not reconcile to queued state"
                )

        # --------------------------------------------------------------
        # Cross-tenant execution preparation must hide/reject Action A.
        # --------------------------------------------------------------
        async with AsyncSessionFactory() as session:
            try:
                await prepare_action_execution_attempt(
                    session,
                    business_id=business_b_id,
                    action_id=action_id,
                )
            except ActionExecutionAttemptNotFoundError:
                await session.rollback()
            else:
                raise RuntimeError(
                    "cross-tenant AIAction execution was accepted"
                )

        # --------------------------------------------------------------
        # Mutate a previously approved action after approval.
        # The immutable authorization hash must reject it.
        # --------------------------------------------------------------
        async with AsyncSessionFactory() as session:
            stale = await session.scalar(
                select(AIAction)
                .where(
                    AIAction.id == stale_action_id,
                    AIAction.business_id == business_a_id,
                )
                .with_for_update()
            )
            if stale is None:
                raise RuntimeError("stale-approval fixture disappeared")

            stale.action_payload = {
                "recipient_ref": "customer-acceptance-fixture",
                "subject": "MUTATED AFTER APPROVAL",
                "body": "This changed payload must never execute.",
            }
            await session.commit()

        async with AsyncSessionFactory() as session:
            try:
                await prepare_action_execution_attempt(
                    session,
                    business_id=business_a_id,
                    action_id=stale_action_id,
                )
            except ActionExecutionAttemptConflictError:
                await session.rollback()
            else:
                raise RuntimeError(
                    "stale approval authorized a mutated payload"
                )

        # No attempt/job may have been created for stale authorization.
        async with AsyncSessionFactory() as session:
            stale_attempt_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ActionExecutionAttempt)
                    .where(
                        ActionExecutionAttempt.business_id == business_a_id,
                        ActionExecutionAttempt.action_id == stale_action_id,
                    )
                )
                or 0
            )

            stale_dispatch_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(BackgroundJob)
                    .where(
                        BackgroundJob.business_id == business_a_id,
                        BackgroundJob.job_type
                        == "dispatch_action_execution",
                        BackgroundJob.action_execution_attempt_id.in_(
                            select(ActionExecutionAttempt.id).where(
                                ActionExecutionAttempt.business_id
                                == business_a_id,
                                ActionExecutionAttempt.action_id
                                == stale_action_id,
                            )
                        ),
                    )
                )
                or 0
            )

            if stale_attempt_count != 0 or stale_dispatch_count != 0:
                raise RuntimeError(
                    "stale authorization crossed the durable execution boundary"
                )

        print("Governed AIAction PostgreSQL preparation smoke PASSED")
        print(
            "  - concurrent preparation: exactly one winner"
        )
        print(
            "  - ActionExecutionAttempt: exactly one queued row"
        )
        print(
            "  - dispatch_action_execution job: exactly one durable row"
        )
        print(
            "  - attempt/provider idempotency identity: stable"
        )
        print(
            "  - cross-tenant execution: rejected"
        )
        print(
            "  - mutated post-approval payload: rejected"
        )
        print(
            "  - connector/provider calls during preparation: zero"
        )

    finally:
        # Delete jobs first because they may reference attempts.
        async with AsyncSessionFactory() as session:
            await session.execute(
                delete(BackgroundJob).where(
                    BackgroundJob.business_id.in_(
                        (business_a_id, business_b_id)
                    )
                )
            )
            # Business cascade removes the isolated execution/action/
            # approval/config/attempt/audit fixtures.
            await session.execute(
                delete(Business).where(
                    Business.id.in_(
                        (business_a_id, business_b_id)
                    )
                )
            )
            await session.commit()

        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
