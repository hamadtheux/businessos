from __future__ import annotations

import re
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.automation import AutomationValidationError
from app.models.automation import AutomationEdge, AutomationNode, AutomationWorkflowVersion
from app.schemas.automation import (
    AutomationCopilotCompileRequest,
    AutomationCopilotRefineRequest,
    NodeUpdate,
    WorkflowCreate,
    WorkflowUpdate,
)
from app.services.automation import (
    create_workflow,
    get_workflow,
    load_graph,
    update_node,
    update_workflow,
    workflow_detail,
)
from app.services.automation_graph import validate_graph, validate_node_configuration


async def compile_workflow(
    session: AsyncSession,
    *,
    business_id: UUID,
    actor_user_id: UUID,
    data: AutomationCopilotCompileRequest,
) -> dict[str, object]:
    prompt = " ".join(data.prompt.split())
    normalized = prompt.casefold()
    trigger_type = _trigger_type(normalized)
    wait_seconds = _wait_seconds(normalized)
    required_integrations = _required_integrations(normalized)
    stop_conditions = _stop_conditions(normalized)
    proposed_actions = _proposed_actions(normalized)
    missing_information = _missing_information(normalized, required_integrations)
    workflow = await create_workflow(
        session,
        business_id=business_id,
        actor_user_id=actor_user_id,
        data=WorkflowCreate(
            name=data.name or _workflow_name(normalized),
            description=(
                f"Automation Copilot draft: {prompt[:1500]} "
                "External delivery remains withheld until required identity, consent, provider, and policy inputs are configured."
            )[:2000],
            trigger_type=trigger_type,
            timezone=data.timezone,
        ),
    )
    version = await session.scalar(select(AutomationWorkflowVersion).where(
        AutomationWorkflowVersion.workflow_id == workflow.id,
        AutomationWorkflowVersion.business_id == business_id,
        AutomationWorkflowVersion.version == workflow.current_version,
    ))
    if version is None:
        raise AutomationValidationError("workflow_version_missing")
    specifications: list[tuple[str, str, dict[str, object]]] = [
        ("trigger", "Trusted trigger", {"kind": "trigger", "trigger_type": trigger_type}),
    ]
    condition = _condition(normalized, trigger_type)
    if condition:
        specifications.append(("branch", "Eligibility and stop-condition check", {
            "kind": "branch", "condition": condition,
            "true_label": "true", "false_label": "false",
        }))
    if wait_seconds:
        specifications.append(("delay", "Durable wait", {
            "kind": "delay", "mode": "duration", "seconds": wait_seconds,
            "until": None, "context_field": None, "offset_seconds": 0,
        }))
    if _requests_external_action(normalized):
        specifications.append(("approval", "Review external communication", {
            "kind": "approval", "reason_code": "external_communication",
            "expires_in_seconds": None,
        }))
    specifications.append(("end", "Safe completion", {"kind": "end", "outcome": "success"}))
    nodes: list[AutomationNode] = []
    for index, (node_type, name, raw) in enumerate(specifications):
        node = AutomationNode(
            business_id=business_id, workflow_id=workflow.id,
            workflow_version_id=version.id, node_key=uuid4(), node_type=node_type,
            name=name, configuration=validate_node_configuration(
                node_type, raw,
                workflow_trigger_type=trigger_type if node_type == "trigger" else None,
            ),
            position_x=0, position_y=index * 140, order_index=index,
        )
        session.add(node)
        nodes.append(node)
    await session.flush()
    edges: list[AutomationEdge] = []
    for index in range(len(nodes) - 1):
        source, target = nodes[index], nodes[index + 1]
        if source.node_type == "branch":
            false_end = AutomationNode(
                business_id=business_id, workflow_id=workflow.id,
                workflow_version_id=version.id, node_key=uuid4(), node_type="end",
                name="Stopped: eligibility condition not met",
                configuration={"kind": "end", "outcome": "success"},
                position_x=360, position_y=index * 140, order_index=len(nodes),
            )
            session.add(false_end)
            nodes.append(false_end)
            await session.flush()
            edges.extend([
                AutomationEdge(
                    business_id=business_id, workflow_id=workflow.id,
                    workflow_version_id=version.id, edge_key=uuid4(),
                    source_node_key=source.node_key, target_node_key=target.node_key,
                    branch_label="true", order_index=0,
                ),
                AutomationEdge(
                    business_id=business_id, workflow_id=workflow.id,
                    workflow_version_id=version.id, edge_key=uuid4(),
                    source_node_key=source.node_key, target_node_key=false_end.node_key,
                    branch_label="false", order_index=1,
                ),
            ])
        else:
            edges.append(AutomationEdge(
                business_id=business_id, workflow_id=workflow.id,
                workflow_version_id=version.id, edge_key=uuid4(),
                source_node_key=source.node_key, target_node_key=target.node_key,
                branch_label=None, order_index=0,
            ))
    session.add_all(edges)
    await session.flush()
    errors = validate_graph(nodes, edges)
    if errors:
        raise AutomationValidationError(",".join(errors))
    detail = await workflow_detail(session, business_id=business_id, workflow_id=workflow.id)
    return _response(
        detail, normalized, required_integrations, missing_information,
        stop_conditions, proposed_actions,
    )


