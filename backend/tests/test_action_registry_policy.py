from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import ValidationError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.ai_action import (  # noqa: E402
    AIActionNotFoundError,
    AIActionValidationError,
    UnsupportedAIActionError,
)
from app.models.ai_action import AIAction  # noqa: E402
from app.schemas.ai_agent import AIAgentProposedAction  # noqa: E402
from app.services.action_policy import evaluate_action_policy  # noqa: E402
from app.services.action_registry import ACTION_REGISTRY  # noqa: E402


BUSINESS_ID = UUID("b1000000-0000-0000-0000-000000000001")
OTHER_BUSINESS_ID = UUID("b2000000-0000-0000-0000-000000000002")

EXPECTED_ACTION_TYPES = {
    "send_email",
    "send_whatsapp_message",
    "send_customer_message",
    "publish_social_post",
    "create_meta_campaign",
    "launch_meta_campaign",
    "create_google_ads_campaign",
    "launch_google_ads_campaign",
    "change_ad_budget",
    "pause_ad_campaign",
    "update_crm",
    "create_order",
}


class ActionRegistryTests(unittest.TestCase):
    def test_registry_contains_exact_initial_actions(self) -> None:
        self.assertEqual(ACTION_REGISTRY.action_types, EXPECTED_ACTION_TYPES)

    def test_unknown_action_fails_closed(self) -> None:
        with self.assertRaises(UnsupportedAIActionError):
            ACTION_REGISTRY.require("run_arbitrary_connector")

    def test_definition_and_registry_views_are_immutable(self) -> None:
        definition = ACTION_REGISTRY.require("send_email")
        with self.assertRaises(FrozenInstanceError):
            definition.default_risk_level = "low"  # type: ignore[misc]
        self.assertIsInstance(ACTION_REGISTRY.action_types, frozenset)

    def test_valid_payload_is_typed_and_normalized(self) -> None:
        payload = ACTION_REGISTRY.validate_payload(
            "send_email",
            {
                "recipient_ref": " customer-17 ",
                "subject": " Renewal ",
                "body": " Hello ",
            },
        )
        self.assertEqual(payload.recipient_ref, "customer-17")
        self.assertEqual(payload.subject, "Renewal")
        self.assertEqual(payload.body, "Hello")

    def test_extra_and_blank_payload_fields_are_rejected(self) -> None:
        cases = (
            {
                "recipient_ref": "customer-17",
                "subject": "Subject",
                "body": "Body",
                "api_key": "secret",
            },
            {
                "recipient_ref": "   ",
                "subject": "Subject",
                "body": "Body",
            },
        )
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(AIActionValidationError):
                    ACTION_REGISTRY.validate_payload("send_email", payload)

    def test_money_uses_decimal_and_enforces_bounds(self) -> None:
        valid = ACTION_REGISTRY.validate_payload(
            "change_ad_budget",
            {
                "campaign_ref": "campaign-1",
                "budget": "125.50",
                "currency": " usd ",
                "budget_period": "daily",
            },
        )
        self.assertEqual(valid.budget, Decimal("125.50"))
        self.assertEqual(valid.currency, "USD")

        for amount in ("-0.01", "1000000.01"):
            with self.subTest(amount=amount):
                with self.assertRaises(AIActionValidationError):
                    ACTION_REGISTRY.validate_payload(
                        "change_ad_budget",
                        {
                            "campaign_ref": "campaign-1",
                            "budget": amount,
                            "currency": "USD",
                            "budget_period": "daily",
                        },
                    )

    def test_invalid_social_platform_is_rejected(self) -> None:
        with self.assertRaises(AIActionValidationError):
            ACTION_REGISTRY.validate_payload(
                "publish_social_post",
                {"platform": "myspace", "content": "Hello"},
            )

    def test_candidate_payload_rejects_nested_credentials_before_ledger(self) -> None:
        with self.assertRaises(ValidationError):
            AIAgentProposedAction(
                action_type="send_email",
                description="Send email.",
                action_payload={
                    "recipient_ref": "customer-1",
                    "metadata": {"authorization": "Bearer secret"},
                },
            )

    def test_campaign_payload_has_bounded_typed_audience(self) -> None:
        payload = ACTION_REGISTRY.validate_payload(
            "create_google_ads_campaign",
            {
                "campaign_name": "Autumn sale",
                "objective": "sales",
                "budget": "1000.00",
                "currency": "usd",
                "budget_period": "lifetime",
                "network": "search",
                "audience": {
                    "countries": ["pk"],
                    "min_age": 21,
                    "max_age": 50,
                    "languages": ["en"],
                },
                "creative": {"creative_refs": ["creative-1"]},
            },
        )
        self.assertEqual(payload.audience.countries, ["PK"])

    def test_url_credentials_and_model_provided_order_prices_are_rejected(self) -> None:
        with self.assertRaises(AIActionValidationError):
            ACTION_REGISTRY.validate_payload(
                "create_meta_campaign",
                {
                    "campaign_name": "Autumn sale",
                    "objective": "sales",
                    "budget": "100.00",
                    "currency": "USD",
                    "budget_period": "daily",
                    "audience": {"countries": ["US"]},
                    "creative": {
                        "creative_refs": ["creative-1"],
                        "destination_url": "https://user:secret@example.com/",
                    },
                },
            )

        with self.assertRaises(AIActionValidationError):
            ACTION_REGISTRY.validate_payload(
                "create_order",
                {
                    "customer_ref": "customer-1",
                    "line_items": [
                        {
                            "catalog_item_ref": "item-1",
                            "quantity": 1,
                            "unit_price": "0.01",
                        }
                    ],
                },
            )


