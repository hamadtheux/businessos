from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.provider import AIAgentProvider, get_agent_provider_model_name
from app.agents.runtime import execute_ai_agent_with_metadata
from app.domain.automation import RETRYABLE_FAILURE_CODES, RUN_CANCELABLE_STATUSES, WORKFLOW_TRANSITIONS
from app.exceptions.automation import (
    AutomationConflictError,
    AutomationNotFoundError,
    AutomationPersistenceError,
    AutomationStateError,
    AutomationValidationError,
)
from app.exceptions.ai_agent import AIAgentError
from app.models.ai_agent_execution import AIAgentExecution
from app.models.approval_request import ApprovalRequest
from app.models.automation import (
    AutomationEdge,
    AutomationEvent,
    AutomationNode,
    AutomationNodeRun,
    AutomationWorkflow,
    AutomationWorkflowRun,
    AutomationWorkflowVersion,
)
from app.schemas.ai_agent import AIAgentExecutionRequest
from app.services.billing import require_capacity, require_feature
from app.schemas.automation import (
    AINodeConfig,
    ApprovalNodeConfig,
    DelayNodeConfig,
    EdgeCreate,
    EdgeUpdate,
    EndNodeConfig,
    ExternalActionNodeConfig,
    InternalOperationNodeConfig,
    NODE_CONFIGURATION_ADAPTER,
    NodeCreate,
    NodeUpdate,
    ScheduleDefinition,
    SimulationRequest,
    WorkflowCreate,
    WorkflowUpdate,
)
from app.schemas.operations import (
    CustomerUpdate,
    NotificationCreate,
    OpportunityCreate,
    ReportGenerateRequest,
)
from app.services.action_governance import govern_materialized_ai_actions
from app.services.ai_action import materialize_ai_actions
from app.services.ai_agent_execution import (
    create_running_ai_agent_execution,
    finalize_successful_ai_agent_execution,
)
from app.services.ai_capabilities import (
    validate_proposed_action_capabilities,
    validate_role_capabilities,
)
from app.services.ai_workforce import get_agent_config
from app.services.approval import create_workflow_approval_request
from app.services.automation_graph import (
    evaluate_condition,
    next_node_key,
    parse_condition,
    validate_graph,
    validate_node_configuration,
)
from app.services.marketing import change_campaign_status
from app.services.operations import (
    change_lead_state,
    create_notification,
    create_opportunity,
    generate_report,
    get_customer,
    record_audit,
    update_customer,
)


MAX_PAGE_SIZE = 100
MAX_RUN_STEPS = 200


async def create_workflow(
    session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: WorkflowCreate
) -> AutomationWorkflow:
    _validate_trigger_and_schedule(data.trigger_type, data.timezone, data.schedule_definition)
    value = AutomationWorkflow(
        business_id=business_id,
        name=data.name.strip(),
        description=data.description,
        status="draft",
        current_version=1,
        trigger_type=data.trigger_type,
        enabled=False,
        timezone=data.timezone,
        schedule_definition=data.schedule_definition.model_dump(mode="json") if data.schedule_definition else {},
        next_run_at=_next_schedule(data.schedule_definition, data.timezone) if data.schedule_definition else None,
        created_by_user_id=actor_user_id,
    )
    session.add(value)
    await _flush(session)
    session.add(AutomationWorkflowVersion(
        business_id=business_id, workflow_id=value.id, version=1, created_by_user_id=actor_user_id
    ))
    await _flush(session)
    _audit(session, value, actor_user_id, "automation.workflow_created", "Created workflow.")
    return value


async def get_workflow(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID, for_update: bool = False
) -> AutomationWorkflow:
    statement = select(AutomationWorkflow).where(
        AutomationWorkflow.id == workflow_id, AutomationWorkflow.business_id == business_id
    )
    if for_update:
        statement = statement.with_for_update()
    try:
        value = await session.scalar(statement)
    except SQLAlchemyError:
        raise AutomationPersistenceError("Unable to read workflow") from None
    if value is None:
        raise AutomationNotFoundError("Workflow not found")
    return value


