from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.business import BusinessAccessDependency, require_business_role
from app.db.session import get_db_session
from app.exceptions.approval import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalPersistenceError,
    ApprovalStateError,
    ApprovalValidationError,
)
from app.models.approval_request import ApprovalRequest
from app.models.ai_action import AIAction
from app.models.automation import AutomationNode, AutomationNodeRun, AutomationWorkflow, AutomationWorkflowRun
from app.schemas.approval import (
    ApprovalDecisionRequest,
    ApprovalRequestPageResponse,
    ApprovalRequestResponse,
    ApprovalStatus,
)
from app.services.approval import (
    DEFAULT_APPROVAL_PAGE_SIZE,
    MAX_APPROVAL_PAGE_SIZE,
    approve_approval_request,
    cancel_approval_request,
    get_approval_request,
    list_approval_requests,
    reject_approval_request,
)


router = APIRouter(
    prefix="/businesses/{business_id}/approvals",
    tags=["AI Action Approvals"],
)

SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("", response_model=ApprovalRequestPageResponse)
async def read_approval_requests(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    approval_status: Annotated[
        ApprovalStatus | None,
        Query(alias="status"),
    ] = "pending",
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_APPROVAL_PAGE_SIZE),
    ] = DEFAULT_APPROVAL_PAGE_SIZE,
) -> ApprovalRequestPageResponse:
    try:
        items = await list_approval_requests(
            session,
            business_id=access.business.id,
            approval_status=approval_status,
            limit=limit,
        )
    except ApprovalValidationError:
        raise _invalid_request_exception() from None
    except ApprovalPersistenceError:
        raise _unavailable_exception() from None
    _set_private_headers(response)
    return ApprovalRequestPageResponse(items=await _approval_responses(session, items))


