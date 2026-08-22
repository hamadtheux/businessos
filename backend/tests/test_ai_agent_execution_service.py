from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault(
    "AIBOS_AUTH_SECRET_KEY",
    "x" * 32,
)

from app.exceptions.ai_agent_execution import (  # noqa: E402
    AIAgentExecutionNotFoundError,
    AIAgentExecutionPersistenceError,
    AIAgentExecutionStateError,
    AIAgentExecutionValidationError,
)
from app.models.ai_agent_execution import AIAgentExecution  # noqa: E402
from app.schemas.ai_agent import (  # noqa: E402
    AIAgentExecutionResult,
    AIAgentProposedAction,
    AIAgentStructuredOutput,
)
from app.services.ai_agent_execution import (  # noqa: E402
    create_running_ai_agent_execution,
    fail_ai_agent_execution,
    finalize_successful_ai_agent_execution,
    get_ai_agent_execution,
)


BUSINESS_A_ID = UUID(
    "91000000-0000-0000-0000-000000000001"
)

BUSINESS_B_ID = UUID(
    "92000000-0000-0000-0000-000000000002"
)

USER_ID = UUID(
    "93000000-0000-0000-0000-000000000003"
)

CONTEXT_REVISION = "a" * 64


class CreateExecutionTests(
    unittest.IsolatedAsyncioTestCase,
):
    async def test_create_running_execution_sets_safe_initial_state(
        self,
    ) -> None:
        session = _FakeSession()

        execution = await create_running_ai_agent_execution(
            session,
            business_id=BUSINESS_A_ID,
            requested_by_user_id=USER_ID,
            role="sales",
            task="  Recommend the next sales step.  ",
            provider_name="  openai  ",
            model_name="  gpt-5.6-terra  ",
        )

        self.assertEqual(
            execution.business_id,
            BUSINESS_A_ID,
        )

        self.assertEqual(
            execution.requested_by_user_id,
            USER_ID,
        )

        self.assertEqual(
            execution.role,
            "sales",
        )

        self.assertEqual(
            execution.trigger_type,
            "api",
        )

        self.assertEqual(
            execution.status,
            "running",
        )

        self.assertEqual(
            execution.task,
            "Recommend the next sales step.",
        )

        self.assertEqual(
            execution.provider_name,
            "openai",
        )

        self.assertEqual(
            execution.model_name,
            "gpt-5.6-terra",
        )

        self.assertIsNone(
            execution.context_revision,
        )

        self.assertEqual(
            execution.context_source_count,
            0,
        )

        self.assertEqual(
            execution.business_brain_source_count,
            0,
        )

        self.assertEqual(
            execution.memory_source_count,
            0,
        )

        self.assertEqual(
            execution.recommendations,
            [],
        )

        self.assertEqual(
            execution.proposed_actions,
            [],
        )

        self.assertIsNone(
            execution.completed_at,
        )

        self.assertEqual(
            session.flush_calls,
            1,
        )

        self.assertEqual(
            session.commit_calls,
            0,
        )

    async def test_create_allows_system_trigger_without_requester(
        self,
    ) -> None:
        session = _FakeSession()

        execution = await create_running_ai_agent_execution(
            session,
            business_id=BUSINESS_A_ID,
            requested_by_user_id=None,
            role="analytics",
            task="Review current business performance.",
            provider_name="openai",
            model_name="gpt-5.6-terra",
            trigger_type="system",
        )

        self.assertIsNone(
            execution.requested_by_user_id,
        )

        self.assertEqual(
            execution.trigger_type,
            "system",
        )

    async def test_create_rejects_blank_task_before_database_write(
        self,
    ) -> None:
        session = _FakeSession()

        with self.assertRaises(
            AIAgentExecutionValidationError,
        ):
            await create_running_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                requested_by_user_id=USER_ID,
                role="sales",
                task="   ",
                provider_name="openai",
                model_name="gpt-5.6-terra",
            )

        self.assertEqual(
            session.added,
            [],
        )

        self.assertEqual(
            session.flush_calls,
            0,
        )

    async def test_create_rejects_blank_provider_name(
        self,
    ) -> None:
        session = _FakeSession()

        with self.assertRaises(
            AIAgentExecutionValidationError,
        ):
            await create_running_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                requested_by_user_id=USER_ID,
                role="sales",
                task="Task",
                provider_name="   ",
                model_name="gpt-5.6-terra",
            )

        self.assertEqual(
            session.flush_calls,
            0,
        )

    async def test_create_rejects_blank_model_name(
        self,
    ) -> None:
        session = _FakeSession()

        with self.assertRaises(
            AIAgentExecutionValidationError,
        ):
            await create_running_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                requested_by_user_id=USER_ID,
                role="sales",
                task="Task",
                provider_name="openai",
                model_name="   ",
            )

        self.assertEqual(
            session.flush_calls,
            0,
        )

    async def test_create_sanitizes_database_failure(
        self,
    ) -> None:
        session = _FakeSession(
            flush_error=SQLAlchemyError(
                "private PostgreSQL detail"
            ),
        )

        with self.assertRaises(
            AIAgentExecutionPersistenceError,
        ) as raised:
            await create_running_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                requested_by_user_id=USER_ID,
                role="sales",
                task="Task",
                provider_name="openai",
                model_name="gpt-5.6-terra",
            )

        self.assertNotIn(
            "private PostgreSQL detail",
            str(raised.exception),
        )


