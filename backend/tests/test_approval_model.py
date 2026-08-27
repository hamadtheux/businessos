from __future__ import annotations

import os
import unittest

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.models.ai_action import AIAction  # noqa: E402
from app.models.approval_request import ApprovalRequest  # noqa: E402


class ApprovalModelTests(unittest.TestCase):
    def test_ai_action_has_tenant_safe_composite_candidate_key(self) -> None:
        names = {
            constraint.name
            for constraint in AIAction.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertIn("uq_ai_actions_id_business", names)

    def test_action_link_uses_composite_tenant_foreign_key(self) -> None:
        constraints = [
            item
            for item in ApprovalRequest.__table__.constraints
            if isinstance(item, ForeignKeyConstraint)
        ]
        composite = next(
            item
            for item in constraints
            if item.name == "fk_approval_requests_action_business_ai_actions"
        )
        self.assertEqual(
            [column.name for column in composite.columns],
            ["action_id", "business_id"],
        )
        self.assertEqual(composite.ondelete, "CASCADE")

    def test_decider_user_deletion_preserves_actor_snapshot(self) -> None:
        decider_fk = next(
            item
            for item in ApprovalRequest.__table__.foreign_key_constraints
            if "decided_by_user_id" in {column.name for column in item.columns}
        )
        self.assertEqual(decider_fk.ondelete, "SET NULL")
        self.assertIn("decision_actor_id", ApprovalRequest.__table__.columns)

    def test_lifecycle_check_and_unique_pending_index_exist(self) -> None:
        checks = {
            constraint.name
            for constraint in ApprovalRequest.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        self.assertIn("ck_approval_requests_consistent_lifecycle", checks)
        self.assertIn(
            "ck_approval_requests_consistent_action_authorization_snapshot",
            checks,
        )
        self.assertIn("action_type_snapshot", ApprovalRequest.__table__.columns)
        self.assertIn(
            "authorized_payload_hash_snapshot",
            ApprovalRequest.__table__.columns,
        )

        pending_index = next(
            item
            for item in ApprovalRequest.__table__.indexes
            if item.name == "ix_approval_requests_one_pending_action"
        )
        self.assertIsInstance(pending_index, Index)
        self.assertTrue(pending_index.unique)
        self.assertIsNotNone(pending_index.dialect_options["postgresql"]["where"])
