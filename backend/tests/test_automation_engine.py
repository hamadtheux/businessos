from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.automation import AutomationValidationError  # noqa: E402
from app.models.approval_request import ApprovalRequest  # noqa: E402
from app.models.automation import (  # noqa: E402
    AutomationEdge,
    AutomationEvent,
    AutomationNode,
    AutomationNodeRun,
    AutomationWorkflow,
    AutomationWorkflowRun,
    AutomationWorkflowVersion,
)
from app.schemas.automation import ScheduleDefinition, SimulationRequest  # noqa: E402
from app.services.automation import simulate_workflow  # noqa: E402
from app.services.automation_graph import (  # noqa: E402
    evaluate_condition,
    validate_graph,
    validate_node_configuration,
)
from app.schemas.automation import ConditionExpression  # noqa: E402


BUSINESS_ID = UUID("81000000-0000-0000-0000-000000000001")
WORKFLOW_ID = UUID("82000000-0000-0000-0000-000000000002")
VERSION_ID = UUID("83000000-0000-0000-0000-000000000003")


class AutomationModelTests(unittest.TestCase):
    def test_all_durable_tables_are_registered_and_tenant_owned(self) -> None:
        expected = {
            AutomationWorkflow: "automation_workflows",
            AutomationWorkflowVersion: "automation_workflow_versions",
            AutomationNode: "automation_nodes",
            AutomationEdge: "automation_edges",
            AutomationEvent: "automation_events",
            AutomationWorkflowRun: "automation_workflow_runs",
            AutomationNodeRun: "automation_node_runs",
        }
        for model, table in expected.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(model.__tablename__, table)
                self.assertIn("business_id", model.__table__.columns)
                self.assertTrue(any(isinstance(value, CheckConstraint) for value in model.__table__.constraints))

    def test_graph_and_run_links_use_composite_tenant_foreign_keys(self) -> None:
        for model in (AutomationWorkflowVersion, AutomationNode, AutomationEdge, AutomationWorkflowRun, AutomationNodeRun):
            with self.subTest(model=model.__name__):
                self.assertTrue(any(isinstance(value, ForeignKeyConstraint) and len(value.column_keys) >= 2 for value in model.__table__.constraints))

    def test_versions_and_event_runs_have_idempotent_unique_keys(self) -> None:
        version_names = {value.name for value in AutomationWorkflowVersion.__table__.constraints if isinstance(value, UniqueConstraint)}
        run_names = {value.name for value in AutomationWorkflowRun.__table__.constraints if isinstance(value, UniqueConstraint)}
        self.assertIn("uq_automation_versions_workflow_version", version_names)
        self.assertIn("uq_automation_runs_version_event", run_names)
        self.assertIn("uq_automation_runs_business_idempotency", run_names)

    def test_existing_approval_queue_accepts_exactly_one_target(self) -> None:
        checks = {value.name for value in ApprovalRequest.__table__.constraints if isinstance(value, CheckConstraint)}
        self.assertIn("ck_approval_requests_exactly_one_target", checks)
        self.assertIn("workflow_node_run_id", ApprovalRequest.__table__.columns)

    def test_models_have_no_arbitrary_code_or_secret_columns(self) -> None:
        forbidden = {"code", "python", "javascript", "sql", "shell", "api_key", "access_token", "oauth_token", "authorization", "raw_response", "chain_of_thought"}
        for model in (AutomationWorkflow, AutomationWorkflowVersion, AutomationNode, AutomationEdge, AutomationEvent, AutomationWorkflowRun, AutomationNodeRun):
            with self.subTest(model=model.__name__):
                self.assertTrue(forbidden.isdisjoint(model.__table__.columns.keys()))