class ReadExecutionTests(
    unittest.IsolatedAsyncioTestCase,
):
    async def test_get_is_tenant_scoped(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
        )

        session = _FakeSession(
            stored_execution=execution,
        )

        result = await get_ai_agent_execution(
            session,
            business_id=BUSINESS_A_ID,
            execution_id=execution.id,
        )

        self.assertIs(
            result,
            execution,
        )

        self.assertEqual(
            session.requested_business_id,
            BUSINESS_A_ID,
        )

        self.assertEqual(
            session.requested_execution_id,
            execution.id,
        )

    async def test_cross_tenant_execution_returns_not_found(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
        )

        session = _FakeSession(
            stored_execution=execution,
        )

        with self.assertRaises(
            AIAgentExecutionNotFoundError,
        ):
            await get_ai_agent_execution(
                session,
                business_id=BUSINESS_B_ID,
                execution_id=execution.id,
            )

    async def test_missing_execution_returns_not_found(
        self,
    ) -> None:
        session = _FakeSession()

        with self.assertRaises(
            AIAgentExecutionNotFoundError,
        ):
            await get_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=uuid4(),
            )

    async def test_read_database_failure_is_sanitized(
        self,
    ) -> None:
        session = _FakeSession(
            scalar_error=SQLAlchemyError(
                "secret database error"
            ),
        )

        with self.assertRaises(
            AIAgentExecutionPersistenceError,
        ) as raised:
            await get_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=uuid4(),
            )

        self.assertNotIn(
            "secret database error",
            str(raised.exception),
        )