@router.get("/{approval_id}", response_model=ApprovalRequestResponse)
async def read_approval_request(
    approval_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ApprovalRequest:
    try:
        approval = await get_approval_request(
            session,
            business_id=access.business.id,
            approval_id=approval_id,
        )
    except ApprovalNotFoundError:
        raise _not_found_exception() from None
    except ApprovalPersistenceError:
        raise _unavailable_exception() from None
    _set_private_headers(response)
    return (await _approval_responses(session, [approval]))[0]


@router.post("/{approval_id}/approve", response_model=ApprovalRequestResponse)
async def approve_request(
    approval_id: UUID,
    decision: ApprovalDecisionRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ApprovalRequest:
    return await _apply_user_decision(
        operation="approve",
        approval_id=approval_id,
        decision=decision,
        access=access,
        response=response,
        session=session,
    )


@router.post("/{approval_id}/reject", response_model=ApprovalRequestResponse)
async def reject_request(
    approval_id: UUID,
    decision: ApprovalDecisionRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ApprovalRequest:
    return await _apply_user_decision(
        operation="reject",
        approval_id=approval_id,
        decision=decision,
        access=access,
        response=response,
        session=session,
    )


@router.post("/{approval_id}/cancel", response_model=ApprovalRequestResponse)
async def cancel_request(
    approval_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
) -> ApprovalRequest:
    require_business_role(access)
    try:
        approval = await cancel_approval_request(
            session,
            business_id=access.business.id,
            approval_id=approval_id,
            canceled_by_user_id=access.user.id,
        )
        await session.commit()
    except ApprovalNotFoundError:
        await _rollback_safely(session)
        raise _not_found_exception() from None
    except (ApprovalStateError, ApprovalConflictError, ApprovalValidationError):
        await _rollback_safely(session)
        raise _invalid_state_exception() from None
    except (ApprovalPersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _unavailable_exception() from None
    _set_private_headers(response)
    return (await _approval_responses(session, [approval]))[0]


async def _apply_user_decision(
    *,
    operation: str,
    approval_id: UUID,
    decision: ApprovalDecisionRequest,
    access,
    response: Response,
    session: AsyncSession,
) -> ApprovalRequest:
    require_business_role(access)
    service = (
        approve_approval_request
        if operation == "approve"
        else reject_approval_request
    )
    try:
        approval = await service(
            session,
            business_id=access.business.id,
            approval_id=approval_id,
            decided_by_user_id=access.user.id,
            decision_note=decision.decision_note,
        )
        await session.commit()
    except ApprovalNotFoundError:
        await _rollback_safely(session)
        raise _not_found_exception() from None
    except (ApprovalStateError, ApprovalConflictError, ApprovalValidationError):
        await _rollback_safely(session)
        raise _invalid_state_exception() from None
    except (ApprovalPersistenceError, SQLAlchemyError):
        await _rollback_safely(session)
        raise _unavailable_exception() from None
    _set_private_headers(response)
    return (await _approval_responses(session, [approval]))[0]


async def _rollback_safely(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except SQLAlchemyError:
        return


def _set_private_headers(response: Response) -> None:
    for name, value in _PRIVATE_RESPONSE_HEADERS.items():
        response.headers[name] = value


def _not_found_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Approval request not found.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


def _invalid_request_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Invalid approval request.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


def _invalid_state_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Requested approval transition is invalid.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


def _unavailable_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Approvals are temporarily unavailable.",
        headers=_PRIVATE_RESPONSE_HEADERS,
    )


_PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}


_ACTION_PROVIDER_CHANNELS = {
    "send_email": "Gmail or Microsoft Outlook",
    "send_whatsapp_message": "WhatsApp Business",
    "send_customer_message": "Connected customer messaging channel",
    "publish_social_post": "Connected social channel",
    "create_meta_campaign": "Meta Ads",
    "launch_meta_campaign": "Meta Ads",
    "create_google_ads_campaign": "Google Ads",
    "launch_google_ads_campaign": "Google Ads",
    "change_ad_budget": "Connected advertising channel",
    "pause_ad_campaign": "Connected advertising channel",
}


def _safe_action_context(action: AIAction) -> dict[str, object]:
    """Return useful review context without message bodies or provider data."""
    payload = action.action_payload if isinstance(action.action_payload, dict) else {}
    summary: dict[str, str | int | bool] = {}
    for key in (
        "subject", "platform", "campaign_name", "objective", "network",
        "campaign_ref", "budget_period", "currency",
    ):
        value = payload.get(key)
        if isinstance(value, (str, int, bool)) and len(str(value)) <= 255:
            summary[key] = value
    media_refs = payload.get("media_refs")
    if isinstance(media_refs, list):
        summary["media_count"] = min(len(media_refs), 10)
    creative = payload.get("creative")
    if isinstance(creative, dict) and isinstance(creative.get("creative_refs"), list):
        summary["creative_count"] = min(len(creative["creative_refs"]), 20)

    recipient = payload.get("recipient_ref") or payload.get("customer_ref")
    audience = payload.get("audience")
    audience_summary = str(recipient)[:255] if isinstance(recipient, str) else None
    if isinstance(audience, dict):
        countries = audience.get("countries")
        if isinstance(countries, list):
            safe_countries = [str(item)[:3] for item in countries[:25]]
            audience_summary = f"Countries: {', '.join(safe_countries)}"
            if isinstance(audience.get("min_age"), int) and isinstance(audience.get("max_age"), int):
                audience_summary += f" · ages {audience['min_age']}–{audience['max_age']}"

    budget = payload.get("budget")
    budget_summary = None
    if isinstance(budget, (str, int, float)):
        currency = payload.get("currency") if isinstance(payload.get("currency"), str) else ""
        period = payload.get("budget_period") if isinstance(payload.get("budget_period"), str) else ""
        budget_summary = " ".join(part for part in (currency, str(budget), period) if part)[:255]

    affected_entity = "AI-proposed external action"
    if isinstance(recipient, str):
        affected_entity = "Customer or lead record"
    elif isinstance(payload.get("campaign_ref"), str) or "campaign" in action.action_type:
        affected_entity = "Advertising campaign"
    elif action.action_type == "publish_social_post":
        affected_entity = "Connected social account"

    return {
        "id": action.id,
        "action_type": action.action_type,
        "description": action.description,
        "risk_level": action.risk_level,
        "status": action.status,
        "policy_decision": action.policy_decision,
        "policy_reason_code": action.policy_reason_code,
        "provider_channel": _ACTION_PROVIDER_CHANNELS.get(action.action_type, "Internal operation"),
        "affected_entity": affected_entity,
        "audience_or_recipient": audience_summary,
        "budget_summary": budget_summary,
        "payload_summary": summary,
    }


async def _approval_responses(session: AsyncSession, approvals: list[ApprovalRequest]) -> list[dict]:
    action_ids = [item.action_id for item in approvals if item.action_id is not None]
    node_run_ids = [item.workflow_node_run_id for item in approvals if item.workflow_node_run_id is not None]
    actions = {}
    workflows = {}
    if not hasattr(session, "scalars") or not hasattr(session, "execute"):
        return [{
            **{column.name: getattr(item, column.name) for column in item.__table__.columns},
            "target_type": "ai_action" if item.action_id is not None else "workflow_node",
            "action": None, "workflow": None,
        } for item in approvals]
    if action_ids:
        business_ids = {item.business_id for item in approvals}
        values = list((await session.scalars(select(AIAction).where(
            AIAction.id.in_(action_ids), AIAction.business_id.in_(business_ids)
        ))).all())
        actions = {item.id: _safe_action_context(item) for item in values}
    if node_run_ids:
        rows = (await session.execute(
            select(AutomationNodeRun, AutomationWorkflowRun, AutomationWorkflow, AutomationNode)
            .join(AutomationWorkflowRun, (AutomationWorkflowRun.id == AutomationNodeRun.workflow_run_id) &
                  (AutomationWorkflowRun.business_id == AutomationNodeRun.business_id))
            .join(AutomationWorkflow, (AutomationWorkflow.id == AutomationWorkflowRun.workflow_id) &
                  (AutomationWorkflow.business_id == AutomationNodeRun.business_id))
            .join(AutomationNode, (AutomationNode.workflow_version_id == AutomationNodeRun.workflow_version_id) &
                  (AutomationNode.node_key == AutomationNodeRun.node_key) &
                  (AutomationNode.business_id == AutomationNodeRun.business_id))
            .where(
                AutomationNodeRun.id.in_(node_run_ids),
                AutomationNodeRun.business_id.in_({item.business_id for item in approvals}),
            )
        )).all()
        workflows = {node_run.id: {
            "node_run_id": node_run.id, "run_id": run.id, "workflow_id": workflow.id,
            "workflow_name": workflow.name, "node_key": node_run.node_key,
            "node_name": node.name, "run_status": run.status,
        } for node_run, run, workflow, node in rows}
    return [{
        **{column.name: getattr(item, column.name) for column in item.__table__.columns},
        "target_type": "ai_action" if item.action_id is not None else "workflow_node",
        "action": actions.get(item.action_id),
        "workflow": workflows.get(item.workflow_node_run_id),
    } for item in approvals]
