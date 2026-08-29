from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from pydantic import ValidationError

from app.domain.automation import NODE_TYPES, TRIGGER_TYPES
from app.exceptions.automation import AutomationValidationError
from app.models.automation import AutomationEdge, AutomationNode
from app.schemas.automation import (
    BranchNodeConfig,
    ConditionExpression,
    ConditionNodeConfig,
    NODE_CONFIGURATION_ADAPTER,
)
from app.services.action_registry import ACTION_REGISTRY


CONDITION_FIELDS = frozenset({
    "customer.status", "customer.tags", "customer.source",
    "lead.stage", "lead.priority", "lead.qualification_state", "lead.estimated_value",
    "order.status", "order.total",
    "appointment.provider_id", "appointment.appointment_type_id", "appointment.status",
    "campaign.status", "campaign.objective",
    "conversation.channel", "conversation.status",
    "opportunity.category", "opportunity.priority", "opportunity.status",
    "event.type", "event.entity_type", "event.entity_id",
})
NUMERIC_FIELDS = frozenset({"lead.estimated_value", "order.total"})
DATE_OPERATORS = frozenset({"date_before", "date_after"})
COMPARISON_OPERATORS = frozenset({"gt", "gte", "lt", "lte"})


def validate_node_configuration(node_type: str, raw: object, *, workflow_trigger_type: str | None = None) -> dict[str, Any]:
    if node_type not in NODE_TYPES or not isinstance(raw, dict):
        raise AutomationValidationError("node_configuration_invalid")
    try:
        parsed = NODE_CONFIGURATION_ADAPTER.validate_python(raw)
    except ValidationError:
        raise AutomationValidationError("node_configuration_invalid") from None
    if parsed.kind != node_type:
        raise AutomationValidationError("node_configuration_invalid")
    if parsed.kind == "trigger":
        if parsed.trigger_type not in TRIGGER_TYPES or (
            workflow_trigger_type is not None and parsed.trigger_type != workflow_trigger_type
        ):
            raise AutomationValidationError("node_configuration_invalid")
    elif parsed.kind in {"condition", "branch"}:
        validate_condition(parsed.condition)
    elif parsed.kind == "action":
        validation_payload = dict(parsed.payload)
        for target in parsed.context_bindings:
            if target in validation_payload:
                raise AutomationValidationError("node_configuration_invalid")
            validation_payload[target] = "00000000-0000-0000-0000-000000000000"
        try:
            ACTION_REGISTRY.validate_payload(parsed.action_type, validation_payload)
        except Exception:
            raise AutomationValidationError("node_configuration_invalid") from None
    return parsed.model_dump(mode="json")


def validate_condition(condition: ConditionExpression) -> None:
    if condition.field not in CONDITION_FIELDS:
        raise AutomationValidationError("condition_field_not_allowed")
    if condition.operator in COMPARISON_OPERATORS and condition.field not in NUMERIC_FIELDS:
        raise AutomationValidationError("condition_operator_invalid")
    if condition.operator in DATE_OPERATORS and condition.field in NUMERIC_FIELDS:
        raise AutomationValidationError("condition_operator_invalid")
    if condition.operator == "contains" and not isinstance(condition.value, (str, list)):
        raise AutomationValidationError("condition_operator_invalid")