class FinalizeExecutionTests(
    unittest.IsolatedAsyncioTestCase,
):
    async def test_completed_result_finalizes_execution(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
            role="sales",
        )

        session = _FakeSession(
            stored_execution=execution,
        )

        result = _successful_result(
            business_id=BUSINESS_A_ID,
            role="sales",
            status="completed",
        )

        finalized = (
            await finalize_successful_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
                result=result,
                duration_ms=1250,
                input_tokens=1400,
                output_tokens=320,
                estimated_cost_usd=Decimal(
                    "0.006400"
                ),
                provider_request_id="req_safe_123",
            )
        )

        self.assertEqual(
            finalized.status,
            "completed",
        )

        self.assertEqual(
            finalized.context_revision,
            CONTEXT_REVISION,
        )

        self.assertEqual(
            finalized.context_source_count,
            3,
        )

        self.assertEqual(
            finalized.business_brain_source_count,
            2,
        )

        self.assertEqual(
            finalized.memory_source_count,
            1,
        )

        self.assertEqual(
            finalized.output_summary,
            "Safe AI result.",
        )

        self.assertEqual(
            finalized.recommendations,
            [
                "Continue with the next step.",
            ],
        )

        self.assertEqual(
            finalized.proposed_actions,
            [],
        )

        self.assertEqual(
            finalized.duration_ms,
            1250,
        )

        self.assertEqual(
            finalized.input_tokens,
            1400,
        )

        self.assertEqual(
            finalized.output_tokens,
            320,
        )

        self.assertEqual(
            finalized.estimated_cost_usd,
            Decimal("0.006400"),
        )

        self.assertEqual(
            finalized.provider_request_id,
            "req_safe_123",
        )

        self.assertIsNotNone(
            finalized.completed_at,
        )

        self.assertEqual(
            session.commit_calls,
            0,
        )

    async def test_needs_approval_preserves_action_as_data_only(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
            role="sales",
        )

        session = _FakeSession(
            stored_execution=execution,
        )

        result = _successful_result(
            business_id=BUSINESS_A_ID,
            role="sales",
            status="needs_approval",
        )

        finalized = (
            await finalize_successful_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
                result=result,
            )
        )

        self.assertEqual(
            finalized.status,
            "needs_approval",
        )

        self.assertEqual(
            len(finalized.proposed_actions),
            1,
        )

        action = finalized.proposed_actions[0]

        self.assertEqual(
            action["action_type"],
            "send_customer_message",
        )

        self.assertTrue(
            action["requires_approval"],
        )

    async def test_blocked_result_is_valid_terminal_state(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
            role="support",
        )

        session = _FakeSession(
            stored_execution=execution,
        )

        result = _successful_result(
            business_id=BUSINESS_A_ID,
            role="support",
            status="blocked",
        )

        finalized = (
            await finalize_successful_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
                result=result,
            )
        )

        self.assertEqual(
            finalized.status,
            "blocked",
        )

        self.assertIsNotNone(
            finalized.completed_at,
        )

    async def test_result_from_other_business_is_rejected(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
            role="sales",
        )

        session = _FakeSession(
            stored_execution=execution,
        )

        result = _successful_result(
            business_id=BUSINESS_B_ID,
            role="sales",
            status="completed",
        )

        with self.assertRaises(
            AIAgentExecutionValidationError,
        ):
            await finalize_successful_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
                result=result,
            )

    async def test_result_role_mismatch_is_rejected(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
            role="sales",
        )

        session = _FakeSession(
            stored_execution=execution,
        )

        result = _successful_result(
            business_id=BUSINESS_A_ID,
            role="analytics",
            status="completed",
        )

        with self.assertRaises(
            AIAgentExecutionValidationError,
        ):
            await finalize_successful_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
                result=result,
            )

    async def test_terminal_execution_cannot_be_finalized_twice(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
            role="sales",
        )

        execution.status = "completed"
        execution.completed_at = datetime.now(
            UTC
        )

        session = _FakeSession(
            stored_execution=execution,
        )

        with self.assertRaises(
            AIAgentExecutionStateError,
        ):
            await finalize_successful_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
                result=_successful_result(
                    business_id=BUSINESS_A_ID,
                    role="sales",
                    status="completed",
                ),
            )

    async def test_invalid_usage_metadata_is_rejected(
        self,
    ) -> None:
        cases = (
            {
                "duration_ms": -1,
            },
            {
                "input_tokens": -1,
            },
            {
                "output_tokens": -1,
            },
            {
                "estimated_cost_usd": Decimal(
                    "-0.01"
                ),
            },
        )

        for kwargs in cases:
            with self.subTest(
                kwargs=kwargs
            ):
                execution = _running_execution(
                    business_id=BUSINESS_A_ID,
                    role="sales",
                )

                session = _FakeSession(
                    stored_execution=execution,
                )

                with self.assertRaises(
                    AIAgentExecutionValidationError,
                ):
                    await finalize_successful_ai_agent_execution(
                        session,
                        business_id=BUSINESS_A_ID,
                        execution_id=execution.id,
                        result=_successful_result(
                            business_id=BUSINESS_A_ID,
                            role="sales",
                            status="completed",
                        ),
                        **kwargs,
                    )

    async def test_finalize_database_failure_is_sanitized(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
            role="sales",
        )

        session = _FakeSession(
            stored_execution=execution,
            flush_error=SQLAlchemyError(
                "private persistence detail"
            ),
        )

        with self.assertRaises(
            AIAgentExecutionPersistenceError,
        ) as raised:
            await finalize_successful_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
                result=_successful_result(
                    business_id=BUSINESS_A_ID,
                    role="sales",
                    status="completed",
                ),
            )

        self.assertNotIn(
            "private persistence detail",
            str(raised.exception),
        )


