from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.definitions import get_agent_definition
from app.domain.ai_workforce import (
    AUTONOMY_DESCRIPTIONS,
    AutonomyMode,
    CANONICAL_AGENT_ROLES,
    CommandIntent,
)
from app.exceptions.ai_workforce import (
    AIWorkforceConflictError,
    AIWorkforceNotFoundError,
    AIWorkforcePersistenceError,
    AIWorkforceValidationError,
)
from app.models.ai_action import AIAction
from app.models.ai_agent_execution import AIAgentExecution
from app.models.ai_workforce import AIAgentConfig, AICommand
from app.models.appointment import Appointment
from app.models.approval_request import ApprovalRequest
from app.models.automation import AutomationWorkflowRun
from app.models.conversation import Conversation, ConversationMessage
from app.models.crm_lead import CRMLead
from app.models.customer import Customer
from app.models.chatbot import ChatbotSession
from app.models.business import Business
from app.models.marketing import Campaign
from app.models.integration import IntegrationConnection
from app.models.notification import Notification
from app.models.opportunity import Opportunity
from app.models.order import Order
from app.models.report import BusinessReport
from app.schemas.ai_agent import AIAgentRole
from app.schemas.ai_workforce import (
    AgentActivityResponse,
    AgentConfigResponse,
    AgentMetricsResponse,
    ApprovalLinkResponse,
    CapabilityResponse,
    CommandRouteResponse,
    DailyBriefResponse,
    DailyBriefSection,
    ProposedActionResponse,
    SuggestedCommandResponse,
)
from app.services.ai_capabilities import (
    AI_CAPABILITY_REGISTRY,
    ROLE_CAPABILITIES,
    validate_role_capabilities,
)
from app.services.operations import record_audit


def route_command(command: str) -> CommandRouteResponse:
    """Deterministically classify text into a strict server-owned route."""
    text = " ".join(command.lower().split())

    def has(*terms: str) -> bool:
        return any(term in text for term in terms)

    role: AIAgentRole
    intent: CommandIntent
    capabilities: list[str]
    modules: list[str]
    delegation: list[AIAgentRole] = []
    clarification = False

    if has(
        "website chatbot", "website bot", "website lead", "website handoff",
        "website session", "chatbot lead", "chatbot handoff", "widget session",
    ):
        role, intent = "analytics", "chatbot_analytics"
        capabilities, modules = ["read_analytics"], ["chatbot"]
    elif has(
        "integration status", "integrations status", "connected account", "connector status",
        "is whatsapp connected", "is gmail connected", "is google ads connected",
        "is meta ads connected", "is instagram connected", "is facebook connected",
        "is outlook connected", "calendar connected",
    ):
        role, intent = "operations", "integration_status"
        capabilities, modules = ["read_integrations"], ["integrations"]
    elif has("why did sales", "sales drop", "revenue drop", "kpi", "compare", "anomaly"):
        role, intent = "analytics", "analytics_analysis"
        capabilities, modules = ["read_analytics", "analyze_sales"], ["analytics", "orders", "crm"]
    elif has("report", "performance summary"):
        role, intent = "analytics", "reporting"
        capabilities, modules = ["read_reports", "read_analytics", "draft_report"], ["reports", "analytics"]
    elif has(
        "respond", "response", "reply", "customer question", "support", "conversation", "handoff",
        "diagnos", "prescription", "prescribe", "symptom", "medical advice", "treatment",
    ):
        role = "support"
        intent = "draft_response" if has("respond", "response", "reply", "draft") else "customer_support"
        capabilities = ["read_conversations", "read_customers", "read_orders", "draft_customer_response"]
        modules = ["conversations", "customers", "orders"]
    elif has("appointment", "available slot", "availability", "schedule", "doctor", "provider"):
        role, intent = "operations", "scheduling_lookup"
        capabilities, modules = ["read_scheduling", "recommend_slots"], ["scheduling"]
    elif has("workflow", "automation"):
        role, intent = "operations", "workflow_analysis"
        capabilities, modules = ["read_workflows", "analyze_operations"], ["automations"]
    elif has("order", "bottleneck", "operations", "capacity"):
        role, intent = "operations", "operations_analysis"
        capabilities, modules = ["read_orders", "analyze_operations"], ["orders", "scheduling", "automations"]
    elif has("campaign", "instagram", "marketing", "content", "social", "competitor"):
        role = "cmo"
        intent = "marketing_plan" if has("create", "plan", "draft") else "marketing_analysis"
        capabilities = ["read_marketing", "analyze_marketing", "draft_campaign"]
        modules = ["marketing", "campaigns", "content"]
    elif has("lead", "follow-up", "follow up", "crm", "pipeline", "opportunity", "sales"):
        role = "sales"
        intent = "lead_follow_up" if has("follow-up", "follow up", "lead") else "sales_analysis"
        capabilities = ["read_crm", "read_customers", "read_opportunities", "analyze_sales"]
        modules = ["crm", "customers", "opportunities", "orders"]
    elif has("focus", "priority", "priorities", "today"):
        role, intent = "business_manager", "daily_focus"
        capabilities, modules = ["analyze_business", "read_analytics", "read_approvals"], ["analytics", "crm", "orders", "scheduling", "approvals"]
        delegation = ["analytics", "sales", "operations"]
    elif has("business", "overview", "how are we", "how is my"):
        role, intent = "business_manager", "business_overview"
        capabilities, modules = ["analyze_business", "read_analytics", "read_reports"], ["analytics", "crm", "orders", "marketing"]
        delegation = ["analytics", "sales", "operations"]
    else:
        role, intent = "business_manager", "unknown"
        capabilities, modules = ["read_business_brain", "analyze_business"], ["business_brain"]
        clarification = True

    allowed = ROLE_CAPABILITIES[role]
    capabilities = [item for item in capabilities if item in allowed]
    return CommandRouteResponse(
        primary_role=role,
        intent=intent,
        required_capabilities=capabilities,
        relevant_modules=modules,
        delegation_roles=delegation[:3],
        clarification_required=clarification,
    )


