from __future__ import annotations

import os
import unittest

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.models.action_execution_attempt import ActionExecutionAttempt  # noqa: E402
from app.models.ai_action import AIAction  # noqa: E402


class ActionExecutionAttemptModelTests(unittest.TestCase):
    def test_tenant_safe_composite_action_foreign_key(self) -> None:
        composite = next(
            item
            for item in ActionExecutionAttempt.__table__.constraints
            if isinstance(item, ForeignKeyConstraint)
            and item.name
            == "fk_action_execution_attempts_action_business_ai_actions"
        )
        self.assertEqual(
            [column.name for column in composite.columns],
            ["action_id", "business_id"],
        )
        self.assertEqual(composite.ondelete, "CASCADE")

    def test_attempt_and_idempotency_uniqueness(self) -> None:
        names = {
            item.name
            for item in ActionExecutionAttempt.__table__.constraints
            if isinstance(item, UniqueConstraint)
        }
        self.assertIn("uq_action_execution_attempts_action_number", names)
        self.assertIn("uq_action_execution_attempts_idempotency_key", names)

    def test_lifecycle_checks_and_active_partial_index_exist(self) -> None:
        checks = {
            item.name
            for item in ActionExecutionAttempt.__table__.constraints
            if isinstance(item, CheckConstraint)
        }
        self.assertIn("ck_action_execution_attempts_consistent_lifecycle", checks)
        self.assertIn("ck_action_execution_attempts_consistent_failure", checks)
        index = next(
            item
            for item in ActionExecutionAttempt.__table__.indexes
            if item.name == "ix_action_execution_attempts_one_active_action"
        )
        self.assertTrue(index.unique)
        self.assertIsNotNone(index.dialect_options["postgresql"]["where"])

    def test_ai_action_lifecycle_supports_queue_and_uncertainty(self) -> None:
        valid_status = next(
            item
            for item in AIAction.__table__.constraints
            if item.name == "ck_ai_actions_valid_status"
        )
        sql = str(valid_status.sqltext)
        self.assertIn("'queued'", sql)
        self.assertIn("'uncertain'", sql)
