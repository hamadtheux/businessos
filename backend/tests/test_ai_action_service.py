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
os.environ.setdefault(
    "AIBOS_AUTH_SECRET_KEY",
    "x" * 32,
)

from app.exceptions.ai_action import (  # noqa: E402
    AIActionConflictError,
    AIActionNotFoundError,
    AIActionPersistenceError,
    AIActionStateError,
    AIActionValidationError,
)
from app.models.ai_action import AIAction  # noqa: E402
from app.models.ai_agent_execution import AIAgentExecution  # noqa: E402
from app.services.ai_action import (  # noqa: E402
    get_ai_action,
    list_execution_ai_actions,
    materialize_ai_actions,
)


BUSINESS_A_ID = UUID(
    "a1000000-0000-0000-0000-000000000001"
)

BUSINESS_B_ID = UUID(
    "a2000000-0000-0000-0000-000000000002"
)

USER_ID = UUID(
    "a3000000-0000-0000-0000-000000000003"
)


class MaterializeAIActionTests(
    unittest.IsolatedAsyncioTestCase,
):
    async def test_materializes_valid_proposals_without_executing_them(
        self,
    ) -> None:
        execution = _execution(
            proposed_actions=[
                _proposal(
                    action_type="send_customer_message",
                    description="Send customer follow-up.",
                    risk_level="medium",
                    requires_approval=True,
                ),
                _proposal(
                    action_type="update_crm",
                    description="Update lead qualification.",
                    risk_level="low",
                    requires_approval=False,
                ),
            ],
        )

        session = _FakeSession(
            execution=execution,
        )

        actions = await materialize_ai_actions(
            session,
            business_id=BUSINESS_A_ID,
            execution_id=execution.id,
        )

        self.assertEqual(
            len(actions),
            2,
        )

        first = actions[0]
        second = actions[1]

        self.assertEqual(
            first.business_id,
            BUSINESS_A_ID,
        )

        self.assertEqual(
            first.execution_id,
            execution.id,
        )

        self.assertEqual(
            first.proposal_index,
            0,
        )

        self.assertEqual(
            first.action_type,
            "send_customer_message",
        )

        self.assertEqual(
            first.description,
            "Send customer follow-up.",
        )

        self.assertEqual(
            first.risk_level,
            "medium",
        )

        self.assertTrue(
            first.proposed_requires_approval,
        )

        self.assertEqual(
            second.proposal_index,
            1,
        )

        self.assertEqual(
            second.action_type,
            "update_crm",
        )

        self.assertFalse(
            second.proposed_requires_approval,
        )

        for action in actions:
            self.assertEqual(
                action.status,
                "proposed",
            )

            self.assertEqual(
                action.action_payload,
                {},
            )

            self.assertIsNone(
                action.policy_decision,
            )

            self.assertIsNone(
                action.policy_evaluated_at,
            )

            self.assertIsNone(
                action.execution_started_at,
            )

            self.assertIsNone(
                action.execution_completed_at,
            )

            self.assertIsNone(
                action.failure_code,
            )

            self.assertIsNone(
                action.external_reference_id,
            )

        self.assertEqual(
            session.add_all_calls,
            1,
        )

        self.assertEqual(
            session.flush_calls,
            1,
        )

        self.assertEqual(
            session.commit_calls,
            0,
        )

    async def test_materialization_is_idempotent(
        self,
    ) -> None:
        execution = _execution(
            proposed_actions=[
                _proposal(),
            ],
        )

        existing = _action_from_proposal(
            execution,
            proposal_index=0,
            proposal=execution.proposed_actions[0],
        )

        existing.status = "pending_approval"
        existing.policy_decision = "require_approval"
        existing.policy_reason_code = (
            "human_approval_required"
        )
        existing.policy_evaluated_at = datetime.now(
            UTC
        )

        session = _FakeSession(
            execution=execution,
            actions=[
                existing,
            ],
        )

        actions = await materialize_ai_actions(
            session,
            business_id=BUSINESS_A_ID,
            execution_id=execution.id,
        )

        self.assertEqual(
            actions,
            [
                existing,
            ],
        )

        self.assertEqual(
            actions[0].status,
            "pending_approval",
        )

        self.assertEqual(
            session.add_all_calls,
            0,
        )

        self.assertEqual(
            session.flush_calls,
            0,
        )

    async def test_partial_existing_materialization_fails_closed(
        self,
    ) -> None:
        execution = _execution(
            proposed_actions=[
                _proposal(
                    action_type="send_email",
                    description="Send email.",
                ),
                _proposal(
                    action_type="publish_social_post",
                    description="Publish social post.",
                ),
            ],
        )

        existing = _action_from_proposal(
            execution,
            proposal_index=0,
            proposal=execution.proposed_actions[0],
        )

        session = _FakeSession(
            execution=execution,
            actions=[
                existing,
            ],
        )

        with self.assertRaises(
            AIActionConflictError,
        ):
            await materialize_ai_actions(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
            )

        self.assertEqual(
            session.add_all_calls,
            0,
        )

    async def test_existing_action_identity_mismatch_fails_closed(
        self,
    ) -> None:
        execution = _execution(
            proposed_actions=[
                _proposal(
                    action_type="send_email",
                    description="Send approved email.",
                ),
            ],
        )

        existing = _action_from_proposal(
            execution,
            proposal_index=0,
            proposal=execution.proposed_actions[0],
        )

        existing.action_type = (
            "launch_google_ads_campaign"
        )

        session = _FakeSession(
            execution=execution,
            actions=[
                existing,
            ],
        )

        with self.assertRaises(
            AIActionConflictError,
        ):
            await materialize_ai_actions(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
            )

    async def test_execution_from_other_business_is_not_found(
        self,
    ) -> None:
        execution = _execution(
            business_id=BUSINESS_A_ID,
            proposed_actions=[
                _proposal(),
            ],
        )

        session = _FakeSession(
            execution=execution,
        )

        with self.assertRaises(
            AIActionNotFoundError,
        ):
            await materialize_ai_actions(
                session,
                business_id=BUSINESS_B_ID,
                execution_id=execution.id,
            )

        self.assertEqual(
            session.add_all_calls,
            0,
        )

    async def test_running_execution_cannot_materialize_actions(
        self,
    ) -> None:
        execution = _execution(
            status="running",
            completed_at=None,
            proposed_actions=[
                _proposal(),
            ],
        )

        session = _FakeSession(
            execution=execution,
        )

        with self.assertRaises(
            AIActionStateError,
        ):
            await materialize_ai_actions(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
            )

    async def test_terminal_execution_requires_completed_at(
        self,
    ) -> None:
        execution = _execution(
            status="needs_approval",
            completed_at=None,
            proposed_actions=[
                _proposal(),
            ],
        )

        session = _FakeSession(
            execution=execution,
        )

        with self.assertRaises(
            AIActionStateError,
        ):
            await materialize_ai_actions(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
            )

    async def test_empty_proposals_create_no_rows(
        self,
    ) -> None:
        execution = _execution(
            proposed_actions=[],
        )

        session = _FakeSession(
            execution=execution,
        )

        result = await materialize_ai_actions(
            session,
            business_id=BUSINESS_A_ID,
            execution_id=execution.id,
        )

        self.assertEqual(
            result,
            [],
        )

        self.assertEqual(
            session.add_all_calls,
            0,
        )

        self.assertEqual(
            session.flush_calls,
            0,
        )

    async def test_invalid_persisted_proposal_is_rejected(
        self,
    ) -> None:
        execution = _execution(
            proposed_actions=[
                {
                    "action_type": "",
                    "description": "Invalid action.",
                    "risk_level": "medium",
                    "requires_approval": True,
                },
            ],
        )

        session = _FakeSession(
            execution=execution,
        )

        with self.assertRaises(
            AIActionValidationError,
        ):
            await materialize_ai_actions(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
            )

        self.assertEqual(
            session.add_all_calls,
            0,
        )

    async def test_materialization_persists_only_normalized_typed_payload(
        self,
    ) -> None:
        execution = _execution(
            status="completed",
            proposed_actions=[
                _proposal(
                    action_type="update_crm",
                    risk_level="low",
                    requires_approval=False,
                    action_payload={
                        "customer_ref": " lead-1 ",
                        "stage": "qualified",
                    },
                )
            ],
        )
        session = _FakeSession(execution=execution)
        actions = await materialize_ai_actions(
            session,
            business_id=BUSINESS_A_ID,
            execution_id=execution.id,
        )
        self.assertEqual(
            actions[0].action_payload,
            {
                "customer_ref": "lead-1",
                "stage": "qualified",
                "owner_ref": None,
                "note": None,
                "next_follow_up_at": None,
            },
        )

    async def test_malformed_candidate_payload_is_rejected_before_action_storage(
        self,
    ) -> None:
        execution = _execution(
            proposed_actions=[
                _proposal(
                    action_payload={
                        "customer_ref": "customer-1",
                        "message": "Hello",
                        "connector_options": {"raw": "untrusted"},
                    }
                )
            ],
        )
        session = _FakeSession(execution=execution)
        with self.assertRaises(AIActionValidationError):
            await materialize_ai_actions(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
            )
        self.assertEqual(session.actions, [])

    async def test_critical_proposal_cannot_bypass_approval(
        self,
    ) -> None:
        execution = _execution(
            proposed_actions=[
                {
                    "action_type": "change_ad_budget",
                    "description": "Increase campaign budget.",
                    "risk_level": "critical",
                    "requires_approval": False,
                },
            ],
        )

        session = _FakeSession(
            execution=execution,
        )

        with self.assertRaises(
            AIActionValidationError,
        ):
            await materialize_ai_actions(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
            )

    async def test_database_failure_is_sanitized(
        self,
    ) -> None:
        execution = _execution(
            proposed_actions=[
                _proposal(),
            ],
        )

        session = _FakeSession(
            execution=execution,
            flush_error=SQLAlchemyError(
                "private database detail"
            ),
        )

        with self.assertRaises(
            AIActionPersistenceError,
        ) as raised:
            await materialize_ai_actions(
                session,
                business_id=BUSINESS_A_ID,
                execution_id=execution.id,
            )

        self.assertNotIn(
            "private database detail",
            str(raised.exception),
        )