async def list_workflows(
    session: AsyncSession, *, business_id: UUID, page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    _validate_page(page, page_size)
    latest = select(
        AutomationWorkflowRun.workflow_id.label("workflow_id"),
        AutomationWorkflowRun.status.label("last_run_status"),
        AutomationWorkflowRun.created_at.label("last_run_at"),
        func.row_number().over(
            partition_by=AutomationWorkflowRun.workflow_id,
            order_by=(AutomationWorkflowRun.created_at.desc(), AutomationWorkflowRun.id.desc()),
        ).label("row_number"),
    ).where(AutomationWorkflowRun.business_id == business_id).subquery()
    try:
        total = int(await session.scalar(select(func.count()).select_from(AutomationWorkflow).where(
            AutomationWorkflow.business_id == business_id
        )) or 0)
        rows = (await session.execute(
            select(AutomationWorkflow, latest.c.last_run_status, latest.c.last_run_at)
            .outerjoin(latest, (latest.c.workflow_id == AutomationWorkflow.id) & (latest.c.row_number == 1))
            .where(AutomationWorkflow.business_id == business_id)
            .order_by(AutomationWorkflow.updated_at.desc(), AutomationWorkflow.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).all()
    except SQLAlchemyError:
        raise AutomationPersistenceError("Unable to list workflows") from None
    return [
        {**_columns(workflow), "last_run_status": status, "last_run_at": run_at}
        for workflow, status, run_at in rows
    ], total


async def workflow_detail(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID
) -> dict[str, Any]:
    workflow = await get_workflow(session, business_id=business_id, workflow_id=workflow_id)
    version, nodes, edges = await load_graph(session, workflow=workflow)
    return {**_columns(workflow), "last_run_status": None, "last_run_at": None,
            "version_id": version.id, "nodes": nodes, "edges": edges}


async def update_workflow(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID, actor_user_id: UUID, data: WorkflowUpdate
) -> AutomationWorkflow:
    workflow = await get_workflow(session, business_id=business_id, workflow_id=workflow_id, for_update=True)
    if workflow.status == "archived":
        raise AutomationStateError("Archived workflow cannot be edited")
    values = data.model_dump(exclude_unset=True)
    schedule = data.schedule_definition if "schedule_definition" in values else (
        ScheduleDefinition.model_validate(workflow.schedule_definition) if workflow.schedule_definition else None
    )
    trigger = data.trigger_type or workflow.trigger_type
    timezone = data.timezone or workflow.timezone
    _validate_trigger_and_schedule(trigger, timezone, schedule)
    if "trigger_type" in values and trigger != workflow.trigger_type:
        version = await _clone_current_version(session, workflow=workflow, actor_user_id=actor_user_id)
        trigger_node = await session.scalar(select(AutomationNode).where(
            AutomationNode.business_id == business_id,
            AutomationNode.workflow_version_id == version.id,
            AutomationNode.node_type == "trigger",
        ))
        if trigger_node is not None:
            trigger_node.configuration = {"kind": "trigger", "trigger_type": trigger}
    for field in ("name", "description", "trigger_type", "timezone"):
        if field in values:
            setattr(workflow, field, values[field])
    if "schedule_definition" in values:
        workflow.schedule_definition = schedule.model_dump(mode="json") if schedule else {}
    workflow.next_run_at = _next_schedule(schedule, timezone) if schedule else None
    await _flush(session)
    _audit(session, workflow, actor_user_id, "automation.workflow_changed", "Changed workflow definition.")
    return workflow


async def duplicate_workflow(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID, actor_user_id: UUID
) -> AutomationWorkflow:
    source = await get_workflow(session, business_id=business_id, workflow_id=workflow_id)
    source_version, nodes, edges = await load_graph(session, workflow=source)
    copy = await create_workflow(session, business_id=business_id, actor_user_id=actor_user_id, data=WorkflowCreate(
        name=f"{source.name} copy"[:180], description=source.description,
        trigger_type=source.trigger_type, timezone=source.timezone,
        schedule_definition=ScheduleDefinition.model_validate(source.schedule_definition) if source.schedule_definition else None,
    ))
    target_version = await _current_version(session, copy)
    for node in nodes:
        session.add(AutomationNode(
            business_id=business_id, workflow_id=copy.id, workflow_version_id=target_version.id,
            node_key=node.node_key, node_type=node.node_type, name=node.name,
            configuration=node.configuration, position_x=node.position_x, position_y=node.position_y,
            order_index=node.order_index,
        ))
    await _flush(session)
    for edge in edges:
        session.add(AutomationEdge(
            business_id=business_id, workflow_id=copy.id, workflow_version_id=target_version.id,
            edge_key=edge.edge_key, source_node_key=edge.source_node_key,
            target_node_key=edge.target_node_key, branch_label=edge.branch_label, order_index=edge.order_index,
        ))
    await _flush(session)
    _audit(session, copy, actor_user_id, "automation.workflow_duplicated", "Duplicated workflow without run history.")
    return copy


async def transition_workflow(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID, actor_user_id: UUID, target_status: str
) -> AutomationWorkflow:
    workflow = await get_workflow(session, business_id=business_id, workflow_id=workflow_id, for_update=True)
    if target_status == workflow.status:
        return workflow
    if target_status not in WORKFLOW_TRANSITIONS.get(workflow.status, frozenset()):
        raise AutomationStateError("Workflow transition is invalid")
    if target_status == "active":
        if isinstance(session, AsyncSession):
            await require_feature(session, business_id=business_id, key="automations")
            await require_capacity(session, business_id=business_id, key="max_active_workflows")
        _, nodes, edges = await load_graph(session, workflow=workflow)
        errors = validate_graph(nodes, edges)
        if errors:
            raise AutomationValidationError(",".join(errors))
    workflow.status = target_status
    workflow.enabled = target_status == "active"
    await _flush(session)
    event = {"active": "activated", "paused": "paused", "archived": "archived"}[target_status]
    _audit(session, workflow, actor_user_id, f"automation.workflow_{event}", f"Workflow {event}.")
    return workflow


async def create_node(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID, actor_user_id: UUID, data: NodeCreate
) -> AutomationNode:
    workflow = await get_workflow(session, business_id=business_id, workflow_id=workflow_id, for_update=True)
    config = validate_node_configuration(data.node_type, data.configuration, workflow_trigger_type=workflow.trigger_type if data.node_type == "trigger" else None)
    version = await _clone_current_version(session, workflow=workflow, actor_user_id=actor_user_id)
    node = AutomationNode(
        business_id=business_id, workflow_id=workflow_id, workflow_version_id=version.id,
        node_key=data.node_key or uuid4(), node_type=data.node_type, name=data.name.strip(),
        configuration=config, position_x=data.position_x, position_y=data.position_y, order_index=data.order_index,
    )
    session.add(node)
    await _flush(session)
    _audit(session, workflow, actor_user_id, "automation.workflow_changed", "Added workflow node.")
    return node


async def update_node(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID, node_key: UUID,
    actor_user_id: UUID, data: NodeUpdate
) -> AutomationNode:
    workflow = await get_workflow(session, business_id=business_id, workflow_id=workflow_id, for_update=True)
    version = await _clone_current_version(session, workflow=workflow, actor_user_id=actor_user_id)
    node = await _node_by_key(session, business_id, version.id, node_key)
    values = data.model_dump(exclude_unset=True)
    node_type = values.get("node_type", node.node_type)
    config = values.get("configuration", node.configuration)
    node.configuration = validate_node_configuration(
        node_type, config, workflow_trigger_type=workflow.trigger_type if node_type == "trigger" else None
    )
    node.node_type = node_type
    for field in ("name", "position_x", "position_y", "order_index"):
        if field in values:
            setattr(node, field, values[field])
    await _flush(session)
    _audit(session, workflow, actor_user_id, "automation.workflow_changed", "Updated workflow node.")
    return node


async def delete_node(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID, node_key: UUID, actor_user_id: UUID
) -> None:
    workflow = await get_workflow(session, business_id=business_id, workflow_id=workflow_id, for_update=True)
    version = await _clone_current_version(session, workflow=workflow, actor_user_id=actor_user_id)
    node = await _node_by_key(session, business_id, version.id, node_key)
    await session.delete(node)
    await _flush(session)
    _audit(session, workflow, actor_user_id, "automation.workflow_changed", "Deleted workflow node and connected edges.")


async def create_edge(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID, actor_user_id: UUID, data: EdgeCreate
) -> AutomationEdge:
    workflow = await get_workflow(session, business_id=business_id, workflow_id=workflow_id, for_update=True)
    version = await _clone_current_version(session, workflow=workflow, actor_user_id=actor_user_id)
    await _node_by_key(session, business_id, version.id, data.source_node_key)
    await _node_by_key(session, business_id, version.id, data.target_node_key)
    branch_clause = (
        AutomationEdge.branch_label.is_(None)
        if data.branch_label is None
        else AutomationEdge.branch_label == data.branch_label
    )
    duplicate = await session.scalar(select(AutomationEdge.id).where(
        AutomationEdge.business_id == business_id,
        AutomationEdge.workflow_version_id == version.id,
        AutomationEdge.source_node_key == data.source_node_key,
        branch_clause,
    ))
    if duplicate is not None:
        raise AutomationConflictError("Workflow route already exists")
    edge = AutomationEdge(
        business_id=business_id, workflow_id=workflow_id, workflow_version_id=version.id,
        edge_key=uuid4(), source_node_key=data.source_node_key, target_node_key=data.target_node_key,
        branch_label=data.branch_label, order_index=data.order_index,
    )
    session.add(edge)
    await _flush(session)
    _audit(session, workflow, actor_user_id, "automation.workflow_changed", "Added workflow edge.")
    return edge


async def update_edge(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID, edge_key: UUID,
    actor_user_id: UUID, data: EdgeUpdate
) -> AutomationEdge:
    workflow = await get_workflow(session, business_id=business_id, workflow_id=workflow_id, for_update=True)
    version = await _clone_current_version(session, workflow=workflow, actor_user_id=actor_user_id)
    edge = await _edge_by_key(session, business_id, version.id, edge_key)
    values = data.model_dump(exclude_unset=True)
    if "target_node_key" in values:
        await _node_by_key(session, business_id, version.id, values["target_node_key"])
    resulting_label = values.get("branch_label", edge.branch_label)
    branch_clause = (
        AutomationEdge.branch_label.is_(None)
        if resulting_label is None
        else AutomationEdge.branch_label == resulting_label
    )
    duplicate = await session.scalar(select(AutomationEdge.id).where(
        AutomationEdge.business_id == business_id,
        AutomationEdge.workflow_version_id == version.id,
        AutomationEdge.source_node_key == edge.source_node_key,
        AutomationEdge.id != edge.id,
        branch_clause,
    ))
    if duplicate is not None:
        raise AutomationConflictError("Workflow route already exists")
    for field, value in values.items():
        setattr(edge, field, value)
    await _flush(session)
    _audit(session, workflow, actor_user_id, "automation.workflow_changed", "Updated workflow edge.")
    return edge


async def delete_edge(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID, edge_key: UUID, actor_user_id: UUID
) -> None:
    workflow = await get_workflow(session, business_id=business_id, workflow_id=workflow_id, for_update=True)
    version = await _clone_current_version(session, workflow=workflow, actor_user_id=actor_user_id)
    edge = await _edge_by_key(session, business_id, version.id, edge_key)
    await session.delete(edge)
    await _flush(session)
    _audit(session, workflow, actor_user_id, "automation.workflow_changed", "Deleted workflow edge.")


async def validate_workflow_graph(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID
) -> list[str]:
    workflow = await get_workflow(session, business_id=business_id, workflow_id=workflow_id)
    _, nodes, edges = await load_graph(session, workflow=workflow)
    return validate_graph(nodes, edges)


async def load_graph(
    session: AsyncSession, *, workflow: AutomationWorkflow
) -> tuple[AutomationWorkflowVersion, list[AutomationNode], list[AutomationEdge]]:
    version = await _current_version(session, workflow)
    try:
        nodes = list((await session.scalars(select(AutomationNode).where(
            AutomationNode.business_id == workflow.business_id,
            AutomationNode.workflow_id == workflow.id,
            AutomationNode.workflow_version_id == version.id,
        ).order_by(AutomationNode.order_index, AutomationNode.id))).all())
        edges = list((await session.scalars(select(AutomationEdge).where(
            AutomationEdge.business_id == workflow.business_id,
            AutomationEdge.workflow_id == workflow.id,
            AutomationEdge.workflow_version_id == version.id,
        ).order_by(AutomationEdge.order_index, AutomationEdge.id))).all())
    except SQLAlchemyError:
        raise AutomationPersistenceError("Unable to load workflow graph") from None
    return version, nodes, edges


async def _clone_current_version(
    session: AsyncSession, *, workflow: AutomationWorkflow, actor_user_id: UUID
) -> AutomationWorkflowVersion:
    if workflow.status == "archived":
        raise AutomationStateError("Archived workflow cannot be edited")
    previous, nodes, edges = await load_graph(session, workflow=workflow)
    version = AutomationWorkflowVersion(
        business_id=workflow.business_id, workflow_id=workflow.id,
        version=workflow.current_version + 1, created_by_user_id=actor_user_id,
    )
    session.add(version)
    await _flush(session)
    for node in nodes:
        session.add(AutomationNode(
            business_id=workflow.business_id, workflow_id=workflow.id, workflow_version_id=version.id,
            node_key=node.node_key, node_type=node.node_type, name=node.name,
            configuration=node.configuration, position_x=node.position_x,
            position_y=node.position_y, order_index=node.order_index,
        ))
    await _flush(session)
    for edge in edges:
        session.add(AutomationEdge(
            business_id=workflow.business_id, workflow_id=workflow.id, workflow_version_id=version.id,
            edge_key=edge.edge_key, source_node_key=edge.source_node_key,
            target_node_key=edge.target_node_key, branch_label=edge.branch_label, order_index=edge.order_index,
        ))
    workflow.current_version = version.version
    if workflow.status == "active":
        workflow.status = "paused"
        workflow.enabled = False
    await _flush(session)
    return version


async def _current_version(session: AsyncSession, workflow: AutomationWorkflow) -> AutomationWorkflowVersion:
    try:
        value = await session.scalar(select(AutomationWorkflowVersion).where(
            AutomationWorkflowVersion.business_id == workflow.business_id,
            AutomationWorkflowVersion.workflow_id == workflow.id,
            AutomationWorkflowVersion.version == workflow.current_version,
        ))
    except SQLAlchemyError:
        raise AutomationPersistenceError("Unable to load workflow version") from None
    if value is None:
        raise AutomationConflictError("Workflow version is missing")
    return value


async def _node_by_key(session: AsyncSession, business_id: UUID, version_id: UUID, node_key: UUID) -> AutomationNode:
    value = await session.scalar(select(AutomationNode).where(
        AutomationNode.business_id == business_id,
        AutomationNode.workflow_version_id == version_id,
        AutomationNode.node_key == node_key,
    ))
    if value is None:
        raise AutomationNotFoundError("Workflow node not found")
    return value


async def _edge_by_key(session: AsyncSession, business_id: UUID, version_id: UUID, edge_key: UUID) -> AutomationEdge:
    value = await session.scalar(select(AutomationEdge).where(
        AutomationEdge.business_id == business_id,
        AutomationEdge.workflow_version_id == version_id,
        AutomationEdge.edge_key == edge_key,
    ))
    if value is None:
        raise AutomationNotFoundError("Workflow edge not found")
    return value


def _validate_trigger_and_schedule(trigger_type: str, timezone_name: str, schedule: ScheduleDefinition | None) -> None:
    from app.domain.automation import TRIGGER_TYPES
    if trigger_type not in TRIGGER_TYPES:
        raise AutomationValidationError("trigger_type_invalid")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise AutomationValidationError("timezone_invalid") from None
    if trigger_type == "scheduled_time" and schedule is None:
        raise AutomationValidationError("schedule_required")
    if trigger_type != "scheduled_time" and schedule is not None:
        raise AutomationValidationError("schedule_not_allowed")


def _next_schedule(schedule: ScheduleDefinition, timezone_name: str, now: datetime | None = None) -> datetime | None:
    instant = now or datetime.now(UTC)
    zone = ZoneInfo(timezone_name)
    local_now = instant.astimezone(zone)
    if schedule.frequency == "one_time":
        return schedule.at.astimezone(UTC) if schedule.at and schedule.at > instant else None
    hour, minute = (int(part) for part in (schedule.at_time or "00:00").split(":"))
    candidate = datetime.combine(local_now.date(), time(hour, minute), zone)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    if schedule.frequency == "weekday":
        while candidate.weekday() > 4:
            candidate += timedelta(days=1)
    elif schedule.frequency == "weekly":
        candidate += timedelta(days=(int(schedule.weekday) - candidate.weekday()) % 7)
        if candidate <= local_now:
            candidate += timedelta(days=7)
    elif schedule.frequency == "monthly":
        day = int(schedule.day_of_month)
        candidate = candidate.replace(day=day)
        if candidate <= local_now:
            year, month = (candidate.year + 1, 1) if candidate.month == 12 else (candidate.year, candidate.month + 1)
            candidate = candidate.replace(year=year, month=month, day=day)
    return candidate.astimezone(UTC)


def _validate_page(page: int, page_size: int) -> None:
    if page < 1 or page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise AutomationValidationError("pagination_invalid")


async def _flush(session: AsyncSession) -> None:
    try:
        await session.flush()
    except IntegrityError:
        raise AutomationConflictError("Automation data conflicts") from None
    except SQLAlchemyError:
        raise AutomationPersistenceError("Unable to persist automation data") from None


def _audit(session: AsyncSession, workflow: AutomationWorkflow, actor_user_id: UUID | None, event: str, summary: str) -> None:
    record_audit(
        session, business_id=workflow.business_id, actor_user_id=actor_user_id,
        event_type=event, entity_type="automation_workflow", entity_id=workflow.id, summary=summary,
    )


def _columns(value: Any) -> dict[str, Any]:
    return {column.name: getattr(value, column.name) for column in value.__table__.columns}


async def create_workflow_run(
    session: AsyncSession,
    *,
    business_id: UUID,
    workflow_id: UUID,
    trigger_type: str,
    context_payload: dict[str, Any],
    requested_by_user_id: UUID | None,
    trigger_event_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> AutomationWorkflowRun:
    if isinstance(session, AsyncSession):
        await require_feature(session, business_id=business_id, key="automations")
        await require_capacity(session, business_id=business_id, key="max_automation_runs_month")
    workflow = await get_workflow(session, business_id=business_id, workflow_id=workflow_id)
    if workflow.status != "active" or not workflow.enabled:
        raise AutomationStateError("Workflow is not active")
    version, nodes, edges = await load_graph(session, workflow=workflow)
    errors = validate_graph(nodes, edges)
    if errors:
        raise AutomationValidationError(",".join(errors))
    if trigger_type not in {"event", "schedule", "manual"}:
        raise AutomationValidationError("run_trigger_invalid")
    existing = None
    if trigger_event_id is not None:
        existing = await session.scalar(select(AutomationWorkflowRun).where(
            AutomationWorkflowRun.business_id == business_id,
            AutomationWorkflowRun.workflow_version_id == version.id,
            AutomationWorkflowRun.trigger_event_id == trigger_event_id,
        ))
    elif idempotency_key:
        existing = await session.scalar(select(AutomationWorkflowRun).where(
            AutomationWorkflowRun.business_id == business_id,
            AutomationWorkflowRun.idempotency_key == idempotency_key,
        ))
    if existing is not None:
        return existing
    entry = next(node for node in nodes if node.node_type == "trigger")
    run = AutomationWorkflowRun(
        business_id=business_id, workflow_id=workflow_id, workflow_version_id=version.id,
        trigger_event_id=trigger_event_id, trigger_type=trigger_type, status="queued",
        context_payload=_bounded_context(context_payload), current_node_key=entry.node_key,
        waiting_reason=None, idempotency_key=idempotency_key, started_at=None,
        completed_at=None, failure_code=None, requested_by_user_id=requested_by_user_id,
    )
    session.add(run)
    await _flush(session)
    return run


async def get_workflow_run(
    session: AsyncSession, *, business_id: UUID, run_id: UUID, for_update: bool = False
) -> AutomationWorkflowRun:
    statement = select(AutomationWorkflowRun).where(
        AutomationWorkflowRun.id == run_id, AutomationWorkflowRun.business_id == business_id
    )
    if for_update:
        statement = statement.with_for_update()
    value = await session.scalar(statement)
    if value is None:
        raise AutomationNotFoundError("Workflow run not found")
    return value


async def list_workflow_runs(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID | None,
    page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    _validate_page(page, page_size)
    where = [AutomationWorkflowRun.business_id == business_id]
    if workflow_id:
        where.append(AutomationWorkflowRun.workflow_id == workflow_id)
    total = int(await session.scalar(select(func.count()).select_from(AutomationWorkflowRun).where(*where)) or 0)
    rows = (await session.execute(
        select(AutomationWorkflowRun, AutomationWorkflow.name, AutomationWorkflowVersion.version)
        .join(AutomationWorkflow, (AutomationWorkflow.id == AutomationWorkflowRun.workflow_id) &
              (AutomationWorkflow.business_id == AutomationWorkflowRun.business_id))
        .join(AutomationWorkflowVersion, (AutomationWorkflowVersion.id == AutomationWorkflowRun.workflow_version_id) &
              (AutomationWorkflowVersion.business_id == AutomationWorkflowRun.business_id))
        .where(*where).order_by(AutomationWorkflowRun.created_at.desc(), AutomationWorkflowRun.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).all()
    return [{**_columns(run), "workflow_name": name, "version": version} for run, name, version in rows], total


async def list_node_runs(
    session: AsyncSession, *, business_id: UUID, run_id: UUID, page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    _validate_page(page, page_size)
    await get_workflow_run(session, business_id=business_id, run_id=run_id)
    where = [AutomationNodeRun.business_id == business_id, AutomationNodeRun.workflow_run_id == run_id]
    total = int(await session.scalar(select(func.count()).select_from(AutomationNodeRun).where(*where)) or 0)
    rows = (await session.execute(
        select(AutomationNodeRun, AutomationNode.name, AutomationNode.node_type)
        .join(AutomationNode, (AutomationNode.workflow_version_id == AutomationNodeRun.workflow_version_id) &
              (AutomationNode.node_key == AutomationNodeRun.node_key) &
              (AutomationNode.business_id == AutomationNodeRun.business_id))
        .where(*where).order_by(AutomationNodeRun.created_at, AutomationNodeRun.id)
        .offset((page - 1) * page_size).limit(page_size)
    )).all()
    return [{**_columns(item), "node_name": name, "node_type": node_type} for item, name, node_type in rows], total


async def cancel_workflow_run(
    session: AsyncSession, *, business_id: UUID, run_id: UUID, actor_user_id: UUID
) -> AutomationWorkflowRun:
    run = await get_workflow_run(session, business_id=business_id, run_id=run_id, for_update=True)
    if run.status == "canceled":
        return run
    if run.status not in RUN_CANCELABLE_STATUSES:
        raise AutomationStateError("Workflow run cannot be canceled")
    now = datetime.now(UTC)
    run.status, run.completed_at, run.waiting_reason = "canceled", now, None
    waiting = await session.scalar(select(AutomationNodeRun).where(
        AutomationNodeRun.business_id == business_id,
        AutomationNodeRun.workflow_run_id == run_id,
        AutomationNodeRun.status == "waiting",
    ).with_for_update())
    if waiting is not None:
        waiting.status, waiting.completed_at = "canceled", now
        approval = await session.scalar(select(ApprovalRequest).where(
            ApprovalRequest.business_id == business_id,
            ApprovalRequest.workflow_node_run_id == waiting.id,
            ApprovalRequest.status == "pending",
        ))
        if approval is not None:
            approval.status, approval.decided_at = "canceled", now
    workflow = await get_workflow(session, business_id=business_id, workflow_id=run.workflow_id)
    _audit(session, workflow, actor_user_id, "automation.workflow_run_canceled", "Canceled queued or waiting workflow run.")
    await _flush(session)
    return run


async def advance_workflow_run(
    session: AsyncSession, *, business_id: UUID, run_id: UUID,
    actor_user_id: UUID | None, provider: AIAgentProvider | None = None,
    now: datetime | None = None,
) -> AutomationWorkflowRun:
    instant = now or datetime.now(UTC)
    run = await get_workflow_run(session, business_id=business_id, run_id=run_id, for_update=True)
    if run.status in {"succeeded", "failed", "canceled"}:
        return run
    nodes = list((await session.scalars(select(AutomationNode).where(
        AutomationNode.business_id == business_id,
        AutomationNode.workflow_version_id == run.workflow_version_id,
    ))).all())
    edges = list((await session.scalars(select(AutomationEdge).where(
        AutomationEdge.business_id == business_id,
        AutomationEdge.workflow_version_id == run.workflow_version_id,
    ))).all())
    node_map = {node.node_key: node for node in nodes}
    if run.current_node_key not in node_map:
        return await _fail_run(session, run, "graph_invalid", actor_user_id)
    if run.status == "waiting":
        ready = await _resume_waiting_node(session, run=run, node_map=node_map, edges=edges, instant=instant)
        if not ready:
            return run
    if run.started_at is None:
        run.started_at = instant
    run.status, run.waiting_reason = "running", None
    for _ in range(MAX_RUN_STEPS):
        node = node_map.get(run.current_node_key)
        if node is None:
            return await _fail_run(session, run, "graph_invalid", actor_user_id)
        attempt = int(await session.scalar(select(func.count()).select_from(AutomationNodeRun).where(
            AutomationNodeRun.business_id == business_id,
            AutomationNodeRun.workflow_run_id == run.id,
            AutomationNodeRun.node_key == node.node_key,
        )) or 0) + 1
        node_run = AutomationNodeRun(
            business_id=business_id, workflow_version_id=run.workflow_version_id,
            workflow_run_id=run.id, node_key=node.node_key, status="running",
            attempt=attempt, started_at=instant, completed_at=None, branch_outcome=None,
            result_summary=None, failure_code=None, resume_at=None, action_id=None,
        )
        session.add(node_run)
        await _flush(session)
        try:
            outcome = await _execute_node(
                session, run=run, node=node, node_run=node_run, actor_user_id=actor_user_id,
                provider=provider, instant=instant,
            )
        except AIAgentError:
            node_run.status, node_run.completed_at, node_run.failure_code = "failed", instant, "ai_temporarily_unavailable"
            return await _fail_run(session, run, "ai_temporarily_unavailable", actor_user_id)
        except AutomationValidationError as exc:
            code = str(exc) if str(exc) in RETRYABLE_FAILURE_CODES | {"condition_input_missing", "node_configuration_invalid"} else "node_configuration_invalid"
            config = NODE_CONFIGURATION_ADAPTER.validate_python(node.configuration)
            max_attempts = config.max_attempts if isinstance(config, InternalOperationNodeConfig) else 1
            node_run.status, node_run.completed_at, node_run.failure_code = "failed", instant, code
            if code in RETRYABLE_FAILURE_CODES and attempt < max_attempts:
                node_run.resume_at = instant + timedelta(seconds=config.retry_delay_seconds)
                run.status, run.waiting_reason, run.current_node_key = "waiting", "retry", node.node_key
                await _flush(session)
                return run
            return await _fail_run(session, run, "retry_exhausted" if attempt >= max_attempts and max_attempts > 1 else code, actor_user_id)
        except Exception:
            node_run.status, node_run.completed_at, node_run.failure_code = "failed", instant, "internal_operation_failed"
            return await _fail_run(session, run, "internal_operation_failed", actor_user_id)
        if outcome == "waiting":
            await _flush(session)
            return run
        if node.node_type == "end":
            run.status, run.completed_at, run.current_node_key = "succeeded", instant, node.node_key
            await _flush(session)
            return run
        run.current_node_key = next_node_key(node, edges, outcome=node_run.branch_outcome)
        await _flush(session)
    return await _fail_run(session, run, "graph_invalid", actor_user_id)


async def _execute_node(
    session: AsyncSession, *, run: AutomationWorkflowRun, node: AutomationNode,
    node_run: AutomationNodeRun, actor_user_id: UUID | None,
    provider: AIAgentProvider | None, instant: datetime,
) -> str:
    config = NODE_CONFIGURATION_ADAPTER.validate_python(node.configuration)
    if node.node_type == "end":
        parsed = config
        node_run.status, node_run.completed_at = "succeeded", instant
        node_run.result_summary = "Workflow completed." if isinstance(parsed, EndNodeConfig) and parsed.outcome == "success" else "Workflow terminal reached."
        return "done"
    if node.node_type == "trigger":
        node_run.status, node_run.completed_at, node_run.result_summary = "succeeded", instant, "Trigger accepted."
        return "done"
    if node.node_type in {"condition", "branch"}:
        result = evaluate_condition(parse_condition(node), run.context_payload)
        outcome = "true" if result else "false"
        if hasattr(config, "true_label"):
            outcome = config.true_label if result else config.false_label
        node_run.status, node_run.completed_at = "succeeded", instant
        node_run.branch_outcome, node_run.result_summary = outcome, f"Condition resolved {outcome}."
        return "done"
    if isinstance(config, DelayNodeConfig):
        resume_at = _delay_resume_at(config, run.context_payload, instant)
        node_run.status, node_run.resume_at = "waiting", resume_at
        node_run.result_summary = "Waiting for durable delay."
        run.status, run.waiting_reason = "waiting", "delay"
        return "waiting"
    if isinstance(config, ApprovalNodeConfig):
        node_run.status, node_run.result_summary = "waiting", "Waiting for internal approval."
        run.status, run.waiting_reason = "waiting", "approval"
        await _flush(session)
        expires = instant + timedelta(seconds=config.expires_in_seconds) if config.expires_in_seconds else None
        await create_workflow_approval_request(
            session, business_id=run.business_id, workflow_node_run_id=node_run.id,
            reason_code=config.reason_code, requested_by_user_id=actor_user_id, expires_at=expires,
        )
        await _workflow_notification(session, run, "Workflow waiting for approval", "A workflow is paused for internal review.", "high")
        return "waiting"
    if isinstance(config, ExternalActionNodeConfig):
        governed = await _create_governed_action_intent(
            session, run=run, config=config, actor_user_id=actor_user_id, instant=instant
        )
        action = governed.action
        node_run.action_id = action.id
        if governed.approval is not None:
            node_run.status, node_run.result_summary = "waiting", "Governed action is waiting for approval; no dispatch occurred."
            run.status, run.waiting_reason = "waiting", "approval"
            await _workflow_notification(session, run, "Workflow action awaiting approval", "A governed action requires review. No external action was sent.", "high")
            return "waiting"
        node_run.status, node_run.completed_at = "succeeded", instant
        node_run.result_summary = "Governed action intent prepared; no dispatch occurred."
        return "done"
    if isinstance(config, InternalOperationNodeConfig):
        await _execute_internal_operation(session, run=run, config=config, actor_user_id=actor_user_id)
        node_run.status, node_run.completed_at, node_run.result_summary = "succeeded", instant, f"Internal operation {config.operation} completed."
        return "done"
    if isinstance(config, AINodeConfig):
        if provider is None:
            raise AutomationValidationError("ai_temporarily_unavailable")
        agent_config = await get_agent_config(
            session, business_id=run.business_id, role=config.role
        )
        if not agent_config.enabled:
            raise AutomationValidationError("ai_agent_disabled")
        try:
            allowed_capabilities = validate_role_capabilities(
                config.role, agent_config.capability_config
            )
        except ValueError:
            raise AutomationValidationError("ai_agent_capability_invalid") from None
        execution = await create_running_ai_agent_execution(
            session, business_id=run.business_id, requested_by_user_id=actor_user_id,
            role=config.role, task=config.task, provider_name=provider.provider_name,
            model_name=get_agent_provider_model_name(provider),
            trigger_type="automation",
        )
        runtime = await execute_ai_agent_with_metadata(
            session, run.business_id,
            AIAgentExecutionRequest(role=config.role, task=config.task, include_business_brain=True, include_memory=True),
            provider,
            custom_instructions=agent_config.custom_instructions,
            allowed_capabilities=allowed_capabilities,
        )
        result = runtime.execution_result
        if result.output.proposed_actions and not config.allow_action_proposals:
            raise AutomationValidationError("ai_action_proposals_disabled")
        try:
            validate_proposed_action_capabilities(
                config.role,
                allowed_capabilities,
                [item.action_type for item in result.output.proposed_actions],
            )
        except ValueError:
            raise AutomationValidationError("ai_agent_capability_violation") from None
        metadata = runtime.provider_metadata
        await finalize_successful_ai_agent_execution(
            session, business_id=run.business_id, execution_id=execution.id, result=result,
            input_tokens=metadata.input_tokens, output_tokens=metadata.output_tokens,
            provider_request_id=metadata.provider_request_id,
        )
        if config.allow_action_proposals:
            actions = await materialize_ai_actions(session, business_id=run.business_id, execution_id=execution.id)
            governed = await govern_materialized_ai_actions(
                session, business_id=run.business_id, actions=actions, requested_by_user_id=actor_user_id
            )
            if any(item.approval is not None for item in governed):
                node_run.status, node_run.result_summary = "waiting", "AI output recorded; proposed actions require approval."
                run.status, run.waiting_reason = "waiting", "approval"
                return "waiting"
        node_run.status, node_run.completed_at = "succeeded", instant
        node_run.result_summary = result.output.summary[:2000]
        return "done"
    raise AutomationValidationError("node_configuration_invalid")


async def _resume_waiting_node(
    session: AsyncSession, *, run: AutomationWorkflowRun, node_map: dict[UUID, AutomationNode],
    edges: list[AutomationEdge], instant: datetime
) -> bool:
    waiting = await session.scalar(select(AutomationNodeRun).where(
        AutomationNodeRun.business_id == run.business_id,
        AutomationNodeRun.workflow_run_id == run.id,
        AutomationNodeRun.node_key == run.current_node_key,
        AutomationNodeRun.status == "waiting",
    ).order_by(AutomationNodeRun.attempt.desc()).with_for_update())
    if run.waiting_reason == "retry":
        if waiting is not None and waiting.resume_at and waiting.resume_at > instant:
            return False
        run.status, run.waiting_reason = "running", None
        return True
    if waiting is None:
        raise AutomationStateError("Waiting node history is missing")
    node = node_map[run.current_node_key]
    if run.waiting_reason == "delay":
        if waiting.resume_at is None or waiting.resume_at > instant:
            return False
    elif run.waiting_reason == "approval":
        target_clause = (
            ApprovalRequest.action_id == waiting.action_id
            if waiting.action_id is not None
            else ApprovalRequest.workflow_node_run_id == waiting.id
        )
        approval = await session.scalar(select(ApprovalRequest).where(
            ApprovalRequest.business_id == run.business_id, target_clause,
        ).order_by(ApprovalRequest.created_at.desc()))
        if approval is None or approval.status == "pending":
            return False
        if approval.status != "approved":
            waiting.status, waiting.completed_at, waiting.failure_code = "failed", instant, f"approval_{approval.status}"
            await _fail_run(session, run, "approval_rejected" if approval.status == "rejected" else "approval_expired", run.requested_by_user_id)
            return False
    waiting.status, waiting.completed_at = "succeeded", instant
    waiting.result_summary = "Approval granted; no external dispatch occurred." if run.waiting_reason == "approval" else "Durable delay elapsed."
    run.current_node_key = next_node_key(node, edges)
    run.status, run.waiting_reason = "running", None
    return True


async def _fail_run(session: AsyncSession, run: AutomationWorkflowRun, code: str, actor_user_id: UUID | None) -> AutomationWorkflowRun:
    run.status, run.failure_code, run.completed_at, run.waiting_reason = "failed", code[:64], datetime.now(UTC), None
    await _workflow_notification(session, run, "Workflow failed", f"Workflow stopped with safe failure code: {run.failure_code}.", "high")
    await _flush(session)
    return run


async def _workflow_notification(
    session: AsyncSession, run: AutomationWorkflowRun, title: str, message: str, priority: str
) -> None:
    await create_notification(session, business_id=run.business_id, actor_user_id=run.requested_by_user_id, data=NotificationCreate(
        recipient_user_id=run.requested_by_user_id, category="automation", title=title,
        message=message, priority=priority, related_entity_type="automation_workflow_run", related_entity_id=run.id,
    ))


async def _create_governed_action_intent(
    session: AsyncSession, *, run: AutomationWorkflowRun, config: ExternalActionNodeConfig,
    actor_user_id: UUID | None, instant: datetime
):
    execution = AIAgentExecution(
        business_id=run.business_id, requested_by_user_id=actor_user_id,
        role="operations", trigger_type="automation", status="completed",
        task="Prepare a governed workflow action intent; do not execute it.",
        provider_name="internal_workflow", model_name="deterministic_intent",
        context_revision=None, context_source_count=0, business_brain_source_count=0,
        memory_source_count=0, output_summary="Workflow prepared one governed action intent.",
        recommendations=[], proposed_actions=[{
            "action_type": config.action_type, "description": config.description,
            "risk_level": config.risk_level, "requires_approval": config.requires_approval,
            "action_payload": config.payload,
        }], failure_code=None, provider_request_id=None, duration_ms=0,
        input_tokens=0, output_tokens=0, estimated_cost_usd=None, completed_at=instant,
    )
    session.add(execution)
    await _flush(session)
    actions = await materialize_ai_actions(session, business_id=run.business_id, execution_id=execution.id)
    governed = await govern_materialized_ai_actions(
        session, business_id=run.business_id, actions=actions, requested_by_user_id=actor_user_id
    )
    return governed[0]


async def _execute_internal_operation(
    session: AsyncSession, *, run: AutomationWorkflowRun,
    config: InternalOperationNodeConfig, actor_user_id: UUID | None
) -> None:
    values = config.parameters
    try:
        if config.operation == "create_notification":
            await create_notification(session, business_id=run.business_id, actor_user_id=actor_user_id, data=NotificationCreate.model_validate(values))
        elif config.operation == "create_opportunity":
            await create_opportunity(session, business_id=run.business_id, actor_user_id=actor_user_id, data=OpportunityCreate.model_validate(values))
        elif config.operation == "update_lead_stage":
            await change_lead_state(
                session, business_id=run.business_id, lead_id=_context_uuid(values, run.context_payload, "lead_id"),
                actor_user_id=actor_user_id, field="stage", value=str(values["stage"]),
            )
        elif config.operation == "add_customer_tag":
            customer_id = _context_uuid(values, run.context_payload, "customer_id")
            customer = await get_customer(session, business_id=run.business_id, customer_id=customer_id)
            tag = str(values["tag"]).strip()[:80]
            tags = list(dict.fromkeys([*customer.tags, tag]))[:20]
            await update_customer(session, business_id=run.business_id, customer_id=customer_id,
                                  actor_user_id=actor_user_id, data=CustomerUpdate(tags=tags))
        elif config.operation == "generate_report":
            await generate_report(session, business_id=run.business_id, actor_user_id=actor_user_id,
                                  data=ReportGenerateRequest.model_validate(values))
        elif config.operation == "set_campaign_status":
            await change_campaign_status(
                session, business_id=run.business_id,
                campaign_id=_context_uuid(values, run.context_payload, "campaign_id"),
                actor_user_id=actor_user_id, status=str(values["status"]),
            )
        else:
            raise AutomationValidationError("node_configuration_invalid")
    except (ValidationError, KeyError, TypeError, ValueError):
        raise AutomationValidationError("node_configuration_invalid") from None
    except AutomationValidationError:
        raise
    except Exception:
        raise AutomationValidationError("internal_operation_failed") from None


def _context_uuid(parameters: dict[str, Any], payload: dict[str, Any], key: str) -> UUID:
    raw = parameters.get(key)
    if raw is None:
        raw = payload.get("event", {}).get("entity_id") if isinstance(payload.get("event"), dict) else None
    try:
        return UUID(str(raw))
    except (TypeError, ValueError):
        raise AutomationValidationError("node_configuration_invalid") from None


def _delay_resume_at(config: DelayNodeConfig, payload: dict[str, Any], instant: datetime) -> datetime:
    if config.mode == "duration":
        return instant + timedelta(seconds=int(config.seconds))
    if config.mode == "until":
        return config.until.astimezone(UTC)
    raw = payload.get("appointment", {}).get("starts_at") if isinstance(payload.get("appointment"), dict) else None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise AutomationValidationError("condition_input_missing") from None
    return value.astimezone(UTC) + timedelta(seconds=config.offset_seconds)


def _bounded_context(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or len(str(value)) > 20_000:
        raise AutomationValidationError("context_payload_invalid")
    return value


async def simulate_workflow(
    session: AsyncSession, *, business_id: UUID, workflow_id: UUID,
    request: SimulationRequest, provider: AIAgentProvider | None = None,
) -> dict[str, Any]:
    """Evaluate a graph without writing workflow, business, action, or approval rows."""
    workflow = await get_workflow(session, business_id=business_id, workflow_id=workflow_id)
    _, nodes, edges = await load_graph(session, workflow=workflow)
    errors = validate_graph(nodes, edges)
    if errors:
        return {"valid": False, "completed": False, "trace": [], "approvals": [],
                "delays": [], "planned_actions": [], "errors": errors}
    node_map = {node.node_key: node for node in nodes}
    current = next(node.node_key for node in nodes if node.node_type == "trigger")
    trace: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    delays: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    payload = _bounded_context(request.payload.model_dump(mode="json", exclude_none=True))
    for _ in range(MAX_RUN_STEPS):
        node = node_map[current]
        if request.forced_failure_node_key == node.node_key:
            trace.append(_trace(node, "failed", "Forced safe simulation failure."))
            return {"valid": True, "completed": False, "trace": trace, "approvals": approvals,
                    "delays": delays, "planned_actions": actions, "errors": ["forced_simulation_failure"]}
        config = NODE_CONFIGURATION_ADAPTER.validate_python(node.configuration)
        outcome = None
        summary = "Node would complete."
        status = "succeeded"
        try:
            if node.node_type in {"condition", "branch"}:
                result = evaluate_condition(parse_condition(node), payload)
                outcome = "true" if result else "false"
                if hasattr(config, "true_label"):
                    outcome = config.true_label if result else config.false_label
                summary = f"Condition would route to {outcome}."
            elif isinstance(config, ExternalActionNodeConfig):
                item = {"node_key": str(node.node_key), "action_type": config.action_type,
                        "description": config.description, "dispatch": False}
                actions.append(item)
                if config.requires_approval:
                    approvals.append({"node_key": str(node.node_key), "reason": "governed_action_policy"})
                status, summary = "planned", "Governed action intent would be proposed; no dispatch."
            elif isinstance(config, ApprovalNodeConfig):
                approvals.append({"node_key": str(node.node_key), "reason": config.reason_code})
                status, summary = "waiting", "Workflow would wait for approval."
            elif isinstance(config, DelayNodeConfig):
                resume_at = _delay_resume_at(config, payload, datetime.now(UTC))
                delays.append({"node_key": str(node.node_key), "resume_at": resume_at.isoformat()})
                status, summary = "waiting", "Workflow would persist a delay."
            elif isinstance(config, InternalOperationNodeConfig):
                actions.append({"node_key": str(node.node_key), "internal_operation": config.operation, "mutation": False})
                status, summary = "planned", f"Internal operation {config.operation} would run."
            elif isinstance(config, AINodeConfig):
                if request.run_ai:
                    if provider is None:
                        raise AutomationValidationError("ai_temporarily_unavailable")
                    agent_config = await get_agent_config(
                        session, business_id=business_id, role=config.role
                    )
                    if not agent_config.enabled:
                        raise AutomationValidationError("ai_agent_disabled")
                    try:
                        allowed_capabilities = validate_role_capabilities(
                            config.role, agent_config.capability_config
                        )
                    except ValueError:
                        raise AutomationValidationError("ai_agent_capability_invalid") from None
                    result = await execute_ai_agent_with_metadata(
                        session, business_id,
                        AIAgentExecutionRequest(role=config.role, task=config.task, include_business_brain=True, include_memory=True),
                        provider,
                        custom_instructions=agent_config.custom_instructions,
                        allowed_capabilities=allowed_capabilities,
                    )
                    if result.execution_result.output.proposed_actions and not config.allow_action_proposals:
                        raise AutomationValidationError("ai_action_proposals_disabled")
                    try:
                        validate_proposed_action_capabilities(
                            config.role,
                            allowed_capabilities,
                            [item.action_type for item in result.execution_result.output.proposed_actions],
                        )
                    except ValueError:
                        raise AutomationValidationError("ai_agent_capability_violation") from None
                    summary = result.execution_result.output.summary[:2000]
                else:
                    status, summary = "planned", "AI node validated; safe model execution was not requested."
            elif isinstance(config, EndNodeConfig):
                summary = "Workflow simulation completed."
        except AutomationValidationError as exc:
            trace.append(_trace(node, "failed", str(exc), outcome))
            return {"valid": True, "completed": False, "trace": trace, "approvals": approvals,
                    "delays": delays, "planned_actions": actions, "errors": [str(exc)]}
        trace.append(_trace(node, status, summary, outcome))
        if node.node_type == "end":
            return {"valid": True, "completed": True, "trace": trace, "approvals": approvals,
                    "delays": delays, "planned_actions": actions, "errors": []}
        current = next_node_key(node, edges, outcome=outcome)
    return {"valid": True, "completed": False, "trace": trace, "approvals": approvals,
            "delays": delays, "planned_actions": actions, "errors": ["graph_invalid"]}


async def process_automation_event(
    session: AsyncSession, *, business_id: UUID, event_id: UUID
) -> tuple[AutomationEvent, list[AutomationWorkflowRun]]:
    event = await session.scalar(select(AutomationEvent).where(
        AutomationEvent.id == event_id, AutomationEvent.business_id == business_id
    ).with_for_update())
    if event is None:
        raise AutomationNotFoundError("Automation event not found")
    if event.status == "processed":
        existing = list((await session.scalars(select(AutomationWorkflowRun).where(
            AutomationWorkflowRun.business_id == business_id,
            AutomationWorkflowRun.trigger_event_id == event.id,
        ).order_by(AutomationWorkflowRun.id))).all())
        return event, existing
    if event.status not in {"pending", "failed"}:
        raise AutomationStateError("Automation event is already processing")
    event.status, event.failure_code = "processing", None
    workflows = list((await session.scalars(select(AutomationWorkflow).where(
        AutomationWorkflow.business_id == business_id,
        AutomationWorkflow.status == "active",
        AutomationWorkflow.enabled.is_(True),
        AutomationWorkflow.trigger_type == event.event_type,
    ).order_by(AutomationWorkflow.id))).all())
    context = {
        "event": {"type": event.event_type, "entity_type": event.entity_type,
                  "entity_id": str(event.entity_id) if event.entity_id else None},
        event.entity_type: event.payload,
    }
    runs: list[AutomationWorkflowRun] = []
    try:
        for workflow in workflows:
            runs.append(await create_workflow_run(
                session, business_id=business_id, workflow_id=workflow.id, trigger_type="event",
                context_payload=context, requested_by_user_id=None, trigger_event_id=event.id,
            ))
        event.status, event.processed_at = "processed", datetime.now(UTC)
        await _flush(session)
    except Exception:
        event.status, event.failure_code = "failed", "event_processing_failed"
        await _flush(session)
        raise
    return event, runs


async def automation_analytics(session: AsyncSession, *, business_id: UUID) -> dict[str, Any]:
    total_workflows = int(await session.scalar(select(func.count()).select_from(AutomationWorkflow).where(
        AutomationWorkflow.business_id == business_id
    )) or 0)
    active_workflows = int(await session.scalar(select(func.count()).select_from(AutomationWorkflow).where(
        AutomationWorkflow.business_id == business_id, AutomationWorkflow.status == "active"
    )) or 0)
    run_counts = dict((await session.execute(select(
        AutomationWorkflowRun.status, func.count(AutomationWorkflowRun.id)
    ).where(AutomationWorkflowRun.business_id == business_id).group_by(AutomationWorkflowRun.status))).all())
    total_runs = sum(int(value) for value in run_counts.values())
    succeeded = int(run_counts.get("succeeded", 0))
    failed = int(run_counts.get("failed", 0))
    waiting_approvals = int(await session.scalar(select(func.count()).select_from(ApprovalRequest).where(
        ApprovalRequest.business_id == business_id,
        ApprovalRequest.status == "pending",
        ApprovalRequest.workflow_node_run_id.is_not(None),
    )) or 0)
    avg = await session.scalar(select(func.avg(func.extract(
        "epoch", AutomationWorkflowRun.completed_at - AutomationWorkflowRun.started_at
    ))).where(
        AutomationWorkflowRun.business_id == business_id,
        AutomationWorkflowRun.completed_at.is_not(None),
        AutomationWorkflowRun.started_at.is_not(None),
    ))
    failures = dict((await session.execute(select(
        AutomationNodeRun.failure_code, func.count(AutomationNodeRun.id)
    ).where(
        AutomationNodeRun.business_id == business_id,
        AutomationNodeRun.failure_code.is_not(None),
    ).group_by(AutomationNodeRun.failure_code))).all())
    return {
        "total_workflows": total_workflows, "active_workflows": active_workflows,
        "total_runs": total_runs, "succeeded_runs": succeeded, "failed_runs": failed,
        "success_rate": round((succeeded / total_runs * 100), 2) if total_runs else 0.0,
        "waiting_approvals": waiting_approvals,
        "average_run_duration_seconds": float(avg) if avg is not None else None,
        "node_failures": {str(key): int(value) for key, value in failures.items()},
    }


def _trace(node: AutomationNode, status: str, summary: str, outcome: str | None = None) -> dict[str, Any]:
    return {"node_key": node.node_key, "node_type": node.node_type, "name": node.name,
            "status": status, "branch_outcome": outcome, "summary": summary[:2000]}
