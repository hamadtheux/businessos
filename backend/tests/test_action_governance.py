from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from uuid import UUID, uuid4

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.ai_action import AIActionConflictError  # noqa: E402
from app.models.ai_action import AIAction  # noqa: E402
from app.models.approval_request import ApprovalRequest  # noqa: E402
from app.services.action_governance import (  # noqa: E402
    govern_ai_action,
    govern_materialized_ai_actions,
)
from app.services.action_policy import evaluate_ai_action_policy  # noqa: E402


BUSINESS_ID = UUID("f1000000-0000-0000-0000-000000000001")
USER_ID = UUID("f2000000-0000-0000-0000-000000000002")


class ActionGovernanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_allow_policy_transitions_proposed_to_ready(self) -> None:
        action = _action(
            action_type="update_crm",
            payload={"customer_ref": "lead-1", "stage": "qualified"},
            proposed_approval=False,
        )
        session = _FakeSession(actions=[action])
        result = await evaluate_ai_action_policy(
            session,
            business_id=BUSINESS_ID,
            action_id=action.id,
        )
        self.assertIs(result, action)
        self.assertEqual(action.status, "ready")
        self.assertEqual(action.policy_decision, "allow")
        self.assertEqual(action.policy_reason_code, "policy_allow")
        self.assertIsNotNone(action.policy_evaluated_at)
        self.assertEqual(session.commit_calls, 0)

    async def test_repeated_same_policy_evaluation_is_idempotent(self) -> None:
        action = _action(
            action_type="update_crm",
            payload={"customer_ref": "lead-1", "stage": "qualified"},
            proposed_approval=False,
        )
        session = _FakeSession(actions=[action])
        await evaluate_ai_action_policy(
            session,
            business_id=BUSINESS_ID,
            action_id=action.id,
        )
        evaluated_at = action.policy_evaluated_at
        await evaluate_ai_action_policy(
            session,
            business_id=BUSINESS_ID,
            action_id=action.id,
        )
        self.assertEqual(action.policy_evaluated_at, evaluated_at)
        self.assertEqual(session.flush_calls, 1)

    async def test_conflicting_reevaluation_fails_closed(self) -> None:
        action = _action(
            action_type="update_crm",
            payload={"customer_ref": "lead-1", "stage": "qualified"},
            proposed_approval=False,
        )
        session = _FakeSession(actions=[action])
        await evaluate_ai_action_policy(
            session,
            business_id=BUSINESS_ID,
            action_id=action.id,
        )
        action.action_payload = {}
        with self.assertRaises(AIActionConflictError):
            await evaluate_ai_action_policy(
                session,
                business_id=BUSINESS_ID,
                action_id=action.id,
            )

    async def test_governance_creates_pending_approval_without_execution(self) -> None:
        action = _action(
            action_type="send_email",
            payload={
                "recipient_ref": "customer-1",
                "subject": "Hello",
                "body": "Hello",
            },
            proposed_approval=False,
        )
        session = _FakeSession(actions=[action])
        result = await govern_ai_action(
            session,
            business_id=BUSINESS_ID,
            action_id=action.id,
            requested_by_user_id=USER_ID,
        )
        self.assertEqual(result.action.status, "pending_approval")
        self.assertEqual(result.action.policy_decision, "require_approval")
        self.assertIsNotNone(result.approval)
        self.assertEqual(result.approval.status, "pending")
        self.assertEqual(result.approval.requested_by_user_id, USER_ID)
        self.assertIsNone(action.execution_started_at)
        self.assertEqual(session.commit_calls, 0)

    async def test_governance_blocks_unknown_without_approval(self) -> None:
        action = _action(action_type="unknown", payload={})
        session = _FakeSession(actions=[action])
        result = await govern_ai_action(
            session,
            business_id=BUSINESS_ID,
            action_id=action.id,
        )
        self.assertEqual(result.action.status, "blocked")
        self.assertEqual(result.action.policy_reason_code, "unsupported_action")
        self.assertIsNone(result.approval)

    async def test_batch_order_is_stable_and_tenant_checked(self) -> None:
        first = _action(
            action_type="update_crm",
            payload={"customer_ref": "lead-1", "note": "Follow up"},
            proposed_approval=False,
            proposal_index=0,
        )
        second = _action(
            action_type="update_crm",
            payload={"customer_ref": "lead-2", "note": "Follow up"},
            proposed_approval=False,
            proposal_index=1,
        )
        session = _FakeSession(actions=[second, first])
        governed = await govern_materialized_ai_actions(
            session,
            business_id=BUSINESS_ID,
            actions=[second, first],
        )
        self.assertEqual([item.action.id for item in governed], [first.id, second.id])


class _FakeSession:
    def __init__(self, *, actions: list[AIAction]) -> None:
        self.actions = actions
        self.approvals: list[ApprovalRequest] = []
        self.flush_calls = 0
        self.commit_calls = 0

    def add(self, value: ApprovalRequest) -> None:
        if value.id is None:
            value.id = uuid4()
        self.approvals.append(value)

    async def flush(self) -> None:
        self.flush_calls += 1

    async def refresh(self, value, *, attribute_names) -> None:
        now = datetime.now(UTC)
        if "created_at" in attribute_names and value.created_at is None:
            value.created_at = now
        if "updated_at" in attribute_names:
            value.updated_at = now

    async def scalar(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        params = statement.compile().params
        business_id = _param(params, "business_id_")
        if entity is AIAction:
            action_id = _param(params, "id_")
            return next(
                (
                    action
                    for action in self.actions
                    if action.id == action_id and action.business_id == business_id
                ),
                None,
            )
        if entity is ApprovalRequest:
            action_id = _param(params, "action_id_")
            status = _param(params, "status_")
            return next(
                (
                    approval
                    for approval in self.approvals
                    if approval.action_id == action_id
                    and approval.business_id == business_id
                    and (status is None or approval.status == status)
                ),
                None,
            )
        return None


def _param(params: dict[str, object], prefix: str):
    return next((value for key, value in params.items() if key.startswith(prefix)), None)


def _action(
    *,
    action_type: str,
    payload: dict[str, object],
    proposed_approval: bool = True,
    proposal_index: int = 0,
) -> AIAction:
    return AIAction(
        id=uuid4(),
        business_id=BUSINESS_ID,
        execution_id=uuid4(),
        proposal_index=proposal_index,
        action_type=action_type,
        description="Govern this action.",
        risk_level="low",
        proposed_requires_approval=proposed_approval,
        status="proposed",
        action_payload=payload,
        policy_decision=None,
        policy_reason_code=None,
        policy_evaluated_at=None,
        execution_started_at=None,
        execution_completed_at=None,
        result_summary=None,
        failure_code=None,
        external_reference_id=None,
    )
