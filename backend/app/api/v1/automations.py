from __future__ import annotations

from typing import Annotated, Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency
from app.api.response_materialization import materialize_response_before_commit
from app.api.dependencies.ai_agent import get_ai_agent_provider
from app.db.session import get_db_session
from app.exceptions.automation import (
    AutomationConflictError,
    AutomationNotFoundError,
    AutomationPersistenceError,
    AutomationStateError,
    AutomationValidationError,
)
from app.schemas.automation import (
    AutomationAnalyticsResponse,
    AutomationEventPageResponse,
    EdgeCreate,
    EdgeResponse,
    EdgeUpdate,
    EventProcessResponse,
    GraphValidationResponse,
    ManualRunRequest,
    NodeCreate,
    NodeResponse,
    NodeUpdate,
    NodeRunPageResponse,
    SimulationRequest,
    SimulationResponse,
    WorkflowCreate,
    WorkflowDetailResponse,
    WorkflowPageResponse,
    WorkflowResponse,
    WorkflowRunPageResponse,
    WorkflowRunResponse,
    WorkflowStatusUpdate,
    WorkflowUpdate,
)
from app.services.automation import (
    advance_workflow_run,
    automation_analytics,
    cancel_workflow_run,
    create_edge,
    create_node,
    create_workflow,
    create_workflow_run,
    delete_edge,
    delete_node,
    duplicate_workflow,
    get_workflow_run,
    list_node_runs,
    list_workflow_runs,
    list_workflows,
    process_automation_event,
    simulate_workflow,
    transition_workflow,
    update_edge,
    update_node,
    update_workflow,
    validate_workflow_graph,
    workflow_detail,
)
from app.services.automation_events import list_automation_events


