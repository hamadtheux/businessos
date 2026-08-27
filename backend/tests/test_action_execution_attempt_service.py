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

from app.exceptions.action_execution_attempt import (  # noqa: E402
    ActionExecutionAttemptConflictError,
    ActionExecutionAttemptNotFoundError,
    ActionExecutionAttemptPersistenceError,
    ActionExecutionAttemptStateError,
    ActionExecutionAttemptValidationError,
    ActionExecutionOutcomeUncertainError,
)
from app.models.action_execution_attempt import ActionExecutionAttempt  # noqa: E402
from app.models.ai_action import AIAction  # noqa: E402
from app.models.approval_request import ApprovalRequest  # noqa: E402
from app.models.business import Business  # noqa: E402
from app.services.action_execution_attempt import (  # noqa: E402
    build_action_attempt_idempotency_key,
    cancel_action_execution_attempt,
    claim_action_execution_attempt,
    list_queued_action_execution_attempts,
    list_stale_dispatching_action_execution_attempts,
    mark_stale_action_execution_attempt_uncertain,
    prepare_action_execution_attempt,
    record_action_execution_failure,
    record_action_execution_success,
    record_action_execution_uncertain,
)
from app.services.action_policy import canonical_action_payload_hash  # noqa: E402
from app.services.action_registry import ACTION_REGISTRY  # noqa: E402


BUSINESS_ID = UUID("11000000-0000-0000-0000-000000000001")
OTHER_BUSINESS_ID = UUID("12000000-0000-0000-0000-000000000002")
USER_ID = UUID("13000000-0000-0000-0000-000000000003")


class PrepareActionExecutionAttemptTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_allowed_action_queues_durable_intent_only(self) -> None:
        action = _ready_action()
        session = _FakeSession(actions=[action])
        attempt = await prepare_action_execution_attempt(
            session,
            business_id=BUSINESS_ID,
            action_id=action.id,
        )
        self.assertEqual(attempt.status, "queued")
        self.assertEqual(attempt.attempt_number, 1)
        self.assertEqual(
            attempt.idempotency_key,
            f"ai-action:{action.id}:attempt:1",
        )
        self.assertEqual(attempt.action_type, "update_crm")
        self.assertEqual(attempt.capability, "crm.customer.update")
        self.assertEqual(action.status, "queued")
        self.assertIsNone(action.execution_started_at)
        self.assertEqual(session.flush_calls, 1)
        self.assertEqual(session.commit_calls, 0)
        self.assertEqual(session.external_calls, 0)

    async def test_approved_required_action_queues(self) -> None:
        action = _ready_action(requires_approval=True)
        approval = _approved_request(action)
        session = _FakeSession(actions=[action], approvals=[approval])
        attempt = await prepare_action_execution_attempt(
            session,
            business_id=BUSINESS_ID,
            action_id=action.id,
        )
        self.assertEqual(attempt.status, "queued")

    async def test_missing_matching_approval_is_denied(self) -> None:
        action = _ready_action(requires_approval=True)
        with self.assertRaises(ActionExecutionAttemptStateError):
            await prepare_action_execution_attempt(
                _FakeSession(actions=[action]),
                business_id=BUSINESS_ID,
                action_id=action.id,
            )

    async def test_wrong_business_is_safe_not_found(self) -> None:
        action = _ready_action()
        with self.assertRaises(ActionExecutionAttemptNotFoundError):
            await prepare_action_execution_attempt(
                _FakeSession(actions=[action]),
                business_id=OTHER_BUSINESS_ID,
                action_id=action.id,
            )

    async def test_non_ready_states_are_denied(self) -> None:
        for status in ("blocked", "rejected", "expired", "canceled", "executing"):
            with self.subTest(status=status):
                action = _ready_action()
                action.status = status
                with self.assertRaises(ActionExecutionAttemptStateError):
                    await prepare_action_execution_attempt(
                        _FakeSession(actions=[action]),
                        business_id=BUSINESS_ID,
                        action_id=action.id,
                    )

    async def test_uncertain_action_has_explicit_retry_prohibition(self) -> None:
        action = _ready_action()
        action.status = "uncertain"
        action.execution_started_at = datetime.now(UTC)
        action.execution_completed_at = datetime.now(UTC)
        action.failure_code = "external_outcome_uncertain"
        with self.assertRaises(ActionExecutionOutcomeUncertainError):
            await prepare_action_execution_attempt(
                _FakeSession(actions=[action]),
                business_id=BUSINESS_ID,
                action_id=action.id,
            )

    async def test_malformed_payload_is_denied(self) -> None:
        action = _ready_action()
        action.action_payload = {
            "customer_ref": "lead-1",
            "api_key": "must-not-flow",
        }
        with self.assertRaises(ActionExecutionAttemptConflictError):
            await prepare_action_execution_attempt(
                _FakeSession(actions=[action]),
                business_id=BUSINESS_ID,
                action_id=action.id,
            )

    async def test_currency_mismatch_is_denied(self) -> None:
        action = _ready_action(requires_approval=True)
        action.action_type = "change_ad_budget"
        action.action_payload = {
            "campaign_ref": "campaign-1",
            "budget": "100.00",
            "currency": "EUR",
            "budget_period": "daily",
        }
        action.risk_level = "critical"
        action.policy_reason_code = "ad_spend_change"
        action.authorized_payload_hash = canonical_action_payload_hash(
            ACTION_REGISTRY.validate_payload(action.action_type, action.action_payload)
        )
        approval = _approved_request(action)
        approval.reason_code = "ad_spend_change"
        with self.assertRaises(ActionExecutionAttemptValidationError):
            await prepare_action_execution_attempt(
                _FakeSession(
                    actions=[action],
                    approvals=[approval],
                    business_currency="USD",
                ),
                business_id=BUSINESS_ID,
                action_id=action.id,
            )

    async def test_duplicate_active_attempt_is_denied(self) -> None:
        action = _ready_action()
        existing = _queued_attempt(action)
        with self.assertRaises(ActionExecutionAttemptConflictError):
            await prepare_action_execution_attempt(
                _FakeSession(actions=[action], attempts=[existing]),
                business_id=BUSINESS_ID,
                action_id=action.id,
            )

    async def test_next_attempt_has_stable_incremented_identity(self) -> None:
        action = _ready_action()
        previous = _queued_attempt(action, attempt_number=1)
        previous.status = "failed"
        previous.dispatch_started_at = previous.queued_at
        previous.lease_acquired_at = previous.queued_at
        previous.lease_expires_at = previous.queued_at + timedelta(minutes=1)
        previous.completed_at = previous.queued_at + timedelta(seconds=1)
        previous.failure_code = "connector_rejected"
        session = _FakeSession(actions=[action], attempts=[previous])
        current = await prepare_action_execution_attempt(
            session,
            business_id=BUSINESS_ID,
            action_id=action.id,
        )
        self.assertEqual(current.attempt_number, 2)
        self.assertEqual(
            current.idempotency_key,
            f"ai-action:{action.id}:attempt:2",
        )

    async def test_database_failure_is_sanitized(self) -> None:
        action = _ready_action()
        session = _FakeSession(
            actions=[action],
            scalar_error=SQLAlchemyError("private database details"),
        )
        with self.assertRaises(ActionExecutionAttemptPersistenceError) as raised:
            await prepare_action_execution_attempt(
                session,
                business_id=BUSINESS_ID,
                action_id=action.id,
            )
        self.assertNotIn("private database details", str(raised.exception))


class ClaimAndOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_transitions_queue_and_action_with_bounded_lease(self) -> None:
        action = _ready_action()
        action.status = "queued"
        attempt = _queued_attempt(action)
        session = _FakeSession(actions=[action], attempts=[attempt])
        claimed = await claim_action_execution_attempt(
            session,
            business_id=BUSINESS_ID,
            attempt_id=attempt.id,
            lease_seconds=60,
        )
        self.assertIs(claimed, attempt)
        self.assertEqual(attempt.status, "dispatching")
        self.assertEqual(action.status, "executing")
        self.assertEqual(action.execution_started_at, attempt.dispatch_started_at)
        self.assertEqual(
            attempt.lease_expires_at - attempt.lease_acquired_at,
            timedelta(seconds=60),
        )
        self.assertEqual(session.commit_calls, 0)
        self.assertEqual(session.external_calls, 0)

    async def test_invalid_claim_state_and_lease_are_denied(self) -> None:
        action = _ready_action()
        action.status = "queued"
        attempt = _queued_attempt(action)
        for lease in (0, 4, 901, True):
            with self.subTest(lease=lease):
                with self.assertRaises(ActionExecutionAttemptValidationError):
                    await claim_action_execution_attempt(
                        _FakeSession(actions=[action], attempts=[attempt]),
                        business_id=BUSINESS_ID,
                        attempt_id=attempt.id,
                        lease_seconds=lease,
                    )

        attempt.status = "failed"
        with self.assertRaises(ActionExecutionAttemptStateError):
            await claim_action_execution_attempt(
                _FakeSession(actions=[action], attempts=[attempt]),
                business_id=BUSINESS_ID,
                attempt_id=attempt.id,
                lease_seconds=60,
            )

    async def test_claim_is_tenant_scoped(self) -> None:
        action = _ready_action()
        action.status = "queued"
        attempt = _queued_attempt(action)
        with self.assertRaises(ActionExecutionAttemptNotFoundError):
            await claim_action_execution_attempt(
                _FakeSession(actions=[action], attempts=[attempt]),
                business_id=OTHER_BUSINESS_ID,
                attempt_id=attempt.id,
                lease_seconds=60,
            )

    async def test_success_completes_attempt_and_action(self) -> None:
        action, attempt, session = _dispatching_fixture()
        result = await record_action_execution_success(
            session,
            business_id=BUSINESS_ID,
            attempt_id=attempt.id,
            external_reference_id=" message-123 ",
        )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.external_reference_id, "message-123")
        self.assertEqual(action.status, "succeeded")
        self.assertEqual(action.external_reference_id, "message-123")
        self.assertIsNotNone(result.completed_at)
        self.assertEqual(action.execution_completed_at, result.completed_at)

        repeated = await record_action_execution_success(
            session,
            business_id=BUSINESS_ID,
            attempt_id=attempt.id,
            external_reference_id="message-123",
        )
        self.assertIs(repeated, attempt)

    async def test_success_preserves_safe_whatsapp_message_reference(self) -> None:
        action, attempt, session = _dispatching_fixture()
        reference = "wamid.HBgMNTU1MjM0NTY3ODkwFQIAERgSQUJDREVGRw=="
        result = await record_action_execution_success(
            session,
            business_id=BUSINESS_ID,
            attempt_id=attempt.id,
            external_reference_id=reference,
        )
        self.assertEqual(result.external_reference_id, reference)
        self.assertEqual(action.external_reference_id, reference)

    async def test_definite_failure_uses_only_safe_code(self) -> None:
        action, attempt, session = _dispatching_fixture()
        await record_action_execution_failure(
            session,
            business_id=BUSINESS_ID,
            attempt_id=attempt.id,
            failure_code="connector_rejected",
        )
        self.assertEqual(attempt.status, "failed")
        self.assertEqual(action.status, "failed")
        self.assertEqual(action.failure_code, "connector_rejected")
        self.assertIsNone(attempt.external_reference_id)

        repeated = await record_action_execution_failure(
            session,
            business_id=BUSINESS_ID,
            attempt_id=attempt.id,
            failure_code="connector_rejected",
        )
        self.assertIs(repeated, attempt)

        for unsafe in ("raw provider error!", "Authorization: Bearer secret"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ActionExecutionAttemptValidationError):
                    await record_action_execution_failure(
                        session,
                        business_id=BUSINESS_ID,
                        attempt_id=attempt.id,
                        failure_code=unsafe,
                    )

        with self.assertRaises(ActionExecutionAttemptValidationError):
            await record_action_execution_failure(
                session,
                business_id=BUSINESS_ID,
                attempt_id=attempt.id,
                failure_code="plausible_but_unregistered_code",
            )

    async def test_success_rejects_non_reference_text(self) -> None:
        _, attempt, session = _dispatching_fixture()
        with self.assertRaises(ActionExecutionAttemptValidationError):
            await record_action_execution_success(
                session,
                business_id=BUSINESS_ID,
                attempt_id=attempt.id,
                external_reference_id="raw provider response body",
            )

    async def test_uncertain_outcome_blocks_retry_and_stores_fixed_code(self) -> None:
        action, attempt, session = _dispatching_fixture()
        await record_action_execution_uncertain(
            session,
            business_id=BUSINESS_ID,
            attempt_id=attempt.id,
        )
        self.assertEqual(attempt.status, "uncertain")
        self.assertEqual(attempt.failure_code, "external_outcome_uncertain")
        self.assertEqual(action.status, "uncertain")
        self.assertEqual(action.failure_code, "external_outcome_uncertain")
        self.assertIsNone(attempt.external_reference_id)
        with self.assertRaises(ActionExecutionOutcomeUncertainError):
            await record_action_execution_success(
                session,
                business_id=BUSINESS_ID,
                attempt_id=attempt.id,
            )

        repeated = await record_action_execution_uncertain(
            session,
            business_id=BUSINESS_ID,
            attempt_id=attempt.id,
        )
        self.assertIs(repeated, attempt)

    async def test_only_queued_attempt_can_be_canceled(self) -> None:
        action = _ready_action()
        action.status = "queued"
        attempt = _queued_attempt(action)
        session = _FakeSession(actions=[action], attempts=[attempt])
        await cancel_action_execution_attempt(
            session,
            business_id=BUSINESS_ID,
            attempt_id=attempt.id,
        )
        self.assertEqual(attempt.status, "canceled")
        self.assertEqual(action.status, "canceled")

        action2, attempt2, session2 = _dispatching_fixture()
        with self.assertRaises(ActionExecutionAttemptStateError):
            await cancel_action_execution_attempt(
                session2,
                business_id=BUSINESS_ID,
                attempt_id=attempt2.id,
            )


class RecoveryAndIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_and_stale_attempt_queries_are_tenant_scoped(self) -> None:
        now = datetime.now(UTC)
        queued_action = _ready_action()
        queued_action.status = "queued"
        queued = _queued_attempt(queued_action)

        stale_action, stale, _ = _dispatching_fixture()
        stale.lease_expires_at = now - timedelta(seconds=1)

        fresh_action, fresh, _ = _dispatching_fixture()
        fresh.lease_expires_at = now + timedelta(minutes=1)

        other_action = _ready_action(business_id=OTHER_BUSINESS_ID)
        other_action.status = "queued"
        other = _queued_attempt(other_action)

        session = _FakeSession(
            actions=[queued_action, stale_action, fresh_action, other_action],
            attempts=[queued, stale, fresh, other],
        )
        queued_result = await list_queued_action_execution_attempts(
            session,
            business_id=BUSINESS_ID,
        )
        stale_result = await list_stale_dispatching_action_execution_attempts(
            session,
            business_id=BUSINESS_ID,
            now=now,
        )
        self.assertEqual(queued_result, [queued])
        self.assertEqual(stale_result, [stale])

    async def test_stale_dispatch_becomes_uncertain_never_queued(self) -> None:
        action, attempt, session = _dispatching_fixture()
        now = datetime.now(UTC)
        attempt.lease_expires_at = now - timedelta(seconds=1)
        await mark_stale_action_execution_attempt_uncertain(
            session,
            business_id=BUSINESS_ID,
            attempt_id=attempt.id,
            now=now,
        )
        self.assertEqual(attempt.status, "uncertain")
        self.assertEqual(attempt.failure_code, "dispatch_lease_expired")
        self.assertEqual(action.status, "uncertain")
        self.assertEqual(session.external_calls, 0)

    async def test_unexpired_dispatch_cannot_be_recovered(self) -> None:
        _, attempt, session = _dispatching_fixture()
        with self.assertRaises(ActionExecutionAttemptStateError):
            await mark_stale_action_execution_attempt_uncertain(
                session,
                business_id=BUSINESS_ID,
                attempt_id=attempt.id,
                now=datetime.now(UTC),
            )

    def test_idempotency_key_is_deterministic_unique_and_bounded(self) -> None:
        action_id = uuid4()
        first = build_action_attempt_idempotency_key(
            action_id=action_id,
            attempt_number=1,
        )
        repeated = build_action_attempt_idempotency_key(
            action_id=action_id,
            attempt_number=1,
        )
        second = build_action_attempt_idempotency_key(
            action_id=action_id,
            attempt_number=2,
        )
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 200)