async def refine_workflow(
    session: AsyncSession,
    *,
    business_id: UUID,
    workflow_id: UUID,
    actor_user_id: UUID,
    data: AutomationCopilotRefineRequest,
) -> dict[str, object]:
    workflow = await get_workflow(
        session, business_id=business_id, workflow_id=workflow_id, for_update=True,
    )
    normalized = " ".join(data.instruction.split()).casefold()
    wait_seconds = _wait_seconds(normalized)
    _version, nodes, _edges = await load_graph(session, workflow=workflow)
    if wait_seconds:
        delay = next((node for node in nodes if node.node_type == "delay"), None)
        if delay is None:
            raise AutomationValidationError("copilot_refinement_requires_delay")
        await update_node(
            session, business_id=business_id, workflow_id=workflow_id,
            node_key=delay.node_key, actor_user_id=actor_user_id,
            data=NodeUpdate(configuration={
                "kind": "delay", "mode": "duration", "seconds": wait_seconds,
                "until": None, "context_field": None, "offset_seconds": 0,
            }),
        )
    elif "email instead" in normalized or "whatsapp instead" in normalized:
        requested = "email" if "email instead" in normalized else "whatsapp"
        await update_workflow(
            session, business_id=business_id, workflow_id=workflow_id,
            actor_user_id=actor_user_id,
            data=WorkflowUpdate(description=(
                f"{workflow.description or ''} Copilot refinement: prefer {requested}; "
                "external delivery remains withheld until consent and connection checks pass."
            )[:2000]),
        )
    else:
        raise AutomationValidationError("copilot_refinement_unsupported")
    detail = await workflow_detail(session, business_id=business_id, workflow_id=workflow_id)
    context = _refinement_context(workflow.description or "", normalized)
    requirements = _required_integrations(context)
    return _response(
        detail, context, requirements,
        _missing_information(context, requirements), _stop_conditions(context),
        _proposed_actions(context),
    )


def _response(detail, normalized, required, missing, stops, proposed_actions):
    explanation = (
        "This draft uses a trusted trigger, deterministic conditions, durable delays, and the existing approval queue. "
        "No message, provider write, or spend occurs during compilation or dry-run."
    )
    if _requests_external_action(normalized):
        explanation += " The requested external action is intentionally withheld until its provider, recipient identity, consent, and policy inputs are authoritative."
    return {
        "workflow": detail,
        "explanation": explanation,
        "required_integrations": required,
        "missing_information": missing,
        "stop_conditions": stops,
        "proposed_actions": proposed_actions,
        "executable_actions_withheld": _requests_external_action(normalized),
    }


def _trigger_type(value: str) -> str:
    if "abandon" in value and ("cart" in value or "checkout" in value):
        return "checkout_abandoned"
    if "order" in value and "deliver" in value:
        return "order_status_changed"
    if "instagram" in value and ("lead" in value or "asks" in value):
        return "inbound_message_recorded"
    if "lead" in value:
        return "lead_created"
    if "order" in value:
        return "order_created"
    return "manual_test"


def _workflow_name(value: str) -> str:
    if "abandon" in value:
        return "Abandoned checkout recovery"
    if "review" in value and "order" in value:
        return "Post-purchase review request"
    if "lead" in value:
        return "Lead qualification follow-up"
    return "Automation Copilot draft"