class AutomationSchemaAndGraphTests(unittest.TestCase):
    def test_structured_schedules_require_frequency_specific_fields(self) -> None:
        self.assertEqual(ScheduleDefinition(frequency="weekday", at_time="09:00").frequency, "weekday")
        self.assertEqual(ScheduleDefinition(frequency="weekly", at_time="09:00", weekday=0).weekday, 0)
        with self.assertRaises(ValidationError):
            ScheduleDefinition(frequency="weekly", at_time="09:00")
        with self.assertRaises(ValidationError):
            ScheduleDefinition(frequency="daily", at_time="25:00")

    def test_node_configuration_is_typed_and_action_registry_backed(self) -> None:
        action = validate_node_configuration("action", {
            "kind": "action", "action_type": "send_customer_message",
            "description": "Prepare a draft", "payload": {"customer_ref": "customer-1", "message": "Hello"},
            "risk_level": "medium", "requires_approval": True,
        })
        self.assertEqual(action["action_type"], "send_customer_message")
        for raw in (
            {"kind": "action", "action_type": "run_shell", "description": "No", "payload": {}},
            {"kind": "trigger", "trigger_type": "external_webhook"},
            {"kind": "ai", "role": "root", "task": "No"},
        ):
            with self.subTest(raw=raw), self.assertRaises(AutomationValidationError):
                validate_node_configuration(str(raw["kind"]), raw)

    def test_conditions_are_allowlisted_and_deterministic(self) -> None:
        condition = ConditionExpression(field="lead.estimated_value", operator="gt", value=5000)
        self.assertTrue(evaluate_condition(condition, {"lead": {"estimated_value": "7500.00"}}))
        self.assertFalse(evaluate_condition(condition, {"lead": {"estimated_value": 100}}))
        with self.assertRaises(AutomationValidationError):
            evaluate_condition(ConditionExpression(field="lead.secret", operator="equals", value="x"), {"lead": {"secret": "x"}})
        with self.assertRaises(AutomationValidationError):
            evaluate_condition(condition, {"lead": {}})

    def test_valid_simple_dag_passes(self) -> None:
        nodes, edges = _simple_graph()
        self.assertEqual(validate_graph(nodes, edges), [])

    def test_cycle_unreachable_and_single_trigger_rules_fail_closed(self) -> None:
        nodes, edges = _simple_graph()
        edges.append(_edge(nodes[1].node_key, nodes[0].node_key))
        errors = validate_graph(nodes, edges)
        self.assertIn("cycle_not_allowed", errors)
        self.assertIn("trigger_has_incoming_edge", errors)
        nodes.append(_node("end", {"kind": "end", "outcome": "success"}, "Orphan"))
        self.assertIn("unreachable_node", validate_graph(nodes, edges))
        nodes.append(_node("trigger", {"kind": "trigger", "trigger_type": "manual_test"}, "Second trigger"))
        self.assertIn("exactly_one_trigger_required", validate_graph(nodes, edges))

    def test_branch_requires_exact_named_outcomes(self) -> None:
        trigger = _node("trigger", {"kind": "trigger", "trigger_type": "manual_test"}, "Start")
        branch = _node("branch", {"kind": "branch", "condition": {"field": "lead.estimated_value", "operator": "gt", "value": 5000}, "true_label": "high", "false_label": "normal"}, "Value")
        high, normal = _node("end", {"kind": "end", "outcome": "success"}, "High"), _node("end", {"kind": "end", "outcome": "success"}, "Normal")
        valid = [_edge(trigger.node_key, branch.node_key), _edge(branch.node_key, high.node_key, "high"), _edge(branch.node_key, normal.node_key, "normal")]
        self.assertEqual(validate_graph([trigger, branch, high, normal], valid), [])
        valid[-1].branch_label = "other"
        self.assertIn("branch_requires_true_false_edges", validate_graph([trigger, branch, high, normal], valid))