class ReadAIActionTests(
    unittest.IsolatedAsyncioTestCase,
):
    async def test_get_action_is_tenant_scoped(
        self,
    ) -> None:
        execution = _execution(
            proposed_actions=[
                _proposal(),
            ],
        )

        action = _action_from_proposal(
            execution,
            proposal_index=0,
            proposal=execution.proposed_actions[0],
        )

        session = _FakeSession(
            execution=execution,
            actions=[
                action,
            ],
        )

        result = await get_ai_action(
            session,
            business_id=BUSINESS_A_ID,
            action_id=action.id,
        )

        self.assertIs(
            result,
            action,
        )

    async def test_cross_tenant_get_returns_not_found(
        self,
    ) -> None:
        execution = _execution(
            proposed_actions=[
                _proposal(),
            ],
        )

        action = _action_from_proposal(
            execution,
            proposal_index=0,
            proposal=execution.proposed_actions[0],
        )

        session = _FakeSession(
            execution=execution,
            actions=[
                action,
            ],
        )

        with self.assertRaises(
            AIActionNotFoundError,
        ):
            await get_ai_action(
                session,
                business_id=BUSINESS_B_ID,
                action_id=action.id,
            )

    async def test_list_actions_preserves_proposal_order(
        self,
    ) -> None:
        execution = _execution(
            proposed_actions=[
                _proposal(
                    action_type="send_email",
                    description="Send email.",
                ),
                _proposal(
                    action_type="publish_social_post",
                    description="Publish social post.",
                ),
            ],
        )

        second = _action_from_proposal(
            execution,
            proposal_index=1,
            proposal=execution.proposed_actions[1],
        )

        first = _action_from_proposal(
            execution,
            proposal_index=0,
            proposal=execution.proposed_actions[0],
        )

        session = _FakeSession(
            execution=execution,
            actions=[
                second,
                first,
            ],
        )

        result = await list_execution_ai_actions(
            session,
            business_id=BUSINESS_A_ID,
            execution_id=execution.id,
        )

        self.assertEqual(
            [
                action.proposal_index
                for action in result
            ],
            [
                0,
                1,
            ],
        )

    async def test_read_database_failure_is_sanitized(
        self,
    ) -> None:
        session = _FakeSession(
            scalar_error=SQLAlchemyError(
                "secret database failure"
            ),
        )

        with self.assertRaises(
            AIActionPersistenceError,
        ) as raised:
            await get_ai_action(
                session,
                business_id=BUSINESS_A_ID,
                action_id=uuid4(),
            )

        self.assertNotIn(
            "secret database failure",
            str(raised.exception),
        )


