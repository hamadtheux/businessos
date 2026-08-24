from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.action_execution_attempt import (  # noqa: E402
    DirectActionDispatchDisabledError,
)
from app.models.ai_action import AIAction  # noqa: E402
from app.models.approval_request import ApprovalRequest  # noqa: E402
from app.models.business import Business  # noqa: E402
from app.schemas.ai_action_payload import ActionPayload  # noqa: E402
from app.services.action_executor import (  # noqa: E402
    ActionExecutionResult,
    ActionHandlerRegistry,
    execute_ready_ai_action,
)


BUSINESS_ID = UUID("d1000000-0000-0000-0000-000000000001")
OTHER_BUSINESS_ID = UUID("d2000000-0000-0000-0000-000000000002")


class ActionExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_action_cannot_dispatch_handler_inside_transaction(self) -> None:
        action = _action()
        handler = _FakeHandler()
        session = _FakeSession(action=action)
        await self._assert_direct_dispatch_disabled(
            action=action,
            handler=handler,
            session=session,
        )
        self.assertEqual(action.status, "ready")
        self.assertIsNone(action.execution_started_at)
        self.assertEqual(session.commit_calls, 0)

    async def test_all_action_states_refuse_direct_dispatch(self) -> None:
        for action_status in ("proposed", "pending_approval", "blocked", "rejected", "expired", "executing", "succeeded", "failed", "canceled"):
            with self.subTest(status=action_status):
                action = _action(status=action_status)
                handler = _FakeHandler()
                await self._assert_direct_dispatch_disabled(
                    action=action,
                    handler=handler,
                )

    async def test_required_approval_does_not_enable_direct_dispatch(self) -> None:
        action = _action(policy_decision="require_approval")
        await self._assert_direct_dispatch_disabled(
            action=action,
            handler=_FakeHandler(),
        )

    async def test_approved_action_still_requires_durable_attempt_workflow(self) -> None:
        action = _action(policy_decision="require_approval")
        approval = _approved_request(action)
        handler = _FakeHandler()
        await self._assert_direct_dispatch_disabled(
            action=action,
            handler=handler,
            session=_FakeSession(action=action, approval=approval),
        )

    async def test_empty_handler_registry_is_fail_closed(self) -> None:
        action = _action()
        with self.assertRaises(DirectActionDispatchDisabledError):
            await execute_ready_ai_action(
                _FakeSession(action=action),
                business_id=BUSINESS_ID,
                action_id=action.id,
                handlers=ActionHandlerRegistry(),
            )
        self.assertEqual(action.status, "ready")
        self.assertIsNone(action.execution_started_at)

    async def test_currency_payload_never_reaches_direct_handler(self) -> None:
        action = _action(policy_decision="require_approval")
        action.action_type = "change_ad_budget"
        action.action_payload = {
            "campaign_ref": "campaign-1",
            "budget": "100.00",
            "currency": "EUR",
            "budget_period": "daily",
        }
        action.policy_reason_code = "ad_spend_change"
        approval = _approved_request(action)
        approval.reason_code = "ad_spend_change"
        handler = _FakeHandler()
        await self._assert_direct_dispatch_disabled(
            action=action,
            handler=handler,
            handlers=ActionHandlerRegistry({"change_ad_budget": handler}),
            session=_FakeSession(
                action=action,
                approval=approval,
                business_currency="USD",
            ),
        )
        self.assertEqual(action.status, "ready")

    async def test_malformed_payload_never_reaches_handler(self) -> None:
        action = _action()
        action.action_payload = {"customer_ref": "customer-1", "api_key": "secret"}
        handler = _FakeHandler()
        await self._assert_direct_dispatch_disabled(action=action, handler=handler)

    async def test_handler_error_cannot_occur_because_handler_is_not_called(self) -> None:
        action = _action()
        handler = _FakeHandler(error=RuntimeError("secret connector response"))
        with self.assertRaises(DirectActionDispatchDisabledError) as raised:
            await execute_ready_ai_action(
                _FakeSession(action=action),
                business_id=BUSINESS_ID,
                action_id=action.id,
                handlers=ActionHandlerRegistry({"update_crm": handler}),
            )
        self.assertNotIn("secret connector response", str(raised.exception))
        self.assertEqual(handler.calls, 0)
        self.assertEqual(action.status, "ready")
        self.assertIsNone(action.result_summary)

    async def test_handler_result_cannot_bypass_durable_attempt(self) -> None:
        action = _action()
        handler = _FakeHandler(
            result=ActionExecutionResult(
                succeeded=False,
                failure_code="connector_rejected",
                result_summary="The connector rejected the request.",
            )
        )
        await self._assert_direct_dispatch_disabled(action=action, handler=handler)
        self.assertEqual(action.status, "ready")
        self.assertIsNone(action.failure_code)

    async def test_direct_dispatch_is_disabled_before_any_tenant_lookup(self) -> None:
        action = _action()
        handler = _FakeHandler()
        with self.assertRaises(DirectActionDispatchDisabledError):
            await execute_ready_ai_action(
                _FakeSession(action=action),
                business_id=OTHER_BUSINESS_ID,
                action_id=action.id,
                handlers=ActionHandlerRegistry({"update_crm": handler}),
            )
        self.assertEqual(handler.calls, 0)

    async def _assert_direct_dispatch_disabled(
        self,
        *,
        action: AIAction,
        handler: "_FakeHandler",
        handlers: ActionHandlerRegistry | None = None,
        session: "_FakeSession | None" = None,
    ) -> None:
        with self.assertRaises(DirectActionDispatchDisabledError):
            await execute_ready_ai_action(
                session or _FakeSession(action=action),
                business_id=BUSINESS_ID,
                action_id=action.id,
                handlers=(
                    handlers
                    or ActionHandlerRegistry({"update_crm": handler})
                ),
            )
        self.assertEqual(handler.calls, 0)