def persisted_command_route(command: AICommand) -> CommandRouteResponse:
    """Rehydrate the immutable structured route stored with a command."""
    fallback = route_command(command.command_text)
    metadata = dict(command.route_metadata or {})
    return CommandRouteResponse.model_validate({
        "primary_role": command.resolved_role,
        "intent": command.intent,
        "required_capabilities": metadata.get(
            "required_capabilities", fallback.required_capabilities
        ),
        "relevant_modules": metadata.get("relevant_modules", fallback.relevant_modules),
        "delegation_roles": metadata.get("delegation_roles", fallback.delegation_roles),
        "clarification_required": metadata.get(
            "clarification_required", fallback.clarification_required
        ),
    })


async def ensure_agent_configs(session: AsyncSession, *, business_id: UUID) -> list[AIAgentConfig]:
    try:
        existing = list((await session.scalars(
            select(AIAgentConfig).where(AIAgentConfig.business_id == business_id)
        )).all())
        by_role = {item.role: item for item in existing}
        for role in CANONICAL_AGENT_ROLES:
            if role in by_role:
                continue
            definition = get_agent_definition(role)
            config = AIAgentConfig(
                business_id=business_id,
                role=role,
                display_name=definition.display_name,
                enabled=True,
                autonomy_mode="manual",
                custom_instructions=None,
                capability_config=sorted(ROLE_CAPABILITIES[role]),
            )
            session.add(config)
            by_role[role] = config
        await session.flush()
        return [by_role[role] for role in CANONICAL_AGENT_ROLES]
    except IntegrityError:
        raise AIWorkforcePersistenceError("Unable to prepare agent configuration") from None
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to prepare agent configuration") from None


async def get_agent_config(session: AsyncSession, *, business_id: UUID, role: AIAgentRole) -> AIAgentConfig:
    configs = await ensure_agent_configs(session, business_id=business_id)
    for config in configs:
        if config.role == role:
            return config
    raise AIWorkforceNotFoundError("Agent configuration not found")


async def update_agent_config(
    session: AsyncSession,
    *,
    business_id: UUID,
    role: AIAgentRole,
    actor_user_id: UUID,
    display_name: str | None,
    enabled: bool | None,
    autonomy_mode: str | None,
    custom_instructions: str | None,
    capabilities: list[str] | None,
    changed_fields: set[str],
) -> AIAgentConfig:
    config = await get_agent_config(session, business_id=business_id, role=role)
    before = f"enabled={config.enabled};autonomy={config.autonomy_mode}"
    if "display_name" in changed_fields:
        if display_name is None:
            raise AIWorkforceValidationError("Display name cannot be blank")
        config.display_name = display_name
    if "enabled" in changed_fields:
        if enabled is None:
            raise AIWorkforceValidationError("Enabled state cannot be null")
        config.enabled = bool(enabled)
    if "autonomy_mode" in changed_fields:
        if autonomy_mode not in AUTONOMY_DESCRIPTIONS:
            raise AIWorkforceValidationError("Invalid autonomy mode")
        config.autonomy_mode = autonomy_mode
    if "custom_instructions" in changed_fields:
        config.custom_instructions = custom_instructions
    if "capabilities" in changed_fields:
        if capabilities is None:
            raise AIWorkforceValidationError("Capabilities cannot be null")
        try:
            config.capability_config = list(validate_role_capabilities(role, capabilities))
        except ValueError:
            raise AIWorkforceValidationError("Invalid role capability selection") from None
    try:
        await session.flush()
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to update agent configuration") from None
    record_audit(
        session, business_id=business_id, actor_user_id=actor_user_id,
        event_type="ai_agent.config_changed", entity_type="ai_agent_config",
        entity_id=config.id, summary=f"Updated {role} AI agent configuration.",
        before_value=before,
        after_value=f"enabled={config.enabled};autonomy={config.autonomy_mode}",
    )
    return config


