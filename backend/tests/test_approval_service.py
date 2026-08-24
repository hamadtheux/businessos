from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.approval import (  # noqa: E402
    ApprovalNotFoundError,
    ApprovalPersistenceError,
    ApprovalStateError,
)
from app.models.ai_action import AIAction  # noqa: E402
from app.models.approval_request import ApprovalRequest  # noqa: E402
from app.services.approval import (  # noqa: E402
    approve_approval_request,
    cancel_approval_request,
    create_approval_request,
    expire_approval_request,
    list_approval_requests,
    reject_approval_request,
)


BUSINESS_ID = UUID("c1000000-0000-0000-0000-000000000001")
OTHER_BUSINESS_ID = UUID("c2000000-0000-0000-0000-000000000002")
USER_ID = UUID("c3000000-0000-0000-0000-000000000003")


class ApprovalServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_is_tenant_scoped_idempotent_and_flush_only(self) -> None:
        action = _pending_action()
        session = _FakeSession(actions=[action])

        first = await create_approval_request(
            session,
            business_id=BUSINESS_ID,
            action_id=action.id,
            reason_code="external_communication",
        )
        second = await create_approval_request(
            session,
            business_id=BUSINESS_ID,
            action_id=action.id,
            reason_code="external_communication",
        )

        self.assertIs(first, second)
        self.assertEqual(len(session.approvals), 1)
        self.assertEqual(first.status, "pending")
        self.assertEqual(session.flush_calls, 1)
        self.assertEqual(session.commit_calls, 0)
        self.assertGreaterEqual(session.for_update_calls, 3)

    async def test_cross_tenant_create_is_safe_not_found(self) -> None:
        session = _FakeSession(actions=[_pending_action()])
        with self.assertRaises(ApprovalNotFoundError):
            await create_approval_request(
                session,
                business_id=OTHER_BUSINESS_ID,
                action_id=session.actions[0].id,
                reason_code="external_communication",
            )

    async def test_blocked_action_cannot_receive_approval(self) -> None:
        action = _pending_action()
        action.status = "blocked"
        action.policy_decision = "block"
        action.policy_reason_code = "unsupported_action"
        session = _FakeSession(actions=[action])
        with self.assertRaises(ApprovalStateError):
            await create_approval_request(
                session,
                business_id=BUSINESS_ID,
                action_id=action.id,
                reason_code="unsupported_action",
            )

    async def test_approval_makes_action_ready_without_execution(self) -> None:
        action, approval, session = _approval_fixture()
        result = await approve_approval_request(
            session,
            business_id=BUSINESS_ID,
            approval_id=approval.id,
            decided_by_user_id=USER_ID,
            decision_note=" Approved by owner. ",
        )
        self.assertEqual(result.status, "approved")
        self.assertEqual(result.decision_note, "Approved by owner.")
        self.assertEqual(result.decided_by_user_id, USER_ID)
        self.assertEqual(result.decision_actor_id, USER_ID)
        self.assertEqual(action.status, "ready")
        self.assertIsNone(action.execution_started_at)

    async def test_rejection_rejects_action(self) -> None:
        action, approval, session = _approval_fixture()
        await reject_approval_request(
            session,
            business_id=BUSINESS_ID,
            approval_id=approval.id,
            decided_by_user_id=USER_ID,
            decision_note="Not appropriate.",
        )
        self.assertEqual(approval.status, "rejected")
        self.assertEqual(action.status, "rejected")

    async def test_expiration_expires_action(self) -> None:
        action, approval, session = _approval_fixture(
            expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        await expire_approval_request(
            session,
            business_id=BUSINESS_ID,
            approval_id=approval.id,
        )
        self.assertEqual(approval.status, "expired")
        self.assertEqual(action.status, "expired")
        self.assertIsNone(approval.decision_actor_id)

    async def test_unexpired_request_cannot_expire(self) -> None:
        _, approval, session = _approval_fixture(
            expires_at=datetime.now(UTC) + timedelta(hours=1)
        )
        with self.assertRaises(ApprovalStateError):
            await expire_approval_request(
                session,
                business_id=BUSINESS_ID,
                approval_id=approval.id,
            )

    async def test_cancel_is_idempotent_and_cancels_action(self) -> None:
        action, approval, session = _approval_fixture()
        first = await cancel_approval_request(
            session,
            business_id=BUSINESS_ID,
            approval_id=approval.id,
            canceled_by_user_id=USER_ID,
        )
        second = await cancel_approval_request(
            session,
            business_id=BUSINESS_ID,
            approval_id=approval.id,
            canceled_by_user_id=USER_ID,
        )
        self.assertIs(first, second)
        self.assertEqual(action.status, "canceled")
        self.assertEqual(approval.status, "canceled")

    async def test_decided_request_cannot_transition_to_opposite_decision(self) -> None:
        _, approval, session = _approval_fixture()
        await reject_approval_request(
            session,
            business_id=BUSINESS_ID,
            approval_id=approval.id,
            decided_by_user_id=USER_ID,
        )
        with self.assertRaises(ApprovalStateError):
            await approve_approval_request(
                session,
                business_id=BUSINESS_ID,
                approval_id=approval.id,
                decided_by_user_id=USER_ID,
            )

    async def test_approved_request_cannot_be_reapproved_after_execution(self) -> None:
        action, approval, session = _approval_fixture()
        await approve_approval_request(
            session,
            business_id=BUSINESS_ID,
            approval_id=approval.id,
            decided_by_user_id=USER_ID,
        )
        action.status = "executing"
        action.execution_started_at = datetime.now(UTC)
        with self.assertRaises(ApprovalStateError):
            await approve_approval_request(
                session,
                business_id=BUSINESS_ID,
                approval_id=approval.id,
                decided_by_user_id=USER_ID,
            )

    async def test_list_is_tenant_and_status_scoped(self) -> None:
        pending = _approval(_pending_action())
        approved = _approval(_pending_action())
        approved.status = "approved"
        approved.decided_at = datetime.now(UTC)
        approved.decision_actor_id = USER_ID
        other = _approval(_pending_action(business_id=OTHER_BUSINESS_ID))
        session = _FakeSession(approvals=[pending, approved, other])
        result = await list_approval_requests(
            session,
            business_id=BUSINESS_ID,
            approval_status="pending",
        )
        self.assertEqual(result, [pending])

    async def test_database_error_is_sanitized(self) -> None:
        session = _FakeSession(
            actions=[_pending_action()],
            scalar_error=SQLAlchemyError("private database detail"),
        )
        with self.assertRaises(ApprovalPersistenceError) as raised:
            await create_approval_request(
                session,
                business_id=BUSINESS_ID,
                action_id=session.actions[0].id,
                reason_code="external_communication",
            )
        self.assertNotIn("private database detail", str(raised.exception))


class _ScalarCollection:
    def __init__(self, values: list[ApprovalRequest]) -> None:
        self.values = values

    def all(self) -> list[ApprovalRequest]:
        return self.values


class _FakeSession:
    def __init__(
        self,
        *,
        actions: list[AIAction] | None = None,
        approvals: list[ApprovalRequest] | None = None,
        scalar_error: SQLAlchemyError | None = None,
    ) -> None:
        self.actions = list(actions or [])
        self.approvals = list(approvals or [])
        self.scalar_error = scalar_error
        self.flush_calls = 0
        self.commit_calls = 0
        self.for_update_calls = 0

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

    async def commit(self) -> None:
        self.commit_calls += 1

    async def scalar(self, statement):
        if self.scalar_error is not None:
            raise self.scalar_error
        if statement._for_update_arg is not None:  # noqa: SLF001
            self.for_update_calls += 1

        entity = statement.column_descriptions[0].get("entity")
        params = statement.compile().params
        business_id = _param(params, "business_id_")

        if entity is AIAction:
            action_id = _param(params, "id_")
            return next(
                (
                    item
                    for item in self.actions
                    if item.id == action_id and item.business_id == business_id
                ),
                None,
            )

        if entity is ApprovalRequest:
            approval_id = _param(params, "id_")
            action_id = _param(params, "action_id_")
            status = _param(params, "status_")
            values = [item for item in self.approvals if item.business_id == business_id]
            if approval_id is not None:
                values = [item for item in values if item.id == approval_id]
            if action_id is not None:
                values = [item for item in values if item.action_id == action_id]
            if status is not None:
                values = [item for item in values if item.status == status]
            return values[0] if values else None
        return None

    async def scalars(self, statement) -> _ScalarCollection:
        params = statement.compile().params
        business_id = _param(params, "business_id_")
        status = _param(params, "status_")
        values = [item for item in self.approvals if item.business_id == business_id]
        if status is not None:
            values = [item for item in values if item.status == status]
        values.sort(key=lambda item: str(item.id), reverse=True)
        return _ScalarCollection(values)


def _param(params: dict[str, object], prefix: str):
    return next((value for key, value in params.items() if key.startswith(prefix)), None)


def _pending_action(*, business_id: UUID = BUSINESS_ID) -> AIAction:
    return AIAction(
        id=uuid4(),
        business_id=business_id,
        execution_id=uuid4(),
        proposal_index=0,
        action_type="send_customer_message",
        description="Send a customer message.",
        risk_level="medium",
        proposed_requires_approval=True,
        status="pending_approval",
        action_payload={"customer_ref": "customer-1", "message": "Hello"},
        policy_decision="require_approval",
        policy_reason_code="external_communication",
        policy_evaluated_at=datetime.now(UTC),
        execution_started_at=None,
        execution_completed_at=None,
        result_summary=None,
        failure_code=None,
        external_reference_id=None,
    )


def _approval(
    action: AIAction,
    *,
    expires_at: datetime | None = None,
) -> ApprovalRequest:
    now = datetime.now(UTC) - timedelta(minutes=1)
    return ApprovalRequest(
        id=uuid4(),
        business_id=action.business_id,
        action_id=action.id,
        requested_by_user_id=None,
        status="pending",
        reason_code="external_communication",
        requested_at=now,
        expires_at=expires_at,
        decided_at=None,
        decided_by_user_id=None,
        decision_actor_id=None,
        decision_note=None,
        created_at=now,
        updated_at=now,
    )


def _approval_fixture(
    *,
    expires_at: datetime | None = None,
) -> tuple[AIAction, ApprovalRequest, _FakeSession]:
    action = _pending_action()
    approval = _approval(action, expires_at=expires_at)
    return action, approval, _FakeSession(actions=[action], approvals=[approval])
