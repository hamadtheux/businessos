from __future__ import annotations

import os
import unittest
from time import perf_counter_ns
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.services.ai_command_execution import _fail_outcome  # noqa: E402


BUSINESS_ID = UUID("97000000-0000-4000-8000-000000000001")
EXECUTION_ID = UUID("97000000-0000-4000-8000-000000000002")


class _RollbackExpiringExecution:
    def __init__(self) -> None:
        self.expired = False

    @property
    def id(self) -> UUID:
        if self.expired:
            raise AssertionError("execution.id was accessed after rollback")
        return EXECUTION_ID


class _Session:
    def __init__(self, execution: _RollbackExpiringExecution) -> None:
        self.execution = execution
        self.rollback_calls = 0
        self.commit_calls = 0

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self.execution.expired = True

    async def commit(self) -> None:
        self.commit_calls += 1


class AICommandExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_path_captures_execution_id_before_rollback(self) -> None:
        execution = _RollbackExpiringExecution()
        session = _Session(execution)
        failed = SimpleNamespace(id=EXECUTION_ID, status="failed")
        record_failure = AsyncMock(return_value=failed)

        with patch(
            "app.services.ai_command_execution.fail_ai_agent_execution",
            new=record_failure,
        ):
            outcome = await _fail_outcome(
                session,
                BUSINESS_ID,
                execution,
                "capability_violation",
                perf_counter_ns(),
            )

        self.assertIs(outcome.execution, failed)
        self.assertIsNone(outcome.result)
        self.assertEqual(outcome.failure_code, "capability_violation")
        self.assertEqual(session.rollback_calls, 1)
        self.assertEqual(session.commit_calls, 1)
        self.assertEqual(record_failure.await_args.kwargs["execution_id"], EXECUTION_ID)


if __name__ == "__main__":
    unittest.main()