class _ScalarCollection:
    def __init__(
        self,
        values: list[AIAction],
    ) -> None:
        self._values = values

    def all(
        self,
    ) -> list[AIAction]:
        return self._values


class _FakeSession:
    def __init__(
        self,
        *,
        execution: AIAgentExecution | None = None,
        actions: list[AIAction] | None = None,
        flush_error: SQLAlchemyError | None = None,
        scalar_error: SQLAlchemyError | None = None,
        scalars_error: SQLAlchemyError | None = None,
    ) -> None:
        self.execution = execution
        self.actions = list(
            actions or []
        )

        self.flush_error = flush_error
        self.scalar_error = scalar_error
        self.scalars_error = scalars_error

        self.add_all_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0

    def add_all(
        self,
        values,
    ) -> None:
        self.add_all_calls += 1

        for value in values:
            if value.id is None:
                value.id = uuid4()

            self.actions.append(
                value
            )

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

        entity = (
            statement.column_descriptions[0]
            .get("entity")
        )

        params = statement.compile().params

        if entity is AIAgentExecution:
            execution_id = _first_param(
                params,
                "id_",
            )

            business_id = _first_param(
                params,
                "business_id_",
            )

            if self.execution is None:
                return None

            if self.execution.id != execution_id:
                return None

            if (
                self.execution.business_id
                != business_id
            ):
                return None

            return self.execution

        if entity is AIAction:
            action_id = _first_param(
                params,
                "id_",
            )

            business_id = _first_param(
                params,
                "business_id_",
            )

            for action in self.actions:
                if (
                    action.id == action_id
                    and action.business_id
                    == business_id
                ):
                    return action

            return None

        return None

    async def scalars(
        self,
        statement,
    ) -> _ScalarCollection:
        if self.scalars_error is not None:
            raise self.scalars_error

        params = statement.compile().params

        business_id = _first_param(
            params,
            "business_id_",
        )

        execution_id = _first_param(
            params,
            "execution_id_",
        )

        values = [
            action
            for action in self.actions
            if (
                action.business_id
                == business_id
                and action.execution_id
                == execution_id
            )
        ]

        values.sort(
            key=lambda action: (
                action.proposal_index,
                str(action.id),
            )
        )

        return _ScalarCollection(
            values
        )