async def reset_agent_config(
    session: AsyncSession, *, business_id: UUID, role: AIAgentRole, actor_user_id: UUID
) -> AIAgentConfig:
    config = await get_agent_config(session, business_id=business_id, role=role)
    definition = get_agent_definition(role)
    config.display_name = definition.display_name
    config.enabled = True
    config.autonomy_mode = "manual"
    config.custom_instructions = None
    config.capability_config = sorted(ROLE_CAPABILITIES[role])
    try:
        await session.flush()
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to reset agent configuration") from None
    record_audit(
        session, business_id=business_id, actor_user_id=actor_user_id,
        event_type="ai_agent.config_reset", entity_type="ai_agent_config",
        entity_id=config.id, summary=f"Reset {role} AI agent configuration.",
    )
    return config


async def agent_metrics_by_role(
    session: AsyncSession, *, business_id: UUID
) -> tuple[dict[str, AgentMetricsResponse], dict[str, datetime | None]]:
    values = {role: AgentMetricsResponse() for role in CANONICAL_AGENT_ROLES}
    try:
        rows = (await session.execute(
            select(
                AIAgentExecution.role,
                func.count(AIAgentExecution.id),
                func.count(AIAgentExecution.id).filter(AIAgentExecution.status == "completed"),
                func.count(AIAgentExecution.id).filter(AIAgentExecution.status == "needs_approval"),
                func.count(AIAgentExecution.id).filter(AIAgentExecution.status == "failed"),
                func.avg(AIAgentExecution.duration_ms),
                func.coalesce(func.sum(AIAgentExecution.input_tokens), 0),
                func.coalesce(func.sum(AIAgentExecution.output_tokens), 0),
                func.max(AIAgentExecution.created_at),
            ).where(AIAgentExecution.business_id == business_id).group_by(AIAgentExecution.role)
        )).all()
        action_rows = (await session.execute(
            select(AIAgentExecution.role, func.count(AIAction.id))
            .join(AIAction, AIAction.execution_id == AIAgentExecution.id)
            .where(AIAgentExecution.business_id == business_id, AIAction.business_id == business_id)
            .group_by(AIAgentExecution.role)
        )).all()
        approval_rows = (await session.execute(
            select(
                AIAgentExecution.role,
                func.count(ApprovalRequest.id).filter(ApprovalRequest.status == "pending"),
                func.count(ApprovalRequest.id).filter(ApprovalRequest.status == "approved"),
                func.count(ApprovalRequest.id).filter(ApprovalRequest.status.in_(("approved", "rejected", "expired"))),
            ).join(AIAction, AIAction.execution_id == AIAgentExecution.id)
            .join(ApprovalRequest, ApprovalRequest.action_id == AIAction.id)
            .where(
                AIAgentExecution.business_id == business_id,
                AIAction.business_id == business_id,
                ApprovalRequest.business_id == business_id,
            ).group_by(AIAgentExecution.role)
        )).all()
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to aggregate agent metrics") from None

    last_activity: dict[str, datetime | None] = {}
    for role, total, completed, needs_approval, failed, average, input_tokens, output_tokens, last_at in rows:
        values[role] = AgentMetricsResponse(
            execution_count=int(total or 0), completed_count=int(completed or 0),
            needs_approval_count=int(needs_approval or 0), failed_count=int(failed or 0),
            average_duration_ms=int(average) if average is not None else None,
            input_tokens=int(input_tokens or 0), output_tokens=int(output_tokens or 0),
        )
        last_activity[role] = last_at
    for role, count in action_rows:
        values[role].proposed_action_count = int(count or 0)
    for role, pending, approved, decided in approval_rows:
        values[role].pending_approval_count = int(pending or 0)
        values[role].approval_rate = round((int(approved) / int(decided)) * 100, 1) if decided else None
    return values, last_activity