class _FakeHandler:
    def __init__(
        self,
        *,
        result: ActionExecutionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or ActionExecutionResult(
            succeeded=True,
            result_summary="CRM updated.",
            external_reference_id="crm-event-1",
        )
        self.error = error
        self.calls = 0
        self.payload: ActionPayload | None = None
        self.idempotency_key: str | None = None

    async def execute(
        self,
        payload: ActionPayload,
        *,
        idempotency_key: str,
    ) -> ActionExecutionResult:
        self.calls += 1
        self.payload = payload
        self.idempotency_key = idempotency_key
        if self.error is not None:
            raise self.error
        return self.result


class _FakeSession:
    def __init__(
        self,
        *,
        action: AIAction,
        approval: ApprovalRequest | None = None,
        scalar_error: SQLAlchemyError | None = None,
        business_currency: str = "USD",
    ) -> None:
        self.action = action
        self.approval = approval
        self.scalar_error = scalar_error
        self.business_currency = business_currency
        self.flush_calls = 0
        self.commit_calls = 0

    async def scalar(self, statement):
        if self.scalar_error is not None:
            raise self.scalar_error
        entity = statement.column_descriptions[0].get("entity")
        params = statement.compile().params
        business_id = _param(params, "business_id_")
        action_id = _param(params, "id_") or _param(params, "action_id_")
        if entity is AIAction:
            if self.action.id == action_id and self.action.business_id == business_id:
                return self.action
            return None
        if entity is ApprovalRequest:
            if (
                self.approval is not None
                and self.approval.action_id == action_id
                and self.approval.business_id == business_id
                and self.approval.status == "approved"
            ):
                return self.approval.id
            return None
        if entity is Business:
            if business_id == BUSINESS_ID:
                return self.business_currency
            return None
        return None

    async def flush(self) -> None:
        self.flush_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1


def _param(params: dict[str, object], prefix: str):
    return next((value for key, value in params.items() if key.startswith(prefix)), None)


def _action(
    *,
    status: str = "ready",
    policy_decision: str = "allow",
) -> AIAction:
    return AIAction(
        id=uuid4(),
        business_id=BUSINESS_ID,
        execution_id=uuid4(),
        proposal_index=0,
        action_type="update_crm",
        description="Update the CRM lead.",
        risk_level="low",
        proposed_requires_approval=False,
        status=status,
        action_payload={"customer_ref": "lead-1", "stage": "qualified"},
        policy_decision=policy_decision,
        policy_reason_code=(
            "policy_allow"
            if policy_decision == "allow"
            else "human_approval_required"
        ),
        policy_evaluated_at=datetime.now(UTC),
        execution_started_at=None,
        execution_completed_at=None,
        result_summary=None,
        failure_code=None,
        external_reference_id=None,
    )


def _approved_request(action: AIAction) -> ApprovalRequest:
    now = datetime.now(UTC)
    return ApprovalRequest(
        id=uuid4(),
        business_id=action.business_id,
        action_id=action.id,
        requested_by_user_id=None,
        status="approved",
        reason_code="human_approval_required",
        requested_at=now,
        expires_at=None,
        decided_at=now,
        decided_by_user_id=None,
        decision_actor_id=uuid4(),
        decision_note=None,
        created_at=now,
        updated_at=now,
    )