router = APIRouter(prefix="/businesses/{business_id}/automations", tags=["Automations"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/workflows", response_model=WorkflowPageResponse)
async def read_workflows(access: BusinessAccessDependency, session: SessionDependency,
                         page: Annotated[int, Query(ge=1)] = 1,
                         page_size: Annotated[int, Query(ge=1, le=100)] = 50):
    items, total = await _read(lambda: list_workflows(
        session, business_id=access.business.id, page=page, page_size=page_size
    ))
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.post("/workflows", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def add_workflow(data: WorkflowCreate, access: BusinessAccessDependency,
                       response: Response, session: SessionDependency):
    return await _write(session, response, lambda: create_workflow(
        session, business_id=access.business.id, actor_user_id=access.user.id, data=data
    ))


@router.get("/workflows/{workflow_id}", response_model=WorkflowDetailResponse)
async def read_workflow(workflow_id: UUID, access: BusinessAccessDependency, session: SessionDependency):
    return await _read(lambda: workflow_detail(
        session, business_id=access.business.id, workflow_id=workflow_id
    ))


@router.patch("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def change_workflow(workflow_id: UUID, data: WorkflowUpdate, access: BusinessAccessDependency,
                          response: Response, session: SessionDependency):
    return await _write(session, response, lambda: update_workflow(
        session, business_id=access.business.id, workflow_id=workflow_id,
        actor_user_id=access.user.id, data=data
    ))


@router.post("/workflows/{workflow_id}/duplicate", response_model=WorkflowResponse,
             status_code=status.HTTP_201_CREATED)
async def copy_workflow(workflow_id: UUID, access: BusinessAccessDependency,
                        response: Response, session: SessionDependency):
    return await _write(session, response, lambda: duplicate_workflow(
        session, business_id=access.business.id, workflow_id=workflow_id,
        actor_user_id=access.user.id
    ))


@router.post("/workflows/{workflow_id}/status", response_model=WorkflowResponse)
async def change_workflow_status(workflow_id: UUID, data: WorkflowStatusUpdate,
                                 access: BusinessAccessDependency, response: Response,
                                 session: SessionDependency):
    return await _write(session, response, lambda: transition_workflow(
        session, business_id=access.business.id, workflow_id=workflow_id,
        actor_user_id=access.user.id, target_status=data.status
    ))


@router.post("/workflows/{workflow_id}/nodes", response_model=NodeResponse,
             status_code=status.HTTP_201_CREATED)
async def add_node(workflow_id: UUID, data: NodeCreate, access: BusinessAccessDependency,
                   response: Response, session: SessionDependency):
    return await _write(session, response, lambda: create_node(
        session, business_id=access.business.id, workflow_id=workflow_id,
        actor_user_id=access.user.id, data=data
    ))


@router.patch("/workflows/{workflow_id}/nodes/{node_key}", response_model=NodeResponse)
async def change_node(workflow_id: UUID, node_key: UUID, data: NodeUpdate,
                      access: BusinessAccessDependency, response: Response,
                      session: SessionDependency):
    return await _write(session, response, lambda: update_node(
        session, business_id=access.business.id, workflow_id=workflow_id,
        node_key=node_key, actor_user_id=access.user.id, data=data
    ))


@router.delete("/workflows/{workflow_id}/nodes/{node_key}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_node(workflow_id: UUID, node_key: UUID, access: BusinessAccessDependency,
                      response: Response, session: SessionDependency):
    await _write(session, response, lambda: delete_node(
        session, business_id=access.business.id, workflow_id=workflow_id,
        node_key=node_key, actor_user_id=access.user.id
    ))


@router.post("/workflows/{workflow_id}/edges", response_model=EdgeResponse,
             status_code=status.HTTP_201_CREATED)
async def add_edge(workflow_id: UUID, data: EdgeCreate, access: BusinessAccessDependency,
                   response: Response, session: SessionDependency):
    return await _write(session, response, lambda: create_edge(
        session, business_id=access.business.id, workflow_id=workflow_id,
        actor_user_id=access.user.id, data=data
    ))


@router.patch("/workflows/{workflow_id}/edges/{edge_key}", response_model=EdgeResponse)
async def change_edge(workflow_id: UUID, edge_key: UUID, data: EdgeUpdate,
                      access: BusinessAccessDependency, response: Response,
                      session: SessionDependency):
    return await _write(session, response, lambda: update_edge(
        session, business_id=access.business.id, workflow_id=workflow_id,
        edge_key=edge_key, actor_user_id=access.user.id, data=data
    ))


@router.delete("/workflows/{workflow_id}/edges/{edge_key}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_edge(workflow_id: UUID, edge_key: UUID, access: BusinessAccessDependency,
                      response: Response, session: SessionDependency):
    await _write(session, response, lambda: delete_edge(
        session, business_id=access.business.id, workflow_id=workflow_id,
        edge_key=edge_key, actor_user_id=access.user.id
    ))


@router.get("/workflows/{workflow_id}/validation", response_model=GraphValidationResponse)
async def validate_workflow(workflow_id: UUID, access: BusinessAccessDependency,
                            session: SessionDependency):
    errors = await _read(lambda: validate_workflow_graph(
        session, business_id=access.business.id, workflow_id=workflow_id
    ))
    return {"valid": not errors, "errors": errors, "warnings": []}


@router.post("/workflows/{workflow_id}/simulate", response_model=SimulationResponse)
async def test_workflow(workflow_id: UUID, data: SimulationRequest,
                        access: BusinessAccessDependency, session: SessionDependency):
    provider = get_ai_agent_provider() if data.run_ai else None
    return await _read(lambda: simulate_workflow(
        session, business_id=access.business.id, workflow_id=workflow_id,
        request=data, provider=provider
    ))


@router.post("/workflows/{workflow_id}/runs", response_model=WorkflowRunResponse,
             status_code=status.HTTP_202_ACCEPTED)
async def queue_manual_run(workflow_id: UUID, data: ManualRunRequest,
                           access: BusinessAccessDependency, response: Response,
                           session: SessionDependency):
    return await _write(session, response, lambda: create_workflow_run(
        session, business_id=access.business.id, workflow_id=workflow_id,
        trigger_type="manual", context_payload=data.payload.model_dump(mode="json", exclude_none=True),
        requested_by_user_id=access.user.id, idempotency_key=data.idempotency_key
    ))


@router.get("/runs", response_model=WorkflowRunPageResponse)
async def read_runs(access: BusinessAccessDependency, session: SessionDependency,
                    workflow_id: UUID | None = None,
                    page: Annotated[int, Query(ge=1)] = 1,
                    page_size: Annotated[int, Query(ge=1, le=100)] = 50):
    items, total = await _read(lambda: list_workflow_runs(
        session, business_id=access.business.id, workflow_id=workflow_id,
        page=page, page_size=page_size
    ))
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.get("/runs/{run_id}", response_model=WorkflowRunResponse)
async def read_run(run_id: UUID, access: BusinessAccessDependency, session: SessionDependency):
    return await _read(lambda: get_workflow_run(
        session, business_id=access.business.id, run_id=run_id
    ))


@router.get("/runs/{run_id}/nodes", response_model=NodeRunPageResponse)
async def read_node_history(run_id: UUID, access: BusinessAccessDependency,
                            session: SessionDependency,
                            page: Annotated[int, Query(ge=1)] = 1,
                            page_size: Annotated[int, Query(ge=1, le=100)] = 50):
    items, total = await _read(lambda: list_node_runs(
        session, business_id=access.business.id, run_id=run_id,
        page=page, page_size=page_size
    ))
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.post("/runs/{run_id}/advance", response_model=WorkflowRunResponse)
async def advance_run(run_id: UUID, access: BusinessAccessDependency,
                      response: Response, session: SessionDependency):
    try:
        provider = get_ai_agent_provider()
    except HTTPException:
        provider = None
    return await _write(session, response, lambda: advance_workflow_run(
        session, business_id=access.business.id, run_id=run_id,
        actor_user_id=access.user.id, provider=provider
    ))


@router.post("/runs/{run_id}/cancel", response_model=WorkflowRunResponse)
async def cancel_run(run_id: UUID, access: BusinessAccessDependency,
                     response: Response, session: SessionDependency):
    return await _write(session, response, lambda: cancel_workflow_run(
        session, business_id=access.business.id, run_id=run_id,
        actor_user_id=access.user.id
    ))


@router.get("/events", response_model=AutomationEventPageResponse)
async def read_events(access: BusinessAccessDependency, session: SessionDependency,
                      page: Annotated[int, Query(ge=1)] = 1,
                      page_size: Annotated[int, Query(ge=1, le=100)] = 50):
    items, total = await _read(lambda: list_automation_events(
        session, business_id=access.business.id, page=page, page_size=page_size
    ))
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.post("/events/{event_id}/process", response_model=EventProcessResponse)
async def process_event(event_id: UUID, access: BusinessAccessDependency,
                        response: Response, session: SessionDependency):
    event, runs = await _write(session, response, lambda: process_automation_event(
        session, business_id=access.business.id, event_id=event_id
    ))
    return {"event": event, "created_run_ids": [run.id for run in runs]}


@router.get("/analytics", response_model=AutomationAnalyticsResponse)
async def read_analytics(access: BusinessAccessDependency, session: SessionDependency):
    return await _read(lambda: automation_analytics(session, business_id=access.business.id))


async def _read(operation: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await operation()
    except AutomationNotFoundError:
        raise HTTPException(status_code=404, detail="Automation resource not found.") from None
    except AutomationValidationError:
        raise HTTPException(status_code=422, detail="Automation input or graph is invalid.") from None
    except (AutomationStateError, AutomationConflictError):
        raise HTTPException(status_code=409, detail="Automation state conflicts with this request.") from None
    except (AutomationPersistenceError, SQLAlchemyError):
        raise HTTPException(status_code=503, detail="Automations are temporarily unavailable.") from None


async def _write(session: AsyncSession, response: Response, operation: Callable[[], Awaitable[Any]]) -> Any:
    try:
        result = await operation()
        await materialize_response_before_commit(session, result)
        await session.commit()
    except HTTPException:
        await session.rollback()
        raise
    except AutomationNotFoundError:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Automation resource not found.") from None
    except AutomationValidationError:
        await session.rollback()
        raise HTTPException(status_code=422, detail="Automation input or graph is invalid.") from None
    except (AutomationStateError, AutomationConflictError):
        await session.rollback()
        raise HTTPException(status_code=409, detail="Automation state conflicts with this request.") from None
    except (AutomationPersistenceError, SQLAlchemyError):
        await session.rollback()
        raise HTTPException(status_code=503, detail="Automations are temporarily unavailable.") from None
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
    return result
