from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.automation import AutomationValidationError  # noqa: E402
from app.models.approval_request import ApprovalRequest  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.automation import (  # noqa: E402
    AutomationEdge,
    AutomationEvent,
    AutomationNode,
    AutomationNodeRun,
    AutomationWorkflow,
    AutomationWorkflowRun,
    AutomationWorkflowVersion,
)
from app.schemas.automation import (  # noqa: E402
    ExternalActionNodeConfig,
    ScheduleDefinition,
    SimulationRequest,
)
from app.services import automation as automation_service  # noqa: E402
from app.services.automation import (  # noqa: E402
    _audit_run,
    _node_run_response,
    _workflow_run_response,
    simulate_workflow,
)
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
    def test_setup_required_copilot_placeholder_cannot_be_activated(self) -> None:
        placeholder = _node(
            "approval",
            {
                "kind": "approval",
                "reason_code": "external_communication_setup_required",
                "expires_in_seconds": None,
            },
            "Provider setup required",
        )

        self.assertEqual(
            automation_service._activation_setup_errors([placeholder]),
            ["workflow_setup_required"],
        )

    def test_terminal_workflow_run_audit_links_to_the_run(self) -> None:
        session = SimpleNamespace(added=[])
        session.add = session.added.append
        run = SimpleNamespace(
            id=uuid4(), business_id=BUSINESS_ID, status="succeeded"
        )

        _audit_run(
            session,
            run,
            None,
            "automation.workflow_run_succeeded",
            "Workflow run completed successfully.",
        )

        audit = session.added[0]
        self.assertIsInstance(audit, AuditLog)
        self.assertEqual(audit.entity_type, "automation_workflow_run")
        self.assertEqual(audit.entity_id, run.id)
        self.assertEqual(audit.after_value, "status=succeeded")

    def test_workflow_run_history_excludes_internal_idempotency_key(self) -> None:
        run = SimpleNamespace(
            id=uuid4(), business_id=BUSINESS_ID, workflow_id=WORKFLOW_ID,
            workflow_version_id=VERSION_ID, trigger_event_id=None,
            trigger_type="event", status="succeeded", context_payload={},
            current_node_key=uuid4(), waiting_reason=None, started_at="started",
            completed_at="completed", failure_code=None, requested_by_user_id=None,
            created_at="created", updated_at="updated", idempotency_key="internal",
        )

        value = _workflow_run_response(run, workflow_name="Lead triage", version=1)

        self.assertEqual(value["workflow_name"], "Lead triage")
        self.assertNotIn("idempotency_key", value)

    def test_node_run_history_exposes_only_the_public_response_contract(self) -> None:
        item = SimpleNamespace(
            id=uuid4(), business_id=BUSINESS_ID, workflow_version_id=VERSION_ID,
            workflow_run_id=uuid4(), node_key=uuid4(), status="succeeded",
            attempt=1, started_at="started", completed_at="completed",
            branch_outcome=None, result_summary="Internal operation completed.",
            failure_code=None, resume_at=None, action_id=None,
            created_at="created", updated_at="updated",
        )

        value = _node_run_response(item, name="Notify owner", node_type="internal_operation")

        self.assertEqual(value["node_name"], "Notify owner")
        self.assertEqual(value["node_type"], "internal_operation")
        self.assertTrue({"business_id", "workflow_version_id", "created_at", "updated_at"}.isdisjoint(value))

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

    def test_external_action_can_bind_recipient_from_trusted_event_customer(self) -> None:
        action = validate_node_configuration(
            "action",
            {
                "kind": "action",
                "action_type": "send_email",
                "description": "Prepare an order confirmation for the event customer.",
                "payload": {
                    "subject": "Order received",
                    "body": "We received your order.",
                },
                "context_bindings": {
                    "recipient_ref": "event_customer_ref",
                },
                "risk_level": "medium",
                "requires_approval": True,
            },
        )

        self.assertEqual(
            action["context_bindings"],
            {"recipient_ref": "event_customer_ref"},
        )
        self.assertNotIn("recipient_ref", action["payload"])

    def test_action_context_bindings_enforce_identity_semantics(self) -> None:
        valid = ExternalActionNodeConfig(
            action_type="send_whatsapp_message",
            description="Reply inside a trusted customer conversation.",
            payload={"message": "Thanks for your message."},
            context_bindings={
                "customer_ref": "event_customer_ref",
                "conversation_ref": "event_conversation_ref",
            },
        )
        self.assertEqual(
            valid.context_bindings["conversation_ref"],
            "event_conversation_ref",
        )

        for bindings in (
            {"recipient_ref": "event_conversation_ref"},
            {"customer_ref": "event_conversation_ref"},
            {"conversation_ref": "event_customer_ref"},
        ):
            with self.subTest(bindings=bindings), self.assertRaisesRegex(
                ValidationError,
                "action_context_binding_semantic_mismatch",
            ):
                ExternalActionNodeConfig(
                    action_type="send_customer_message",
                    description="Invalid binding.",
                    payload={"message": "Hello"},
                    context_bindings=bindings,
                )

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


class AutomationRuntimeBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_customer_ref_resolves_order_customer_inside_same_tenant(self) -> None:
        order_id = uuid4()
        customer_id = uuid4()

        session = SimpleNamespace()
        session.scalar = AsyncMock(
            side_effect=[
                SimpleNamespace(
                    id=order_id,
                    business_id=BUSINESS_ID,
                    customer_id=customer_id,
                ),
                SimpleNamespace(
                    id=customer_id,
                    business_id=BUSINESS_ID,
                    status="active",
                ),
            ]
        )

        resolved = await automation_service._resolve_event_customer_ref(
            session,
            business_id=BUSINESS_ID,
            payload={
                "event": {
                    "type": "order_created",
                    "entity_type": "order",
                    "entity_id": str(order_id),
                },
                "order": {
                    "status": "confirmed",
                },
            },
        )

        self.assertEqual(resolved, str(customer_id))
        self.assertEqual(session.scalar.await_count, 2)

    async def test_action_payload_resolves_event_customer_binding_before_governance(self) -> None:
        customer_id = uuid4()
        config = ExternalActionNodeConfig(
            action_type="send_email",
            description="Prepare an order confirmation.",
            payload={
                "subject": "Order received",
                "body": "We received your order.",
            },
            context_bindings={
                "recipient_ref": "event_customer_ref",
            },
            risk_level="medium",
            requires_approval=True,
        )
        run = SimpleNamespace(
            business_id=BUSINESS_ID,
            context_payload={
                "event": {
                    "type": "order_created",
                    "entity_type": "order",
                    "entity_id": str(uuid4()),
                }
            },
        )

        with patch(
            "app.services.automation._resolve_event_customer_ref",
            new=AsyncMock(return_value=str(customer_id)),
        ):
            resolved = await automation_service._resolve_action_payload(
                SimpleNamespace(),
                run=run,
                config=config,
            )

        self.assertEqual(resolved["recipient_ref"], str(customer_id))
        self.assertEqual(resolved["subject"], "Order received")
        self.assertEqual(resolved["body"], "We received your order.")
        self.assertNotIn("recipient_ref", config.payload)


    async def test_governed_action_intent_uses_runtime_resolved_payload(self) -> None:
        customer_id = uuid4()
        run = SimpleNamespace(
            id=uuid4(),
            business_id=BUSINESS_ID,
            requested_by_user_id=None,
            context_payload={
                "event": {
                    "type": "order_created",
                    "entity_type": "order",
                    "entity_id": str(uuid4()),
                }
            },
        )
        config = ExternalActionNodeConfig(
            action_type="send_email",
            description="Prepare an order confirmation.",
            payload={
                "subject": "Order received",
                "body": "We received your order.",
            },
            context_bindings={
                "recipient_ref": "event_customer_ref",
            },
            risk_level="medium",
            requires_approval=True,
        )

        added = []
        session = SimpleNamespace()
        session.add = added.append

        async def flush_with_identity(_session) -> None:
            if added and getattr(added[-1], "id", None) is None:
                added[-1].id = uuid4()

        governed_result = SimpleNamespace(
            action=SimpleNamespace(id=uuid4()),
            approval=SimpleNamespace(id=uuid4()),
        )

        with (
            patch(
                "app.services.automation._resolve_action_payload",
                new=AsyncMock(
                    return_value={
                        "recipient_ref": str(customer_id),
                        "subject": "Order received",
                        "body": "We received your order.",
                        "conversation_ref": None,
                        "reply_to_ref": None,
                        "thread_ref": None,
                    }
                ),
            ) as resolve_payload,
            patch(
                "app.services.automation._flush",
                new=AsyncMock(side_effect=flush_with_identity),
            ),
            patch(
                "app.services.automation.materialize_ai_actions",
                new=AsyncMock(return_value=[SimpleNamespace(id=uuid4())]),
            ) as materialize,
            patch(
                "app.services.automation.govern_materialized_ai_actions",
                new=AsyncMock(return_value=[governed_result]),
            ) as govern,
        ):
            result = await automation_service._create_governed_action_intent(
                session,
                run=run,
                config=config,
                actor_user_id=None,
                instant=__import__("datetime").datetime.now(
                    __import__("datetime").UTC
                ),
            )

        self.assertIs(result, governed_result)
        resolve_payload.assert_awaited_once()

        execution_id = materialize.await_args.kwargs["execution_id"]
        self.assertIsNotNone(execution_id)
        self.assertEqual(added[0].id, execution_id)
        self.assertEqual(
            added[0].proposed_actions[0]["action_payload"]["recipient_ref"],
            str(customer_id),
        )
        self.assertNotIn("recipient_ref", config.payload)
        govern.assert_awaited_once()

    async def test_event_customer_ref_resolves_lead_customer_inside_same_tenant(self) -> None:
        lead_id = uuid4()
        customer_id = uuid4()

        session = SimpleNamespace()
        session.scalar = AsyncMock(
            side_effect=[
                SimpleNamespace(
                    id=lead_id,
                    business_id=BUSINESS_ID,
                    customer_id=customer_id,
                ),
                SimpleNamespace(
                    id=customer_id,
                    business_id=BUSINESS_ID,
                    status="active",
                ),
            ]
        )

        resolved = await automation_service._resolve_event_customer_ref(
            session,
            business_id=BUSINESS_ID,
            payload={
                "event": {
                    "type": "lead_created",
                    "entity_type": "lead",
                    "entity_id": str(lead_id),
                },
                "lead": {
                    "stage": "new",
                },
            },
        )

        self.assertEqual(resolved, str(customer_id))
        self.assertEqual(session.scalar.await_count, 2)

    async def test_event_customer_ref_resolves_conversation_customer_inside_same_tenant(self) -> None:
        conversation_id = uuid4()
        customer_id = uuid4()

        session = SimpleNamespace()
        session.scalar = AsyncMock(
            side_effect=[
                SimpleNamespace(
                    id=conversation_id,
                    business_id=BUSINESS_ID,
                    customer_id=customer_id,
                ),
                SimpleNamespace(
                    id=customer_id,
                    business_id=BUSINESS_ID,
                    status="active",
                ),
            ]
        )

        resolved = await automation_service._resolve_event_customer_ref(
            session,
            business_id=BUSINESS_ID,
            payload={
                "event": {
                    "type": "inbound_message_recorded",
                    "entity_type": "conversation",
                    "entity_id": str(conversation_id),
                },
                "conversation": {
                    "channel": "email",
                },
            },
        )

        self.assertEqual(resolved, str(customer_id))
        self.assertEqual(session.scalar.await_count, 2)

    async def test_event_customer_ref_fails_when_order_is_not_accessible(self) -> None:
        session = SimpleNamespace()
        session.scalar = AsyncMock(return_value=None)

        with self.assertRaisesRegex(
            AutomationValidationError,
            "action_context_entity_not_found",
        ):
            await automation_service._resolve_event_customer_ref(
                session,
                business_id=BUSINESS_ID,
                payload={
                    "event": {
                        "type": "order_created",
                        "entity_type": "order",
                        "entity_id": str(uuid4()),
                    }
                },
            )

    async def test_event_customer_ref_fails_when_order_has_no_customer(self) -> None:
        order_id = uuid4()
        session = SimpleNamespace()
        session.scalar = AsyncMock(
            return_value=SimpleNamespace(
                id=order_id,
                business_id=BUSINESS_ID,
                customer_id=None,
            )
        )

        with self.assertRaisesRegex(
            AutomationValidationError,
            "action_context_customer_unavailable",
        ):
            await automation_service._resolve_event_customer_ref(
                session,
                business_id=BUSINESS_ID,
                payload={
                    "event": {
                        "type": "order_created",
                        "entity_type": "order",
                        "entity_id": str(order_id),
                    }
                },
            )

    async def test_event_customer_ref_fails_when_linked_customer_is_not_accessible(self) -> None:
        order_id = uuid4()
        customer_id = uuid4()
        session = SimpleNamespace()
        session.scalar = AsyncMock(
            side_effect=[
                SimpleNamespace(
                    id=order_id,
                    business_id=BUSINESS_ID,
                    customer_id=customer_id,
                ),
                None,
            ]
        )

        with self.assertRaisesRegex(
            AutomationValidationError,
            "action_context_customer_unavailable",
        ):
            await automation_service._resolve_event_customer_ref(
                session,
                business_id=BUSINESS_ID,
                payload={
                    "event": {
                        "type": "order_created",
                        "entity_type": "order",
                        "entity_id": str(order_id),
                    }
                },
            )

    async def test_event_conversation_ref_resolves_inside_same_tenant(self) -> None:
        conversation_id = uuid4()
        session = SimpleNamespace()
        session.scalar = AsyncMock(return_value=SimpleNamespace(
            id=conversation_id,
            business_id=BUSINESS_ID,
        ))

        value = await automation_service._resolve_event_conversation_ref(
            session,
            business_id=BUSINESS_ID,
            payload={
                "event": {
                    "type": "inbound_message_recorded",
                    "entity_type": "conversation",
                    "entity_id": str(conversation_id),
                }
            },
        )

        self.assertEqual(value, str(conversation_id))

    async def test_event_conversation_ref_requires_conversation_entity(self) -> None:
        with self.assertRaisesRegex(
            AutomationValidationError,
            "action_context_conversation_required",
        ):
            await automation_service._resolve_event_conversation_ref(
                SimpleNamespace(),
                business_id=BUSINESS_ID,
                payload={
                    "event": {
                        "type": "inbound_message_recorded",
                        "entity_type": "conversation_message",
                        "entity_id": str(uuid4()),
                    }
                },
            )

    async def test_event_conversation_ref_rejects_invalid_uuid(self) -> None:
        with self.assertRaisesRegex(
            AutomationValidationError,
            "action_context_conversation_invalid",
        ):
            await automation_service._resolve_event_conversation_ref(
                SimpleNamespace(),
                business_id=BUSINESS_ID,
                payload={
                    "event": {
                        "type": "inbound_message_recorded",
                        "entity_type": "conversation",
                        "entity_id": "not-a-uuid",
                    }
                },
            )

    async def test_event_conversation_ref_rejects_missing_conversation(self) -> None:
        session = SimpleNamespace()
        session.scalar = AsyncMock(return_value=None)

        with self.assertRaisesRegex(
            AutomationValidationError,
            "action_context_conversation_not_found",
        ):
            await automation_service._resolve_event_conversation_ref(
                session,
                business_id=BUSINESS_ID,
                payload={
                    "event": {
                        "type": "inbound_message_recorded",
                        "entity_type": "conversation",
                        "entity_id": str(uuid4()),
                    }
                },
            )

    async def test_event_conversation_ref_rejects_cross_tenant_result(self) -> None:
        session = SimpleNamespace()
        session.scalar = AsyncMock(return_value=SimpleNamespace(
            id=uuid4(),
            business_id=uuid4(),
        ))

        with self.assertRaisesRegex(
            AutomationValidationError,
            "action_context_conversation_not_found",
        ):
            await automation_service._resolve_event_conversation_ref(
                session,
                business_id=BUSINESS_ID,
                payload={
                    "event": {
                        "type": "inbound_message_recorded",
                        "entity_type": "conversation",
                        "entity_id": str(uuid4()),
                    }
                },
            )

    async def test_action_payload_resolves_trusted_conversation_binding(self) -> None:
        customer_id = uuid4()
        conversation_id = uuid4()
        config = ExternalActionNodeConfig(
            action_type="send_whatsapp_message",
            description="Reply within the trusted conversation.",
            payload={"message": "Thanks for your message."},
            context_bindings={
                "customer_ref": "event_customer_ref",
                "conversation_ref": "event_conversation_ref",
            },
        )
        run = SimpleNamespace(
            business_id=BUSINESS_ID,
            context_payload={
                "event": {
                    "type": "inbound_message_recorded",
                    "entity_type": "conversation",
                    "entity_id": str(conversation_id),
                }
            },
        )

        with (
            patch(
                "app.services.automation._resolve_event_customer_ref",
                new=AsyncMock(return_value=str(customer_id)),
            ),
            patch(
                "app.services.automation._resolve_event_conversation_ref",
                new=AsyncMock(return_value=str(conversation_id)),
            ),
        ):
            resolved = await automation_service._resolve_action_payload(
                SimpleNamespace(), run=run, config=config
            )

        self.assertEqual(resolved["customer_ref"], str(customer_id))
        self.assertEqual(resolved["conversation_ref"], str(conversation_id))


class AutomationActionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def _fixture(self, *, waiting_reason: str):
        action_node_key = uuid4()
        end_node_key = uuid4()
        action_id = uuid4()
        waiting = SimpleNamespace(
            id=uuid4(),
            action_id=action_id,
            status="waiting",
            completed_at=None,
            failure_code=None,
            result_summary="Governed action is waiting for approval.",
            resume_at=None,
        )
        run = SimpleNamespace(
            id=uuid4(),
            business_id=BUSINESS_ID,
            current_node_key=action_node_key,
            waiting_reason=waiting_reason,
            status="waiting",
            requested_by_user_id=None,
        )
        node = SimpleNamespace(node_key=action_node_key, node_type="action")
        end = SimpleNamespace(node_key=end_node_key, node_type="end")
        edge = SimpleNamespace(
            id=uuid4(),
            source_node_key=action_node_key,
            target_node_key=end_node_key,
            branch_label=None,
            order_index=0,
        )
        return run, waiting, action_id, {action_node_key: node, end_node_key: end}, [edge]

    async def test_approval_queues_dispatch_but_does_not_complete_action_node(self) -> None:
        run, waiting, action_id, node_map, edges = self._fixture(
            waiting_reason="approval"
        )
        session = SimpleNamespace()
        session.scalar = AsyncMock(side_effect=[
            waiting,
            SimpleNamespace(status="approved"),
            SimpleNamespace(id=action_id, business_id=BUSINESS_ID, status="queued"),
        ])

        ready = await automation_service._resume_waiting_node(
            session,
            run=run,
            node_map=node_map,
            edges=edges,
            instant=datetime.now(UTC),
        )

        self.assertFalse(ready)
        self.assertEqual(run.status, "waiting")
        self.assertEqual(run.waiting_reason, "action_execution")
        self.assertEqual(waiting.status, "waiting")
        self.assertIn("dispatch is queued", waiting.result_summary)

    async def test_provider_success_completes_action_node_and_resumes_graph(self) -> None:
        run, waiting, action_id, node_map, edges = self._fixture(
            waiting_reason="action_execution"
        )
        action = SimpleNamespace(
            id=action_id,
            business_id=BUSINESS_ID,
            status="succeeded",
            result_summary="Provider accepted the governed action.",
            failure_code=None,
        )
        session = SimpleNamespace()
        session.scalar = AsyncMock(side_effect=[waiting, action])

        ready = await automation_service._resume_waiting_node(
            session,
            run=run,
            node_map=node_map,
            edges=edges,
            instant=datetime.now(UTC),
        )

        self.assertTrue(ready)
        self.assertEqual(waiting.status, "succeeded")
        self.assertEqual(
            waiting.result_summary,
            "Provider accepted the governed action.",
        )
        self.assertNotEqual(run.current_node_key, next(iter(node_map)))
        self.assertEqual(run.waiting_reason, None)

    async def test_uncertain_provider_result_fails_workflow_truthfully(self) -> None:
        run, waiting, action_id, node_map, edges = self._fixture(
            waiting_reason="action_execution"
        )
        action = SimpleNamespace(
            id=action_id,
            business_id=BUSINESS_ID,
            status="uncertain",
            result_summary=(
                "The provider outcome is uncertain; reconciliation is required."
            ),
            failure_code="external_outcome_uncertain",
        )
        session = SimpleNamespace()
        session.scalar = AsyncMock(side_effect=[waiting, action])

        async def fail_run(_session, current_run, code, _actor):
            current_run.status = "failed"
            current_run.failure_code = code
            return current_run

        with patch(
            "app.services.automation._fail_run",
            new=AsyncMock(side_effect=fail_run),
        ):
            ready = await automation_service._resume_waiting_node(
                session,
                run=run,
                node_map=node_map,
                edges=edges,
                instant=datetime.now(UTC),
            )

        self.assertFalse(ready)
        self.assertEqual(waiting.status, "failed")
        self.assertEqual(waiting.failure_code, "action_dispatch_uncertain")
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.failure_code, "action_dispatch_uncertain")

    async def test_run_cannot_be_canceled_after_dispatch_is_queued(self) -> None:
        run = SimpleNamespace(
            id=uuid4(),
            business_id=BUSINESS_ID,
            status="waiting",
            waiting_reason="action_execution",
        )
        with patch(
            "app.services.automation.get_workflow_run",
            new=AsyncMock(return_value=run),
        ):
            with self.assertRaisesRegex(
                automation_service.AutomationStateError,
                "cannot be canceled after provider dispatch was queued",
            ):
                await automation_service.cancel_workflow_run(
                    SimpleNamespace(),
                    business_id=BUSINESS_ID,
                    run_id=run.id,
                    actor_user_id=uuid4(),
                )

    async def test_canceling_pending_action_review_cancels_action_and_approval(self) -> None:
        actor_id = uuid4()
        action_id = uuid4()
        run = SimpleNamespace(
            id=uuid4(),
            business_id=BUSINESS_ID,
            workflow_id=WORKFLOW_ID,
            status="waiting",
            waiting_reason="approval",
            completed_at=None,
        )
        waiting = SimpleNamespace(
            id=uuid4(),
            action_id=action_id,
            status="waiting",
            completed_at=None,
        )
        action = SimpleNamespace(
            id=action_id,
            business_id=BUSINESS_ID,
            status="pending_approval",
        )
        approval = SimpleNamespace(
            status="pending",
            decided_at=None,
            decided_by_user_id=None,
            decision_actor_id=None,
            decision_note=None,
        )
        workflow = SimpleNamespace(
            id=WORKFLOW_ID,
            business_id=BUSINESS_ID,
            status="active",
        )
        session = SimpleNamespace(added=[])
        session.add = session.added.append
        session.scalar = AsyncMock(side_effect=[waiting, action, approval])

        with (
            patch(
                "app.services.automation.get_workflow_run",
                new=AsyncMock(return_value=run),
            ),
            patch(
                "app.services.automation.get_workflow",
                new=AsyncMock(return_value=workflow),
            ),
            patch(
                "app.services.automation._flush",
                new=AsyncMock(),
            ),
        ):
            result = await automation_service.cancel_workflow_run(
                session,
                business_id=BUSINESS_ID,
                run_id=run.id,
                actor_user_id=actor_id,
            )

        self.assertIs(result, run)
        self.assertEqual(run.status, "canceled")
        self.assertEqual(waiting.status, "canceled")
        self.assertEqual(action.status, "canceled")
        self.assertEqual(approval.status, "canceled")
        self.assertEqual(approval.decision_actor_id, actor_id)


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