def _wait_seconds(value: str) -> int | None:
    match = re.search(
        r"(?:wait\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*(minutes?|hours?|days?)",
        value,
    )
    if not match:
        return None
    amount_text = match.group(1)
    amount = int(amount_text) if amount_text.isdigit() else {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }[amount_text]
    multiplier = 60 if match.group(2).startswith("minute") else 3600 if match.group(2).startswith("hour") else 86400
    seconds = amount * multiplier
    if not 60 <= seconds <= 2_592_000:
        raise AutomationValidationError("copilot_delay_out_of_range")
    return seconds


def _condition(value: str, trigger_type: str) -> dict[str, object] | None:
    amount = re.search(r"orders?\s+(?:above|over|greater than)\s*\$?([0-9]+(?:\.[0-9]{1,2})?)", value)
    if amount:
        return {"field": "order.total", "operator": "gt", "value": amount.group(1)}
    if trigger_type == "order_status_changed" and "deliver" in value:
        return {"field": "order.status", "operator": "equals", "value": "completed"}
    if trigger_type == "inbound_message_recorded" and "instagram" in value:
        return {"field": "conversation.channel", "operator": "equals", "value": "instagram"}
    return None


def _required_integrations(value: str) -> list[str]:
    result = []
    if "whatsapp" in value:
        result.append("whatsapp_business")
    if "email" in value:
        result.append("gmail_or_outlook")
    if "instagram" in value:
        result.append("instagram")
    return result


def _refinement_context(description: str, instruction: str) -> str:
    context = f"{description.casefold()} {instruction}".strip()
    if "email instead" in instruction:
        context = context.replace("whatsapp", "")
    elif "whatsapp instead" in instruction:
        context = context.replace("email", "")
    return " ".join(context.split())


def _proposed_actions(value: str) -> list[dict[str, str]]:
    """Describe requested actions without inventing recipient refs or payloads."""
    candidates: list[tuple[int, dict[str, str]]] = []
    if "whatsapp" in value:
        candidates.append((value.index("whatsapp"), {
            "action_type": "send_whatsapp_message",
            "channel": "whatsapp",
            "condition": "Only when an authoritative WhatsApp recipient and channel consent are available.",
            "policy_behavior": "Create a governed action intent requiring the existing policy and approval flow.",
            "execution_state": "withheld_pending_authoritative_inputs",
        }))
    if "email" in value:
        purchase_guard = " and no purchase has been recorded after the trigger" if "not purchased" in value or "have not purchased" in value else ""
        candidates.append((value.index("email"), {
            "action_type": "send_email",
            "channel": "email",
            "condition": f"Only when an authoritative email recipient and channel consent are available{purchase_guard}.",
            "policy_behavior": "Create a governed action intent requiring the existing policy and approval flow.",
            "execution_state": "withheld_pending_authoritative_inputs",
        }))
    if not candidates and ("send" in value or "message" in value):
        candidates.append((0, {
            "action_type": "send_customer_message",
            "channel": "customer_message",
            "condition": "Only when an authoritative recipient, supported channel, and consent are available.",
            "policy_behavior": "Create a governed action intent requiring the existing policy and approval flow.",
            "execution_state": "withheld_pending_authoritative_inputs",
        }))
    return [item for _, item in sorted(candidates, key=lambda candidate: candidate[0])]


def _stop_conditions(value: str) -> list[str]:
    result = ["stop after the goal event is recorded", "stop after opt-out or consent withdrawal"]
    if "weekend" in value:
        result.append("do not run on weekends in the business timezone")
    if "purchase" in value or "cart" in value or "checkout" in value:
        result.append("stop immediately after purchase")
    return list(dict.fromkeys(result))


def _missing_information(value: str, required: list[str]) -> list[str]:
    result = []
    if required:
        result.append("a healthy selected provider connection")
    if _requests_external_action(value):
        result.extend(["authoritative recipient identity", "channel consent and opt-out state", "quiet-hours policy"])
    if "discount" in value:
        result.append("an approved configured discount")
    return result


def _requests_external_action(value: str) -> bool:
    return any(term in value for term in ("send", "message", "email", "whatsapp", "alert", "ask for a review"))