def config_response(
    config: AIAgentConfig,
    *,
    metrics: AgentMetricsResponse | None = None,
    last_activity_at: datetime | None = None,
) -> AgentConfigResponse:
    role = cast(AIAgentRole, config.role)
    selected = tuple(config.capability_config or [])
    valid_selected = [key for key in selected if key in ROLE_CAPABILITIES[role]]
    definition = get_agent_definition(role)
    return AgentConfigResponse(
        id=config.id, business_id=config.business_id, role=role,
        display_name=config.display_name, enabled=config.enabled,
        status="active" if config.enabled else "disabled", health="ready",
        autonomy_mode=cast(AutonomyMode, config.autonomy_mode),
        autonomy_description=AUTONOMY_DESCRIPTIONS[cast(AutonomyMode, config.autonomy_mode)],
        custom_instructions=config.custom_instructions,
        capabilities=[CapabilityResponse(
            key=AI_CAPABILITY_REGISTRY[key].key,
            category=AI_CAPABILITY_REGISTRY[key].category,
            description=AI_CAPABILITY_REGISTRY[key].description,
        ) for key in sorted(valid_selected)],
        default_capabilities=sorted(ROLE_CAPABILITIES[role]),
        role_description=definition.mission, metrics=metrics or AgentMetricsResponse(),
        last_activity_at=last_activity_at, created_at=config.created_at, updated_at=config.updated_at,
    )