class _ScalarCollection:
    def __init__(self, values: list[ActionExecutionAttempt]) -> None:
        self.values = values

    def all(self) -> list[ActionExecutionAttempt]:
        return self.values


class _FakeSession:
    def __init__(
        self,
        *,
        actions: list[AIAction] | None = None,
        attempts: list[ActionExecutionAttempt] | None = None,
        approvals: list[ApprovalRequest] | None = None,
        business_currency: str = "USD",
        scalar_error: SQLAlchemyError | None = None,
    ) -> None:
        self.actions = list(actions or [])
        self.attempts = list(attempts or [])
        self.approvals = list(approvals or [])
        self.business_currency = business_currency
        self.scalar_error = scalar_error
        self.flush_calls = 0
        self.commit_calls = 0
        self.external_calls = 0

    def add(self, value: ActionExecutionAttempt) -> None:
        if value.id is None:
            value.id = uuid4()
        self.attempts.append(value)

    async def flush(self) -> None:
        self.flush_calls += 1

    async def refresh(self, value, *, attribute_names) -> None:
        now = datetime.now(UTC)
        if "created_at" in attribute_names and value.created_at is None:
            value.created_at = now
        if "updated_at" in attribute_names:
            value.updated_at = now

    async def scalar(self, statement):
        if self.scalar_error is not None:
            raise self.scalar_error
        description = statement.column_descriptions[0]
        entity = description.get("entity")
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
            action_id = _param(params, "action_id_")
            reason = _param(params, "reason_code_")
            return next(
                (
                    item.id
                    for item in self.approvals
                    if item.business_id == business_id
                    and item.action_id == action_id
                    and item.status == "approved"
                    and item.reason_code == reason
                ),
                None,
            )

        if entity is Business:
            return self.business_currency if business_id == BUSINESS_ID else None

        if entity is ActionExecutionAttempt:
            action_id = _param(params, "action_id_")
            if description.get("name") == "max":
                numbers = [
                    item.attempt_number
                    for item in self.attempts
                    if item.business_id == business_id and item.action_id == action_id
                ]
                return max(numbers) if numbers else None

            attempt_id = _param(params, "id_")
            if attempt_id is not None:
                return next(
                    (
                        item
                        for item in self.attempts
                        if item.id == attempt_id and item.business_id == business_id
                    ),
                    None,
                )
            if action_id is not None:
                active = next(
                    (
                        item
                        for item in self.attempts
                        if item.business_id == business_id
                        and item.action_id == action_id
                        and item.status in {"queued", "dispatching"}
                    ),
                    None,
                )
                return active.id if active is not None else None
        return None

    async def scalars(self, statement) -> _ScalarCollection:
        params = statement.compile().params
        business_id = _param(params, "business_id_")
        status = _param(params, "status_")
        lease_cutoff = _param(params, "lease_expires_at_")
        values = [
            item
            for item in self.attempts
            if item.business_id == business_id
            and (status is None or item.status == status)
            and (
                lease_cutoff is None
                or (
                    item.lease_expires_at is not None
                    and item.lease_expires_at <= lease_cutoff
                )
            )
        ]
        values.sort(key=lambda item: (item.queued_at, str(item.id)))
        return _ScalarCollection(values)