class PolicyEngineTests(unittest.TestCase):
    def test_low_risk_safe_action_is_allowed(self) -> None:
        result = evaluate_action_policy(
            _action(
                "update_crm",
                {"customer_ref": "lead-1", "stage": "qualified"},
                risk="low",
                proposed_approval=False,
            ),
            business_id=BUSINESS_ID,
        )
        self.assertEqual((result.decision, result.reason_code), ("allow", "policy_allow"))

    def test_critical_proposal_cannot_bypass_approval(self) -> None:
        result = evaluate_action_policy(
            _action(
                "update_crm",
                {"customer_ref": "lead-1", "stage": "qualified"},
                risk="critical",
                proposed_approval=False,
            ),
            business_id=BUSINESS_ID,
        )
        self.assertEqual((result.decision, result.reason_code), ("require_approval", "critical_action"))

    def test_external_message_requires_approval(self) -> None:
        result = evaluate_action_policy(
            _action(
                "send_customer_message",
                {"customer_ref": "customer-1", "message": "Hello"},
                proposed_approval=False,
            ),
            business_id=BUSINESS_ID,
        )
        self.assertEqual(result.reason_code, "external_communication")

    def test_social_publication_requires_approval(self) -> None:
        result = evaluate_action_policy(
            _action(
                "publish_social_post",
                {"platform": "linkedin", "content": "Hello"},
                proposed_approval=False,
            ),
            business_id=BUSINESS_ID,
        )
        self.assertEqual(result.reason_code, "external_publication")

    def test_campaign_launch_requires_approval(self) -> None:
        result = evaluate_action_policy(
            _action(
                "launch_meta_campaign",
                {"campaign_ref": "campaign-1"},
                proposed_approval=False,
            ),
            business_id=BUSINESS_ID,
        )
        self.assertEqual(result.reason_code, "campaign_launch")

    def test_budget_change_requires_approval(self) -> None:
        result = evaluate_action_policy(
            _action(
                "change_ad_budget",
                {
                    "campaign_ref": "campaign-1",
                    "budget": "200.00",
                    "currency": "USD",
                    "budget_period": "daily",
                },
                proposed_approval=False,
            ),
            business_id=BUSINESS_ID,
        )
        self.assertEqual(result.reason_code, "ad_spend_change")

    def test_destructive_action_requires_approval(self) -> None:
        result = evaluate_action_policy(
            _action(
                "pause_ad_campaign",
                {"campaign_ref": "campaign-1"},
                proposed_approval=False,
            ),
            business_id=BUSINESS_ID,
        )
        self.assertEqual(result.reason_code, "destructive_action")

    def test_unknown_and_invalid_payloads_are_blocked(self) -> None:
        cases = (
            (_action("unknown", {}), "unsupported_action"),
            (_action("send_email", {}), "invalid_action_payload"),
        )
        for action, reason in cases:
            with self.subTest(reason=reason):
                result = evaluate_action_policy(action, business_id=BUSINESS_ID)
                self.assertEqual((result.decision, result.reason_code), ("block", reason))

    def test_repeated_evaluation_is_deterministic(self) -> None:
        action = _action(
            "send_email",
            {
                "recipient_ref": "customer-1",
                "subject": "Hello",
                "body": "Hello",
            },
        )
        first = evaluate_action_policy(action, business_id=BUSINESS_ID)
        second = evaluate_action_policy(action, business_id=BUSINESS_ID)
        self.assertEqual(first, second)

    def test_cross_tenant_evaluation_fails_as_not_found(self) -> None:
        with self.assertRaises(AIActionNotFoundError):
            evaluate_action_policy(
                _action("update_crm", {"customer_ref": "c", "note": "n"}),
                business_id=OTHER_BUSINESS_ID,
            )


def _action(
    action_type: str,
    payload: dict[str, object],
    *,
    risk: str = "medium",
    proposed_approval: bool = True,
) -> AIAction:
    return AIAction(
        id=uuid4(),
        business_id=BUSINESS_ID,
        execution_id=uuid4(),
        proposal_index=0,
        action_type=action_type,
        description="Test action.",
        risk_level=risk,
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