async def list_activity(
    session: AsyncSession, *, business_id: UUID, page: int, page_size: int,
    role: AIAgentRole | None = None, status: str | None = None,
    command_id: UUID | None = None,
) -> tuple[list[AgentActivityResponse], int]:
    filters = [AIAgentExecution.business_id == business_id]
    if role:
        filters.append(AIAgentExecution.role == role)
    if status:
        filters.append(AIAgentExecution.status == status)
    if command_id:
        filters.append(AIAgentExecution.command_id == command_id)
    statement = select(AIAgentExecution).where(*filters)
    try:
        total = int(await session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        executions = list((await session.scalars(
            statement.order_by(AIAgentExecution.created_at.desc(), AIAgentExecution.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).all())
        actions_by_execution, _ = await _load_actions(session, business_id=business_id, execution_ids=[item.id for item in executions])
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to read agent activity") from None
    return [activity_response(item, actions_by_execution.get(item.id, [])) for item in executions], total


async def get_activity(session: AsyncSession, *, business_id: UUID, execution_id: UUID) -> AgentActivityResponse:
    try:
        execution = await session.scalar(select(AIAgentExecution).where(
            AIAgentExecution.business_id == business_id, AIAgentExecution.id == execution_id
        ))
        if execution is None:
            raise AIWorkforceNotFoundError("Agent execution not found")
        actions, _ = await _load_actions(session, business_id=business_id, execution_ids=[execution.id])
    except AIWorkforceNotFoundError:
        raise
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to read agent activity") from None
    return activity_response(execution, actions.get(execution.id, []))


def activity_response(execution: AIAgentExecution, actions: list[ProposedActionResponse]) -> AgentActivityResponse:
    task_summary = " ".join(execution.task.split())
    if len(task_summary) > 180:
        task_summary = task_summary[:177] + "..."
    return AgentActivityResponse(
        id=execution.id, business_id=execution.business_id,
        command_id=execution.command_id, parent_execution_id=execution.parent_execution_id,
        role=cast(AIAgentRole, execution.role), trigger=execution.trigger_type,
        status=execution.status, task_summary=task_summary,
        summary=execution.output_summary, failure_code=execution.failure_code,
        duration_ms=execution.duration_ms, input_tokens=execution.input_tokens,
        output_tokens=execution.output_tokens, estimated_cost_usd=execution.estimated_cost_usd,
        delegation_sequence=execution.delegation_sequence,
        delegation_depth=execution.delegation_depth, proposed_actions=actions,
        created_at=execution.created_at, completed_at=execution.completed_at,
    )


async def _load_actions(
    session: AsyncSession, *, business_id: UUID, execution_ids: list[UUID]
) -> tuple[dict[UUID, list[ProposedActionResponse]], list[ProposedActionResponse]]:
    grouped: dict[UUID, list[ProposedActionResponse]] = defaultdict(list)
    if not execution_ids:
        return grouped, []
    actions = list((await session.scalars(select(AIAction).where(
        AIAction.business_id == business_id, AIAction.execution_id.in_(execution_ids)
    ).order_by(AIAction.created_at, AIAction.proposal_index))).all())
    approvals = list((await session.scalars(select(ApprovalRequest).where(
        ApprovalRequest.business_id == business_id,
        ApprovalRequest.action_id.in_([item.id for item in actions]) if actions else False,
    ))).all()) if actions else []
    by_action = {item.action_id: item for item in approvals if item.action_id}
    flat: list[ProposedActionResponse] = []
    for action in actions:
        approval = by_action.get(action.id)
        value = ProposedActionResponse(
            id=action.id, execution_id=action.execution_id, action_type=action.action_type,
            description=action.description, risk_level=action.risk_level, status=action.status,
            policy_decision=action.policy_decision,
            requires_approval=action.policy_decision == "require_approval" or action.proposed_requires_approval,
            approval=ApprovalLinkResponse(id=approval.id, status=approval.status, reason_code=approval.reason_code) if approval else None,
        )
        grouped[action.execution_id].append(value)
        flat.append(value)
    return grouped, flat


async def create_command_record(
    session: AsyncSession, *, business_id: UUID, user_id: UUID, command_text: str,
    route: CommandRouteResponse, trigger_source: str, context_references: list[dict[str, str]],
) -> AICommand:
    value = AICommand(
        business_id=business_id, requested_by_user_id=user_id,
        command_text=command_text, resolved_role=route.primary_role,
        intent=route.intent, status="queued", execution_id=None,
        route_metadata={
            **route.model_dump(mode="json"), "trigger_source": trigger_source,
            "context_references": context_references,
        }, summary=None, failure_code=None, completed_at=None,
    )
    session.add(value)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to create AI command") from None
    record_audit(
        session, business_id=business_id, actor_user_id=user_id,
        event_type="ai_command.submitted", entity_type="ai_command", entity_id=value.id,
        summary=f"Submitted command routed to {route.primary_role}.",
        after_value=f"intent={route.intent}",
    )
    return value


async def get_command(session: AsyncSession, *, business_id: UUID, command_id: UUID) -> AICommand:
    try:
        value = await session.scalar(select(AICommand).where(
            AICommand.business_id == business_id, AICommand.id == command_id
        ))
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to read AI command") from None
    if value is None:
        raise AIWorkforceNotFoundError("AI command not found")
    return value


async def list_commands(
    session: AsyncSession, *, business_id: UUID, page: int, page_size: int, status: str | None
) -> tuple[list[AICommand], int]:
    filters = [AICommand.business_id == business_id]
    if status:
        filters.append(AICommand.status == status)
    statement = select(AICommand).where(*filters)
    try:
        total = int(await session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        values = list((await session.scalars(statement.order_by(
            AICommand.created_at.desc(), AICommand.id.desc()
        ).offset((page - 1) * page_size).limit(page_size))).all())
        return values, total
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to list AI commands") from None


async def cancel_command(
    session: AsyncSession, *, business_id: UUID, command_id: UUID, user_id: UUID
) -> AICommand:
    command = await get_command(session, business_id=business_id, command_id=command_id)
    if command.status != "queued":
        raise AIWorkforceConflictError("Only queued commands can be canceled")
    command.status = "canceled"
    command.completed_at = datetime.now(UTC)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to cancel AI command") from None
    record_audit(
        session, business_id=business_id, actor_user_id=user_id,
        event_type="ai_command.canceled", entity_type="ai_command", entity_id=command.id,
        summary="Canceled queued AI command.",
    )
    return command


async def build_operational_context(
    session: AsyncSession, *, business_id: UUID, route: CommandRouteResponse,
    context_references: list[dict[str, str]],
) -> str:
    try:
        timezone_name = await session.scalar(
            select(Business.timezone).where(Business.id == business_id)
        )
        try:
            business_timezone = ZoneInfo(timezone_name or "UTC")
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            business_timezone = ZoneInfo("UTC")
        now = datetime.now(UTC)
        local_tomorrow = datetime.now(business_timezone).date() + timedelta(days=1)
        tomorrow_start = datetime.combine(
            local_tomorrow, datetime.min.time(), tzinfo=business_timezone
        ).astimezone(UTC)
        tomorrow_end = tomorrow_start + timedelta(days=1)
        customer_count = await _count(session, Customer, business_id)
        lead_count = await _count(session, CRMLead, business_id, CRMLead.stage.notin_(("won", "lost")))
        overdue_followups = await _count(session, CRMLead, business_id, CRMLead.next_follow_up_at < now, CRMLead.stage.notin_(("won", "lost")))
        active_orders = await _count(session, Order, business_id, Order.status.in_(("draft", "confirmed", "processing")))
        revenue = await session.scalar(select(func.coalesce(func.sum(Order.total), 0)).where(
            Order.business_id == business_id, Order.status == "completed"
        ))
        open_conversations = await _count(session, Conversation, business_id, Conversation.status.in_(("open", "escalated")))
        tomorrow_appointments = await _count(session, Appointment, business_id,
            Appointment.status == "confirmed", Appointment.starts_at >= tomorrow_start,
            Appointment.starts_at < tomorrow_end)
        workflow_failures = await _count(session, AutomationWorkflowRun, business_id, AutomationWorkflowRun.status == "failed")
        pending_approvals = await _count(session, ApprovalRequest, business_id, ApprovalRequest.status == "pending")
        open_opportunities = await _count(session, Opportunity, business_id, Opportunity.status.in_(("open", "in_progress")))
        campaign_count = await _count(session, Campaign, business_id, Campaign.status.notin_(("completed", "canceled")))
        report_count = await _count(session, BusinessReport, business_id)
        connected_integrations = await _count(
            session,
            IntegrationConnection,
            business_id,
            IntegrationConnection.status.in_(("connected", "degraded")),
        )
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to assemble command context") from None
    facts = [
        f"Customers: {customer_count}.", f"Open CRM leads: {lead_count}.",
        f"Overdue CRM follow-ups: {overdue_followups}.", f"Active orders: {active_orders}.",
        f"Recorded completed-order revenue: {Decimal(revenue or 0):.2f}.",
        f"Open or escalated conversations: {open_conversations}.",
        f"Confirmed appointments tomorrow: {tomorrow_appointments}.",
        f"Recorded failed workflow runs: {workflow_failures}.",
        f"Pending approvals: {pending_approvals}.", f"Open opportunities: {open_opportunities}.",
        f"Non-terminal marketing campaigns: {campaign_count}.", f"Available reports: {report_count}.",
        f"Connected or degraded integrations: {connected_integrations}.",
    ]
    if route.intent == "chatbot_analytics" or "chatbot" in route.relevant_modules:
        try:
            chatbot_period_start = now - timedelta(days=30)
            chatbot_row = (await session.execute(select(
                func.count(ChatbotSession.id),
                func.count(ChatbotSession.lead_captured_at),
                func.count(ChatbotSession.handoff_requested_at),
                func.coalesce(func.sum(ChatbotSession.message_count), 0),
                func.coalesce(func.sum(ChatbotSession.appointment_booked_count), 0),
            ).where(
                ChatbotSession.business_id == business_id,
                ChatbotSession.started_at >= chatbot_period_start,
            ))).one()
        except SQLAlchemyError:
            raise AIWorkforcePersistenceError("Unable to read chatbot metrics") from None
        facts.append(
            "Website chatbot metrics for the last 30 days (aggregated; no raw transcripts): "
            f"sessions={int(chatbot_row[0])}, leads={int(chatbot_row[1])}, "
            f"handoffs={int(chatbot_row[2])}, messages={int(chatbot_row[3])}, "
            f"appointments_booked={int(chatbot_row[4])}."
        )
    if route.intent == "integration_status" or "integrations" in route.relevant_modules:
        try:
            integrations = list((await session.scalars(
                select(IntegrationConnection)
                .where(IntegrationConnection.business_id == business_id)
                .order_by(IntegrationConnection.connector_type, IntegrationConnection.id)
                .limit(20)
            )).all())
        except SQLAlchemyError:
            raise AIWorkforcePersistenceError("Unable to read bounded integration context") from None
        if integrations:
            facts.append("Bounded connector status (credentials excluded): " + "; ".join(
                f"{item.display_name} [{item.id}] status={item.status}, auth={item.authentication_state}, "
                f"health={item.health}, selected_resources={len(item.selected_resources or [])}"
                for item in integrations
            ))
        else:
            facts.append("No integration connection records exist for this business.")
    if route.primary_role == "sales":
        try:
            leads = list((await session.scalars(
                select(CRMLead).where(
                    CRMLead.business_id == business_id,
                    CRMLead.stage.notin_(("won", "lost")),
                ).order_by(
                    case(
                        (CRMLead.priority == "urgent", 0),
                        (CRMLead.priority == "high", 1),
                        (CRMLead.priority == "medium", 2),
                        else_=3,
                    ),
                    CRMLead.next_follow_up_at.asc().nullslast(),
                    CRMLead.updated_at.desc(),
                ).limit(10)
            )).all())
        except SQLAlchemyError:
            raise AIWorkforcePersistenceError("Unable to read bounded sales context") from None
        if leads:
            facts.append("Bounded priority leads (no contact details): " + "; ".join(
                f"{lead.display_name} [{lead.id}] stage={lead.stage}, priority={lead.priority}, "
                f"qualification={lead.qualification_state}, follow_up={lead.next_follow_up_at.isoformat() if lead.next_follow_up_at else 'not_set'}"
                for lead in leads
            ))
    if route.primary_role == "operations":
        try:
            orders = list((await session.scalars(
                select(Order).where(
                    Order.business_id == business_id,
                    Order.status.in_(("draft", "confirmed", "processing")),
                ).order_by(Order.created_at, Order.id).limit(10)
            )).all())
        except SQLAlchemyError:
            raise AIWorkforcePersistenceError("Unable to read bounded operations context") from None
        if orders:
            facts.append("Bounded active orders: " + "; ".join(
                f"{order.order_number} [{order.id}] status={order.status}, total={order.total} {order.currency}"
                for order in orders
            ))
    if route.primary_role == "cmo":
        try:
            campaigns = list((await session.scalars(
                select(Campaign).where(Campaign.business_id == business_id)
                .order_by(Campaign.updated_at.desc(), Campaign.id.desc()).limit(10)
            )).all())
        except SQLAlchemyError:
            raise AIWorkforcePersistenceError("Unable to read bounded marketing context") from None
        if campaigns:
            facts.append("Bounded campaign records: " + "; ".join(
                f"{campaign.name} [{campaign.id}] status={campaign.status}, planned_budget={campaign.planned_budget} {campaign.currency}"
                for campaign in campaigns
            ))
    if route.intent == "workflow_analysis":
        try:
            failures = list((await session.scalars(
                select(AutomationWorkflowRun).where(
                    AutomationWorkflowRun.business_id == business_id,
                    AutomationWorkflowRun.status == "failed",
                ).order_by(
                    AutomationWorkflowRun.created_at.desc(),
                    AutomationWorkflowRun.id.desc(),
                ).limit(10)
            )).all())
        except SQLAlchemyError:
            raise AIWorkforcePersistenceError("Unable to read bounded workflow context") from None
        if failures:
            facts.append("Bounded failed workflow runs: " + "; ".join(
                f"run={run.id}, workflow={run.workflow_id}, failure_code={run.failure_code or 'not_recorded'}"
                for run in failures
            ))
    if route.primary_role == "support":
        await _append_selected_support_context(
            session, business_id=business_id,
            context_references=context_references, facts=facts,
        )
    if route.intent == "scheduling_lookup":
        appointment_type_id = next((UUID(item["id"]) for item in context_references if item["type"] == "appointment_type"), None)
        provider_id = next((UUID(item["id"]) for item in context_references if item["type"] == "provider"), None)
        if appointment_type_id:
            from app.services.scheduling import find_next_available_slots
            from app.exceptions.scheduling import (
                SchedulingNotFoundError,
                SchedulingPersistenceError,
                SchedulingValidationError,
            )
            try:
                slots = await find_next_available_slots(
                    session, business_id=business_id, appointment_type_id=appointment_type_id,
                    provider_id=provider_id, starts_after=now, desired_results=5,
                    search_days=30, now=now,
                )
            except (SchedulingNotFoundError, SchedulingValidationError):
                raise AIWorkforceValidationError("Invalid scheduling context reference") from None
            except SchedulingPersistenceError:
                raise AIWorkforcePersistenceError("Unable to read scheduling availability") from None
            if slots:
                facts.append("Authoritative available slots: " + "; ".join(
                    f"{slot.provider_display_name} at {slot.starts_at.isoformat()} ({slot.timezone})"
                    for slot in slots
                ))
            else:
                facts.append("No authoritative available slots were found in the requested horizon.")
        else:
            facts.append("No appointment_type context reference was supplied, so no availability was inferred.")
    return "\n".join(facts)[:8_000]


async def _append_selected_support_context(
    session: AsyncSession, *, business_id: UUID,
    context_references: list[dict[str, str]], facts: list[str],
) -> None:
    """Load only explicitly referenced support records, with bounded message text."""
    try:
        for reference in context_references:
            reference_id = UUID(reference["id"])
            if reference["type"] == "customer":
                customer = await session.scalar(select(Customer).where(
                    Customer.business_id == business_id, Customer.id == reference_id
                ))
                if customer:
                    facts.append(
                        f"Selected customer: {customer.display_name} [{customer.id}], "
                        f"status={customer.status}, company={customer.company or 'not_recorded'}."
                    )
            elif reference["type"] == "order":
                order = await session.scalar(select(Order).where(
                    Order.business_id == business_id, Order.id == reference_id
                ))
                if order:
                    facts.append(
                        f"Selected order: {order.order_number} [{order.id}], status={order.status}, "
                        f"total={order.total} {order.currency}."
                    )
            elif reference["type"] == "conversation":
                conversation = await session.scalar(select(Conversation).where(
                    Conversation.business_id == business_id,
                    Conversation.id == reference_id,
                ))
                if conversation:
                    messages = list((await session.scalars(select(ConversationMessage).where(
                        ConversationMessage.business_id == business_id,
                        ConversationMessage.conversation_id == conversation.id,
                    ).order_by(
                        ConversationMessage.sent_at.desc(), ConversationMessage.id.desc()
                    ).limit(5))).all())
                    facts.append(
                        f"Selected conversation [{conversation.id}] channel={conversation.channel}, "
                        f"status={conversation.status}. Recent bounded messages: "
                        + " | ".join(
                            f"{message.direction}: {' '.join(message.content.split())[:500]}"
                            for message in reversed(messages)
                        )
                    )
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to read bounded support context") from None


async def daily_brief(session: AsyncSession, *, business_id: UUID) -> DailyBriefResponse:
    route = route_command("What should I focus on today?")
    context = await build_operational_context(session, business_id=business_id, route=route, context_references=[])
    facts = [line.rstrip(".") for line in context.splitlines()]
    sections = [
        DailyBriefSection(key="business", title="Business overview", facts=facts[:2]),
        DailyBriefSection(key="sales", title="Sales and CRM", facts=facts[2:5]),
        DailyBriefSection(key="operations", title="Operations and scheduling", facts=facts[5:8]),
        DailyBriefSection(key="governance", title="Approvals and opportunities", facts=facts[8:10]),
        DailyBriefSection(key="marketing", title="Marketing and reports", facts=facts[10:]),
    ]
    priorities = []
    suggestions = await suggested_commands(session, business_id=business_id)
    priorities.extend(item.command for item in suggestions[:3])
    if not priorities:
        priorities.append("Review the current business overview")
    return DailyBriefResponse(generated_at=datetime.now(UTC), sections=sections, recommended_priorities=priorities)


async def suggested_commands(session: AsyncSession, *, business_id: UUID) -> list[SuggestedCommandResponse]:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        overdue = await _count(session, CRMLead, business_id, CRMLead.next_follow_up_at < now, CRMLead.stage.notin_(("won", "lost")))
        approvals = await _count(session, ApprovalRequest, business_id, ApprovalRequest.status == "pending")
        appointments = await _count(session, Appointment, business_id, Appointment.status == "confirmed", Appointment.starts_at >= tomorrow, Appointment.starts_at < tomorrow + timedelta(days=1))
        failures = await _count(session, AutomationWorkflowRun, business_id, AutomationWorkflowRun.status == "failed")
        campaigns = await _count(session, Campaign, business_id)
    except SQLAlchemyError:
        raise AIWorkforcePersistenceError("Unable to generate command suggestions") from None
    values: list[SuggestedCommandResponse] = []
    if overdue:
        values.append(SuggestedCommandResponse(command="Show leads needing follow-up", reason=f"{overdue} overdue follow-ups are recorded.", role="sales"))
    if approvals:
        values.append(SuggestedCommandResponse(command="Review pending AI actions", reason=f"{approvals} approvals are pending.", role="business_manager"))
    if appointments:
        values.append(SuggestedCommandResponse(command="Show tomorrow's schedule", reason=f"{appointments} appointments are confirmed tomorrow.", role="operations"))
    if failures:
        values.append(SuggestedCommandResponse(command="Analyze workflow failures", reason=f"{failures} failed workflow runs are recorded.", role="operations"))
    if campaigns:
        values.append(SuggestedCommandResponse(command="Summarize campaign performance", reason="Marketing campaign data is available.", role="cmo"))
    if not values:
        values.append(SuggestedCommandResponse(command="Give me a business overview", reason="Start with the current available business data.", role="business_manager"))
    return values[:6]


async def create_agent_notification(
    session: AsyncSession, *, business_id: UUID, user_id: UUID | None,
    category: str, title: str, message: str, entity_type: str, entity_id: UUID,
    priority: str = "medium",
) -> None:
    session.add(Notification(
        business_id=business_id, recipient_user_id=user_id, category=category,
        title=title[:180], message=message[:1_000], priority=priority, read=False,
        related_entity_type=entity_type, related_entity_id=entity_id,
    ))


async def _count(session: AsyncSession, model: type, business_id: UUID, *conditions: object) -> int:
    return int(await session.scalar(select(func.count(model.id)).where(model.business_id == business_id, *conditions)) or 0)