def _param(params: dict[str, object], prefix: str):
    return next((value for key, value in params.items() if key.startswith(prefix)), None)


def _ready_action(
    *,
    requires_approval: bool = False,
    business_id: UUID = BUSINESS_ID,
) -> AIAction:
    action_type = "send_email" if requires_approval else "update_crm"
    payload = (
        {
            "recipient_ref": "customer-1",
            "subject": "Hello",
            "body": "Hello",
        }
        if requires_approval
        else {"customer_ref": "lead-1", "stage": "qualified"}
    )
    action = AIAction(
        id=uuid4(),
        business_id=business_id,
        execution_id=uuid4(),
        proposal_index=0,
        action_type=action_type,
        description="Execute a governed action.",
        risk_level="medium" if requires_approval else "low",
        proposed_requires_approval=requires_approval,
        status="ready",
        action_payload=payload,
        policy_decision="require_approval" if requires_approval else "allow",
        policy_reason_code=(
            "external_communication" if requires_approval else "policy_allow"
        ),
        policy_evaluated_at=datetime.now(UTC),
        execution_started_at=None,
        execution_completed_at=None,
        result_summary=None,
        failure_code=None,
        external_reference_id=None,
    )
    action.authorized_payload_hash = canonical_action_payload_hash(
        ACTION_REGISTRY.validate_payload(action.action_type, action.action_payload)
    )
    return action


def _approved_request(action: AIAction) -> ApprovalRequest:
    now = datetime.now(UTC)
    return ApprovalRequest(
        id=uuid4(),
        business_id=action.business_id,
        action_id=action.id,
        requested_by_user_id=USER_ID,
        status="approved",
        reason_code=action.policy_reason_code,
        requested_at=now - timedelta(minutes=1),
        expires_at=None,
        decided_at=now,
        decided_by_user_id=USER_ID,
        decision_actor_id=USER_ID,
        decision_note=None,
        created_at=now,
        updated_at=now,
    )


def _queued_attempt(
    action: AIAction,
    *,
    attempt_number: int = 1,
) -> ActionExecutionAttempt:
    now = datetime.now(UTC)
    capability = (
        "communications.email.send"
        if action.action_type == "send_email"
        else "crm.customer.update"
    )
    return ActionExecutionAttempt(
        id=uuid4(),
        business_id=action.business_id,
        action_id=action.id,
        attempt_number=attempt_number,
        idempotency_key=(
            f"ai-action:{action.id}:attempt:{attempt_number}"
        ),
        action_type=action.action_type,
        capability=capability,
        status="queued",
        queued_at=now,
        dispatch_started_at=None,
        completed_at=None,
        lease_acquired_at=None,
        lease_expires_at=None,
        external_reference_id=None,
        failure_code=None,
        created_at=now,
        updated_at=now,
    )


def _dispatching_fixture(
) -> tuple[AIAction, ActionExecutionAttempt, _FakeSession]:
    action = _ready_action()
    action.status = "executing"
    started = datetime.now(UTC)
    action.execution_started_at = started
    attempt = _queued_attempt(action)
    attempt.status = "dispatching"
    attempt.dispatch_started_at = started
    attempt.lease_acquired_at = started
    attempt.lease_expires_at = started + timedelta(minutes=1)
    return action, attempt, _FakeSession(actions=[action], attempts=[attempt])