class AutomationSimulationTests(unittest.IsolatedAsyncioTestCase):
    async def test_simulation_traces_branches_without_side_effects(self) -> None:
        trigger = _node("trigger", {"kind": "trigger", "trigger_type": "manual_test"}, "Start")
        branch = _node("branch", {"kind": "branch", "condition": {"field": "lead.estimated_value", "operator": "gt", "value": 5000}, "true_label": "true", "false_label": "false"}, "Value")
        action = _node("action", {"kind": "action", "action_type": "send_customer_message", "description": "Prepare a message", "payload": {"customer_ref": "customer-1", "message": "Hello"}, "risk_level": "medium", "requires_approval": True}, "Governed message")
        end = _node("end", {"kind": "end", "outcome": "success"}, "Complete")
        low = _node("end", {"kind": "end", "outcome": "success"}, "Low value")
        nodes = [trigger, branch, action, end, low]
        edges = [_edge(trigger.node_key, branch.node_key), _edge(branch.node_key, action.node_key, "true"), _edge(branch.node_key, low.node_key, "false"), _edge(action.node_key, end.node_key)]
        session = _NoWriteSession()
        with patch("app.services.automation.get_workflow", new=AsyncMock(return_value=SimpleNamespace(id=WORKFLOW_ID))), patch("app.services.automation.load_graph", new=AsyncMock(return_value=(SimpleNamespace(id=VERSION_ID), nodes, edges))):
            result = await simulate_workflow(session, business_id=BUSINESS_ID, workflow_id=WORKFLOW_ID, request=SimulationRequest(payload={"lead": {"estimated_value": 7500}}))
        self.assertTrue(result["completed"])
        self.assertEqual(result["trace"][1]["branch_outcome"], "true")
        self.assertEqual(result["planned_actions"][0]["dispatch"], False)
        self.assertEqual(len(result["approvals"]), 1)
        self.assertEqual(session.write_calls, 0)

    async def test_simulation_returns_planned_delay_and_forced_failure(self) -> None:
        trigger = _node("trigger", {"kind": "trigger", "trigger_type": "manual_test"}, "Start")
        delay = _node("delay", {"kind": "delay", "mode": "duration", "seconds": 600, "offset_seconds": 0}, "Wait")
        end = _node("end", {"kind": "end", "outcome": "success"}, "Complete")
        nodes, edges = [trigger, delay, end], [_edge(trigger.node_key, delay.node_key), _edge(delay.node_key, end.node_key)]
        with patch("app.services.automation.get_workflow", new=AsyncMock(return_value=SimpleNamespace(id=WORKFLOW_ID))), patch("app.services.automation.load_graph", new=AsyncMock(return_value=(SimpleNamespace(id=VERSION_ID), nodes, edges))):
            planned = await simulate_workflow(_NoWriteSession(), business_id=BUSINESS_ID, workflow_id=WORKFLOW_ID, request=SimulationRequest(payload={}))
            failed = await simulate_workflow(_NoWriteSession(), business_id=BUSINESS_ID, workflow_id=WORKFLOW_ID, request=SimulationRequest(payload={}, forced_failure_node_key=delay.node_key))
        self.assertTrue(planned["completed"])
        self.assertEqual(len(planned["delays"]), 1)
        self.assertEqual(failed["errors"], ["forced_simulation_failure"])


class _NoWriteSession:
    write_calls = 0

    def add(self, _value) -> None:
        self.write_calls += 1
        raise AssertionError("simulation attempted a write")


def _node(node_type: str, configuration: dict, name: str) -> AutomationNode:
    return AutomationNode(
        id=uuid4(), business_id=BUSINESS_ID, workflow_id=WORKFLOW_ID,
        workflow_version_id=VERSION_ID, node_key=uuid4(), node_type=node_type,
        name=name, configuration=configuration, position_x=0, position_y=0, order_index=0,
    )


def _edge(source, target, label=None) -> AutomationEdge:
    return AutomationEdge(
        id=uuid4(), business_id=BUSINESS_ID, workflow_id=WORKFLOW_ID,
        workflow_version_id=VERSION_ID, edge_key=uuid4(), source_node_key=source,
        target_node_key=target, branch_label=label, order_index=0,
    )


def _simple_graph() -> tuple[list[AutomationNode], list[AutomationEdge]]:
    trigger = _node("trigger", {"kind": "trigger", "trigger_type": "manual_test"}, "Start")
    end = _node("end", {"kind": "end", "outcome": "success"}, "Complete")
    return [trigger, end], [_edge(trigger.node_key, end.node_key)]