def _first_param(
    params: dict[str, object],
    prefix: str,
):
    for key, value in params.items():
        if key.startswith(
            prefix
        ):
            return value

    return None


def _proposal(
    *,
    action_type: str = "send_customer_message",
    description: str = "Send customer follow-up.",
    risk_level: str = "medium",
    requires_approval: bool = True,
    action_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    proposal: dict[str, object] = {
        "action_type": action_type,
        "description": description,
        "risk_level": risk_level,
        "requires_approval": requires_approval,
    }
    if action_payload is not None:
        proposal["action_payload"] = dict(action_payload)
    return proposal


_UNSET_COMPLETED_AT = object()


def _execution(
    *,
    business_id: UUID = BUSINESS_A_ID,
    status: str = "needs_approval",
    completed_at: datetime | None | object = _UNSET_COMPLETED_AT,
    proposed_actions: list[object] | None = None,
) -> AIAgentExecution:
    if completed_at is _UNSET_COMPLETED_AT:
        resolved_completed_at: datetime | None = (
            None
            if status == "running"
            else datetime.now(UTC)
        )
    else:
        resolved_completed_at = completed_at

    if (
        resolved_completed_at is not None
        and not isinstance(
            resolved_completed_at,
            datetime,
        )
    ):
        raise TypeError(
            "completed_at must be datetime or None"
        )

    return AIAgentExecution(
        id=uuid4(),
        business_id=business_id,
        requested_by_user_id=USER_ID,
        role="sales",
        trigger_type="api",
        status=status,
        task="Prepare next business action.",
        provider_name="openai",
        model_name="gpt-5.6-terra",
        context_revision="a" * 64,
        context_source_count=2,
        business_brain_source_count=1,
        memory_source_count=1,
        output_summary="Safe execution result.",
        recommendations=[],
        proposed_actions=list(
            proposed_actions or []
        ),
        failure_code=None,
        provider_request_id=None,
        duration_ms=100,
        input_tokens=None,
        output_tokens=None,
        estimated_cost_usd=None,
        completed_at=resolved_completed_at,
    )


def _action_from_proposal(
    execution: AIAgentExecution,
    *,
    proposal_index: int,
    proposal: object,
) -> AIAction:
    assert isinstance(
        proposal,
        dict,
    )

    return AIAction(
        id=uuid4(),
        business_id=execution.business_id,
        execution_id=execution.id,
        proposal_index=proposal_index,
        action_type=str(
            proposal["action_type"]
        ),
        description=str(
            proposal["description"]
        ),
        risk_level=str(
            proposal["risk_level"]
        ),
        proposed_requires_approval=bool(
            proposal["requires_approval"]
        ),
        status="proposed",
        action_payload={},
        policy_decision=None,
        policy_reason_code=None,
        policy_evaluated_at=None,
        execution_started_at=None,
        execution_completed_at=None,
        result_summary=None,
        failure_code=None,
        external_reference_id=None,
    )