def evaluate_condition(condition: ConditionExpression, payload: dict[str, Any]) -> bool:
    validate_condition(condition)
    actual = _safe_lookup(payload, condition.field)
    expected = condition.value
    if condition.operator == "equals":
        return actual == expected
    if condition.operator == "not_equals":
        return actual != expected
    if condition.operator == "contains":
        return isinstance(actual, (str, list, tuple, set)) and expected in actual
    if condition.operator in COMPARISON_OPERATORS:
        try:
            left, right = Decimal(str(actual)), Decimal(str(expected))
        except (InvalidOperation, TypeError, ValueError):
            raise AutomationValidationError("condition_input_missing") from None
        return {
            "gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right,
        }[condition.operator]
    try:
        left_date = datetime.fromisoformat(str(actual).replace("Z", "+00:00"))
        right_date = datetime.fromisoformat(str(expected).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise AutomationValidationError("condition_input_missing") from None
    return left_date < right_date if condition.operator == "date_before" else left_date > right_date


def validate_graph(nodes: Iterable[AutomationNode], edges: Iterable[AutomationEdge]) -> list[str]:
    node_list, edge_list = list(nodes), list(edges)
    errors: list[str] = []
    keys = {node.node_key for node in node_list}
    if len(keys) != len(node_list):
        errors.append("duplicate_node_key")
    triggers = [node for node in node_list if node.node_type == "trigger"]
    if len(triggers) != 1:
        errors.append("exactly_one_trigger_required")
    outgoing: dict[object, list[AutomationEdge]] = {key: [] for key in keys}
    incoming: dict[object, list[AutomationEdge]] = {key: [] for key in keys}
    seen_routes: set[tuple[object, str | None]] = set()
    for edge in edge_list:
        if edge.source_node_key not in keys or edge.target_node_key not in keys:
            errors.append("dangling_edge")
            continue
        route = (edge.source_node_key, edge.branch_label)
        if route in seen_routes:
            errors.append("ambiguous_edge")
        seen_routes.add(route)
        outgoing[edge.source_node_key].append(edge)
        incoming[edge.target_node_key].append(edge)
    for node in node_list:
        try:
            config = validate_node_configuration(node.node_type, node.configuration)
        except AutomationValidationError as exc:
            errors.append(str(exc))
            continue
        node_out = outgoing[node.node_key]
        if node.node_type == "trigger" and incoming[node.node_key]:
            errors.append("trigger_has_incoming_edge")
        if node.node_type != "trigger" and not incoming[node.node_key]:
            errors.append("node_has_no_incoming_edge")
        if node.node_type == "end" and node_out:
            errors.append("end_has_outgoing_edge")
        elif node.node_type != "end" and not node_out:
            errors.append("non_terminal_has_no_outgoing_edge")
        if node.node_type in {"trigger", "action", "delay", "approval", "ai", "internal_operation"} and len(node_out) != 1:
            errors.append("node_requires_one_continuation")
        if node.node_type in {"condition", "branch"}:
            labels = {edge.branch_label for edge in node_out}
            parsed = NODE_CONFIGURATION_ADAPTER.validate_python(config)
            expected = {"true", "false"}
            if isinstance(parsed, BranchNodeConfig):
                expected = {parsed.true_label, parsed.false_label}
            if len(node_out) != 2 or labels != expected:
                errors.append("branch_requires_true_false_edges")
    if triggers:
        visited: set[object] = set()
        stack = [triggers[0].node_key]
        while stack:
            key = stack.pop()
            if key in visited:
                continue
            visited.add(key)
            stack.extend(edge.target_node_key for edge in outgoing.get(key, []))
        if visited != keys:
            errors.append("unreachable_node")
    if _has_cycle(keys, outgoing):
        errors.append("cycle_not_allowed")
    if not any(node.node_type == "end" for node in node_list):
        errors.append("terminal_path_required")
    return list(dict.fromkeys(errors))


def next_node_key(node: AutomationNode, edges: list[AutomationEdge], *, outcome: str | None = None):
    candidates = sorted(
        (edge for edge in edges if edge.source_node_key == node.node_key),
        key=lambda edge: (edge.order_index, str(edge.id)),
    )
    if node.node_type in {"condition", "branch"}:
        for edge in candidates:
            if edge.branch_label == outcome:
                return edge.target_node_key
        raise AutomationValidationError("graph_invalid")
    if len(candidates) != 1:
        raise AutomationValidationError("graph_invalid")
    return candidates[0].target_node_key


def parse_condition(node: AutomationNode) -> ConditionExpression:
    parsed = NODE_CONFIGURATION_ADAPTER.validate_python(node.configuration)
    if not isinstance(parsed, (ConditionNodeConfig, BranchNodeConfig)):
        raise AutomationValidationError("node_configuration_invalid")
    return parsed.condition


def _safe_lookup(payload: dict[str, Any], field: str) -> Any:
    current: Any = payload
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            raise AutomationValidationError("condition_input_missing")
        current = current[part]
    return current


def _has_cycle(keys: set[object], outgoing: dict[object, list[AutomationEdge]]) -> bool:
    state: dict[object, int] = {key: 0 for key in keys}

    def visit(key: object) -> bool:
        if state[key] == 1:
            return True
        if state[key] == 2:
            return False
        state[key] = 1
        for edge in outgoing.get(key, []):
            if visit(edge.target_node_key):
                return True
        state[key] = 2
        return False

    return any(visit(key) for key in keys if state[key] == 0)
