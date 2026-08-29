from __future__ import annotations

import os
import unittest
from uuid import UUID, uuid4

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.ai_action import AIActionNotFoundError  # noqa: E402
from app.models.ai_action import AIAction  # noqa: E402
from app.services.action_policy import evaluate_action_policy  # noqa: E402
from app.services.automation_copilot import (  # noqa: E402
    _proposed_actions,
    _required_integrations,
    _stop_conditions,
    _trigger_type,
)


BUSINESS_ID = UUID("8a000000-0000-4000-8000-000000000001")
OTHER_BUSINESS_ID = UUID("8a000000-0000-4000-8000-000000000002")


class Phase8AProductScenarioTests(unittest.TestCase):
    def test_order_pack_prepares_governed_email_without_claiming_dispatch(self) -> None:
        prompt = (
            "when an order is created, prepare a confirmation email for governed "
            "review and stop after the message decision is recorded"
        )

        self.assertEqual(_trigger_type(prompt), "order_created")
        self.assertEqual(_required_integrations(prompt), ["gmail_or_outlook"])
        actions = _proposed_actions(prompt)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action_type"], "send_email")
        self.assertEqual(
            actions[0]["execution_state"],
            "withheld_pending_authoritative_inputs",
        )
        self.assertIn("stop after the goal event is recorded", _stop_conditions(prompt))

    def test_lead_pack_prepares_external_work_that_policy_forces_to_approval(self) -> None:
        prompt = "when a lead is created, wait 1 day, then prepare a customer email"
        self.assertEqual(_trigger_type(prompt), "lead_created")
        proposal = _proposed_actions(prompt)[0]
        action = _action(
            proposal["action_type"],
            {
                "recipient_ref": "lead-record-1",
                "subject": "Follow-up",
                "body": "Prepared follow-up",
            },
            proposed_approval=False,
        )

        decision = evaluate_action_policy(action, business_id=BUSINESS_ID)

        self.assertEqual(decision.decision, "require_approval")
        self.assertEqual(decision.reason_code, "external_communication")

    def test_social_publish_preparation_is_approval_gated_and_tenant_bound(self) -> None:
        action = _action(
            "publish_social_post",
            {"platform": "instagram", "content": "Grounded launch draft"},
            proposed_approval=False,
        )

        decision = evaluate_action_policy(action, business_id=BUSINESS_ID)

        self.assertEqual(decision.decision, "require_approval")
        self.assertEqual(decision.reason_code, "external_publication")
        with self.assertRaises(AIActionNotFoundError):
            evaluate_action_policy(action, business_id=OTHER_BUSINESS_ID)

    def test_unavailable_pack_triggers_fail_closed_to_manual_planning(self) -> None:
        prompts = (
            "when shipment tracking changes notify the customer",
            "reactivate customers after ninety inactive days",
            "escalate urgent support risk",
            "recommend social content from a new opportunity",
        )

        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(_trigger_type(prompt), "manual_test")


def _action(
    action_type: str,
    payload: dict[str, object],
    *,
    proposed_approval: bool,
) -> AIAction:
    return AIAction(
        id=uuid4(),
        business_id=BUSINESS_ID,
        execution_id=uuid4(),
        proposal_index=0,
        action_type=action_type,
        description="Phase 8A prepared action.",
        risk_level="medium",
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
