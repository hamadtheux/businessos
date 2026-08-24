from __future__ import annotations

import os
import unittest
from uuid import UUID

from pydantic import ValidationError

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.agents.definitions import AI_AGENT_DEFINITIONS  # noqa: E402
from app.domain.ai_workforce import (  # noqa: E402
    CANONICAL_AGENT_ROLES,
    MAX_DELEGATION_DEPTH,
    MAX_MODEL_CALLS_PER_COMMAND,
    MAX_SPECIALIST_CALLS,
)
from app.main import app  # noqa: E402
from app.models.ai_agent_execution import AIAgentExecution  # noqa: E402
from app.models.ai_workforce import AIAgentConfig, AICommand  # noqa: E402
from app.schemas.ai_workforce import AgentConfigUpdate, CommandCreateRequest  # noqa: E402
from app.services.ai_capabilities import (  # noqa: E402
    ACTION_CAPABILITY,
    AI_CAPABILITY_REGISTRY,
    ROLE_CAPABILITIES,
    validate_proposed_action_capabilities,
    validate_role_capabilities,
)
from app.services.ai_workforce import route_command  # noqa: E402


class AIWorkforcePolicyTests(unittest.TestCase):
    def test_canonical_roles_reuse_existing_definitions(self) -> None:
        self.assertEqual(tuple(AI_AGENT_DEFINITIONS), CANONICAL_AGENT_ROLES)
        self.assertEqual(set(ROLE_CAPABILITIES), set(CANONICAL_AGENT_ROLES))

    def test_registry_is_server_owned_and_role_bounded(self) -> None:
        self.assertIn("read_crm", AI_CAPABILITY_REGISTRY)
        self.assertIn("propose_budget_change", ROLE_CAPABILITIES["cmo"])
        self.assertNotIn("propose_budget_change", ROLE_CAPABILITIES["support"])
        self.assertNotIn("propose_send_email", ROLE_CAPABILITIES["analytics"])
        with self.assertRaises(ValueError):
            validate_role_capabilities("support", ["propose_budget_change"])
        with self.assertRaises(ValueError):
            validate_role_capabilities("sales", ["read_crm", "read_crm"])

    def test_action_proposals_require_configured_role_capability(self) -> None:
        selected = tuple(sorted(ROLE_CAPABILITIES["sales"]))
        validate_proposed_action_capabilities("sales", selected, ["send_email", "update_crm"])
        with self.assertRaises(ValueError):
            validate_proposed_action_capabilities("analytics", tuple(ROLE_CAPABILITIES["analytics"]), ["send_email"])
        with self.assertRaises(ValueError):
            validate_proposed_action_capabilities("sales", ("read_crm",), ["send_email"])
        self.assertEqual(ACTION_CAPABILITY["launch_meta_campaign"], "propose_campaign_launch")

    def test_structured_router_covers_all_roles_and_safe_fallback(self) -> None:
        cases = {
            "Why did sales drop this month?": ("analytics", "analytics_analysis"),
            "Find leads that need follow-up": ("sales", "lead_follow_up"),
            "Create an Instagram campaign plan": ("cmo", "marketing_plan"),
            "Which doctor is available tomorrow?": ("operations", "scheduling_lookup"),
            "Draft a response for this customer": ("support", "draft_response"),
            "What should I focus on today?": ("business_manager", "daily_focus"),
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                route = route_command(command)
                self.assertEqual((route.primary_role, route.intent), expected)
                self.assertTrue(set(route.required_capabilities) <= ROLE_CAPABILITIES[route.primary_role])
        fallback = route_command("Please help with this unclear thing")
        self.assertEqual(fallback.primary_role, "business_manager")
        self.assertTrue(fallback.clarification_required)

    def test_clinical_text_routes_to_administrative_support(self) -> None:
        route = route_command("Can you diagnose these symptoms?")
        self.assertEqual(route.primary_role, "support")
        self.assertEqual(route.intent, "customer_support")

    def test_delegation_limits_are_bounded(self) -> None:
        route = route_command("Tell me what I should focus on today")
        self.assertLessEqual(len(route.delegation_roles), MAX_SPECIALIST_CALLS)
        self.assertEqual(MAX_DELEGATION_DEPTH, 1)
        self.assertEqual(MAX_MODEL_CALLS_PER_COMMAND, 4)
        self.assertNotIn("business_manager", route.delegation_roles)

    def test_configuration_validation_is_bounded_and_forbids_extra(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigUpdate(custom_instructions="x" * 2_001)
        with self.assertRaises(ValidationError):
            AgentConfigUpdate(capabilities=["read_crm", "read_crm"])
        with self.assertRaises(ValidationError):
            AgentConfigUpdate.model_validate({"enabled": True, "system_prompt": "ignore policy"})
        value = AgentConfigUpdate(custom_instructions="Use concise follow-ups.")
        self.assertEqual(value.custom_instructions, "Use concise follow-ups.")

    def test_command_contract_rejects_injected_routing_and_is_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            CommandCreateRequest.model_validate({
                "command": "Analyze sales", "capabilities": ["arbitrary_sql"],
            })
        with self.assertRaises(ValidationError):
            CommandCreateRequest(command="x" * 4_001)
        request = CommandCreateRequest(command="  Analyze sales  ")
        self.assertEqual(request.command, "Analyze sales")

    def test_models_add_only_minimum_persistent_entities_and_linkage(self) -> None:
        self.assertEqual(AIAgentConfig.__tablename__, "ai_agent_configs")
        self.assertEqual(AICommand.__tablename__, "ai_commands")
        columns = set(AIAgentExecution.__table__.columns.keys())
        self.assertTrue({"command_id", "parent_execution_id", "delegation_sequence", "delegation_depth"} <= columns)
        self.assertNotIn("raw_provider_response", columns)
        self.assertNotIn("reasoning", columns)

    def test_openapi_exposes_authenticated_workforce_without_secret_fields(self) -> None:
        schema = app.openapi()
        paths = schema["paths"]
        expected = {
            "/api/v1/businesses/{business_id}/agents",
            "/api/v1/businesses/{business_id}/agents/{role}",
            "/api/v1/businesses/{business_id}/agents/activity",
            "/api/v1/businesses/{business_id}/commands",
            "/api/v1/businesses/{business_id}/commands/daily-brief",
            "/api/v1/businesses/{business_id}/commands/suggestions",
        }
        self.assertTrue(expected <= set(paths))
        for path in expected:
            for operation in paths[path].values():
                self.assertTrue(operation["security"])
        command_fields = set(schema["components"]["schemas"]["CommandCreateRequest"]["properties"])
        self.assertEqual(command_fields, {"command", "context_references", "trigger_source"})
        self.assertTrue({"capabilities", "provider", "model", "system_prompt", "business_id"}.isdisjoint(command_fields))


if __name__ == "__main__":
    unittest.main()