class FailExecutionTests(
    unittest.IsolatedAsyncioTestCase,
):
    async def test_running_execution_can_be_failed_safely(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
        )

        session = _FakeSession(
            stored_execution=execution,
        )

        failed = await fail_ai_agent_execution(
            session,
            business_id=BUSINESS_A_ID,
            execution_id=execution.id,
            failure_code="provider_unavailable",
            duration_ms=900,
        )

        self.assertEqual(
            failed.status,
            "failed",
        )

        self.assertEqual(
            failed.failure_code,
            "provider_unavailable",
        )

        self.assertEqual(
            failed.duration_ms,
            900,
        )

        self.assertEqual(
            failed.recommendations,
            [],
        )

        self.assertEqual(
            failed.proposed_actions,
            [],
        )

        self.assertIsNone(
            failed.output_summary,
        )

        self.assertIsNotNone(
            failed.completed_at,
        )

        self.assertEqual(
            session.commit_calls,
            0,
        )

    async def test_failure_code_is_trimmed(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
        )

        session = _FakeSession(
            stored_execution=execution,
        )

        failed = await fail_ai_agent_execution(
            session,
            business_id=BUSINESS_A_ID,
            execution_id=execution.id,
            failure_code="  context_unavailable  ",
        )

        self.assertEqual(
            failed.failure_code,
            "context_unavailable",
        )

    async def test_blank_failure_code_is_rejected(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
        )

        session = _FakeSession(
            stored_execution=execution,
        )

        with self.assertRaises(
            AIAgentExecutionValidationError,
        ):
            await fail_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
                failure_code="   ",
            )

    async def test_terminal_execution_cannot_be_failed_again(
        self,
    ) -> None:
        execution = _running_execution(
            business_id=BUSINESS_A_ID,
        )

        execution.status = "failed"
        execution.completed_at = datetime.now(
            UTC
        )

        session = _FakeSession(
            stored_execution=execution,
        )

        with self.assertRaises(
            AIAgentExecutionStateError,
        ):
            await fail_ai_agent_execution(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
                failure_code="provider_unavailable",
            )


class _FakeSession:
    def __init__(
        self,
        *,
        stored_execution: AIAgentExecution | None = None,
        flush_error: SQLAlchemyError | None = None,
        scalar_error: SQLAlchemyError | None = None,
    ) -> None:
        self.stored_execution = stored_execution
        self.flush_error = flush_error
        self.scalar_error = scalar_error

        self.added: list[
            AIAgentExecution
        ] = []

        self.flush_calls = 0
        self.commit_calls = 0

        self.requested_business_id: UUID | None = None
        self.requested_execution_id: UUID | None = None

    def add(
        self,
        value: AIAgentExecution,
    ) -> None:
        if value.id is None:
            value.id = uuid4()

        self.added.append(
            value
        )

        self.stored_execution = value

    async def flush(
        self,
    ) -> None:
        self.flush_calls += 1

        if self.flush_error is not None:
            raise self.flush_error

    async def commit(
        self,
    ) -> None:
        self.commit_calls += 1

    async def scalar(
        self,
        statement,
    ):
        if self.scalar_error is not None:
            raise self.scalar_error

        parameters = (
            statement.compile().params
        )

        for name, value in parameters.items():
            if name.startswith(
                "id_"
            ):
                self.requested_execution_id = value

            if name.startswith(
                "business_id_"
            ):
                self.requested_business_id = value

        execution = self.stored_execution

        if execution is None:
            return None

        if (
            execution.id
            != self.requested_execution_id
        ):
            return None

        if (
            execution.business_id
            != self.requested_business_id
        ):
            return None

        return execution


def _running_execution(
    *,
    business_id: UUID,
    role: str = "sales",
) -> AIAgentExecution:
    return AIAgentExecution(
        id=uuid4(),
        business_id=business_id,
        requested_by_user_id=USER_ID,
        role=role,
        trigger_type="api",
        status="running",
        task="Recommend the next step.",
        provider_name="openai",
        model_name="gpt-5.6-terra",
        context_revision=None,
        context_source_count=0,
        business_brain_source_count=0,
        memory_source_count=0,
        output_summary=None,
        recommendations=[],
        proposed_actions=[],
        failure_code=None,
        provider_request_id=None,
        duration_ms=None,
        input_tokens=None,
        output_tokens=None,
        estimated_cost_usd=None,
        completed_at=None,
    )


def _successful_result(
    *,
    business_id: UUID,
    role: str,
    status: str,
) -> AIAgentExecutionResult:
    actions = []

    if status == "needs_approval":
        actions = [
            AIAgentProposedAction(
                action_type="send_customer_message",
                description=(
                    "Send the prepared customer follow-up."
                ),
                risk_level="medium",
                requires_approval=True,
            )
        ]

    return AIAgentExecutionResult(
        business_id=business_id,
        role=role,  # type: ignore[arg-type]
        context_revision=CONTEXT_REVISION,
        context_source_count=3,
        business_brain_source_count=2,
        memory_source_count=1,
        output=AIAgentStructuredOutput(
            status=status,  # type: ignore[arg-type]
            summary="Safe AI result.",
            recommendations=[
                "Continue with the next step.",
            ],
            proposed_actions=actions,
        ),
    )