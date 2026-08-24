from __future__ import annotations

from hashlib import sha256
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.action_adapters import connector_action_adapters
from app.integrations.registry import require_connector
from app.exceptions.integration import IntegrationValidationError
from app.models.ai_action import AIAction
from app.models.approval_request import ApprovalRequest
from app.models.automation_intelligence import (
    AudienceHypothesis,
    MarketingActionProposal,
)
from app.models.integration import IntegrationConnection
from app.models.marketing import Campaign, MarketingContent
from app.schemas.ai_action_payload import (
    CampaignAudience,
    CampaignCreative,
    CreateGoogleAdsCampaignPayload,
    CreateMetaCampaignPayload,
    PublishSocialPostPayload,
)
from app.schemas.ai_agent import (
    AIAgentExecutionResult,
    AIAgentProposedAction,
    AIAgentStructuredOutput,
)
from app.services.action_governance import govern_materialized_ai_actions
from app.services.ai_action import materialize_ai_actions
from app.services.ai_agent_execution import (
    create_running_ai_agent_execution,
    finalize_successful_ai_agent_execution,
)
from app.services.marketing import get_campaign, get_content
from app.exceptions.marketing import (
    MarketingPersistenceError,
    MarketingValidationError,
)


CampaignActionChannel = Literal["meta", "google_ads"]
SocialActionChannel = Literal["facebook", "instagram"]


async def prepare_campaign_action(
    session: AsyncSession,
    *,
    business_id: UUID,
    campaign_id: UUID,
    requested_by_user_id: UUID,
    channel: CampaignActionChannel | None,
) -> dict[str, object]:
    campaign = await get_campaign(
        session, business_id=business_id, campaign_id=campaign_id
    )
    target = _campaign_channel(campaign, channel)
    action_type = {
        "meta": "create_meta_campaign",
        "google_ads": "create_google_ads_campaign",
    }[target]
    connector_type = {"meta": "meta_ads", "google_ads": "google_ads"}[target]
    existing = await _existing_link(
        session,
        business_id=business_id,
        entity_type="campaign",
        entity_id=campaign.id,
        channel=target,
        action_type=action_type,
    )
    if existing is not None:
        return await _proposal_response(session, existing)

    countries, min_age, max_age = await _trusted_campaign_audience(
        session, business_id=business_id, campaign=campaign
    )
    if not countries:
        raise MarketingValidationError(
            "A trusted ISO country is required before an advertising action can be prepared"
        )

    objective = _campaign_objective(campaign.objective)
    audience = CampaignAudience(
        countries=countries,
        min_age=min_age,
        max_age=max_age,
    )
    creative = CampaignCreative(
        creative_refs=[f"marketing-campaign:{campaign.id}"],
        destination_url=campaign.landing_destination,
    )
    common = {
        "campaign_name": campaign.name,
        "objective": objective,
        "budget": campaign.planned_budget,
        "currency": campaign.currency,
        "budget_period": campaign.budget_mode,
        "audience": audience,
        "creative": creative,
    }
    payload = (
        CreateMetaCampaignPayload(**common)
        if target == "meta"
        else CreateGoogleAdsCampaignPayload(network="search", **common)
    )
    return await _materialize_governed_proposal(
        session,
        business_id=business_id,
        requested_by_user_id=requested_by_user_id,
        entity_type="campaign",
        entity_id=campaign.id,
        channel=target,
        connector_type=connector_type,
        action=AIAgentProposedAction(
            action_type=action_type,
            description=(
                f"Create the approved internal campaign proposal “{campaign.name}” "
                f"in {connector_type}. This proposal does not launch or spend."
            ),
            risk_level="high",
            requires_approval=True,
            action_payload=payload,
        ),
    )


async def prepare_content_publish_action(
    session: AsyncSession,
    *,
    business_id: UUID,
    content_id: UUID,
    requested_by_user_id: UUID,
    channel: SocialActionChannel | None,
) -> dict[str, object]:
    content = await get_content(
        session, business_id=business_id, content_id=content_id
    )
    if content.status not in {"approved", "scheduled", "ready_to_publish"}:
        raise MarketingValidationError(
            "Content must be reviewed and approved before a publish action can be prepared"
        )
    target = channel or content.channel
    if target not in {"facebook", "instagram"}:
        raise MarketingValidationError("Content does not target a supported social channel")
    if channel is not None and content.channel not in {channel, "meta", "other"}:
        raise MarketingValidationError("Requested channel conflicts with the content proposal")
    connector_type = target
    action_type = "publish_social_post"
    existing = await _existing_link(
        session,
        business_id=business_id,
        entity_type="content",
        entity_id=content.id,
        channel=target,
        action_type=action_type,
    )
    if existing is not None:
        return await _proposal_response(session, existing)

    body = content.body
    if content.cta:
        body = f"{body}\n\n{content.cta}"
    return await _materialize_governed_proposal(
        session,
        business_id=business_id,
        requested_by_user_id=requested_by_user_id,
        entity_type="content",
        entity_id=content.id,
        channel=target,
        connector_type=connector_type,
        action=AIAgentProposedAction(
            action_type=action_type,
            description=f"Publish the reviewed internal content proposal “{content.title}” to {target}.",
            risk_level="high",
            requires_approval=True,
            action_payload=PublishSocialPostPayload(
                platform=target,
                content=body[:10_000],
                media_refs=[],
            ),
        ),
    )


async def _materialize_governed_proposal(
    session: AsyncSession,
    *,
    business_id: UUID,
    requested_by_user_id: UUID,
    entity_type: str,
    entity_id: UUID,
    channel: str,
    connector_type: str,
    action: AIAgentProposedAction,
) -> dict[str, object]:
    task = f"Prepare governed {entity_type} action for {entity_id} on {channel}"
    execution = await create_running_ai_agent_execution(
        session,
        business_id=business_id,
        requested_by_user_id=requested_by_user_id,
        role="cmo",
        task=task,
        provider_name="internal",
        model_name="deterministic-action-materializer-v1",
        trigger_type="api",
    )
    revision = sha256(f"{business_id}:{entity_type}:{entity_id}:{channel}".encode()).hexdigest()
    await finalize_successful_ai_agent_execution(
        session,
        business_id=business_id,
        execution_id=execution.id,
        result=AIAgentExecutionResult(
            business_id=business_id,
            role="cmo",
            context_revision=revision,
            context_source_count=0,
            business_brain_source_count=0,
            memory_source_count=0,
            output=AIAgentStructuredOutput(
                status="needs_approval",
                summary=(
                    "Prepared a structured internal action proposal. No connector was called "
                    "and no external side effect occurred."
                ),
                recommendations=[],
                proposed_actions=[action],
            ),
        ),
    )
    actions = await materialize_ai_actions(
        session, business_id=business_id, execution_id=execution.id
    )
    governed = await govern_materialized_ai_actions(
        session,
        business_id=business_id,
        actions=actions,
        requested_by_user_id=requested_by_user_id,
    )
    if len(governed) != 1:
        raise MarketingPersistenceError
    governed_action = governed[0]
    link = MarketingActionProposal(
        business_id=business_id,
        entity_type=entity_type,
        entity_id=entity_id,
        channel=channel,
        action_type=governed_action.action.action_type,
        connector_type=connector_type,
        execution_id=execution.id,
        ai_action_id=governed_action.action.id,
        approval_id=(governed_action.approval.id if governed_action.approval else None),
    )
    session.add(link)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise MarketingPersistenceError from None
    return await _proposal_response(session, link)


async def _existing_link(
    session: AsyncSession,
    *,
    business_id: UUID,
    entity_type: str,
    entity_id: UUID,
    channel: str,
    action_type: str,
) -> MarketingActionProposal | None:
    try:
        return await session.scalar(
            select(MarketingActionProposal).where(
                MarketingActionProposal.business_id == business_id,
                MarketingActionProposal.entity_type == entity_type,
                MarketingActionProposal.entity_id == entity_id,
                MarketingActionProposal.channel == channel,
                MarketingActionProposal.action_type == action_type,
            )
        )
    except SQLAlchemyError:
        raise MarketingPersistenceError from None


async def _proposal_response(
    session: AsyncSession, link: MarketingActionProposal
) -> dict[str, object]:
    try:
        action = await session.scalar(
            select(AIAction).where(
                AIAction.business_id == link.business_id,
                AIAction.id == link.ai_action_id,
            )
        )
        approval = None
        if link.approval_id is not None:
            approval = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.business_id == link.business_id,
                    ApprovalRequest.id == link.approval_id,
                )
            )
    except SQLAlchemyError:
        raise MarketingPersistenceError from None
    if action is None:
        raise MarketingPersistenceError
    connector_state = await _connector_state(
        session,
        business_id=link.business_id,
        connector_type=link.connector_type,
        action_type=link.action_type,
    )
    return {
        "id": link.id,
        "business_id": link.business_id,
        "entity_type": link.entity_type,
        "entity_id": link.entity_id,
        "channel": link.channel,
        "connector_type": link.connector_type,
        "execution_id": link.execution_id,
        "ai_action_id": link.ai_action_id,
        "action_type": link.action_type,
        "action_status": action.status,
        "policy_decision": action.policy_decision,
        "policy_reason_code": action.policy_reason_code,
        "approval_id": link.approval_id,
        "approval_status": approval.status if approval is not None else None,
        **connector_state,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
    }


async def _connector_state(
    session: AsyncSession,
    *,
    business_id: UUID,
    connector_type: str,
    action_type: str,
) -> dict[str, object]:
    definition = None
    try:
        definition = require_connector(connector_type)
    except IntegrationValidationError:
        pass
    if definition is None:
        return {
            "connector_state": "provider_disabled",
            "connector_message": (
                f"No authenticated connector definition is registered for {connector_type}. "
                "No execution attempt was created."
            ),
        }
    try:
        connection = await session.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.business_id == business_id,
                IntegrationConnection.connector_type == connector_type,
                IntegrationConnection.status == "connected",
                IntegrationConnection.authentication_state == "authorized",
            )
        )
    except SQLAlchemyError:
        raise MarketingPersistenceError from None
    if connection is None:
        state = "connection_required"
        message = f"Connect {connector_type} before external execution can be attempted."
    else:
        if not connector_action_adapters.supports(connector_type, action_type):
            state = "provider_disabled"
            message = (
                f"{connector_type} is connected for supported reads, but this connection "
                "does not provide the required external-write capability. No execution "
                "attempt was created."
            )
        else:
            state = "ready_after_approval"
            message = "Connector execution is available after policy and approval requirements are satisfied."
    return {"connector_state": state, "connector_message": message}


def _campaign_channel(campaign: Campaign, requested: CampaignActionChannel | None) -> str:
    supported = [item for item in campaign.channels if item in {"meta", "google_ads"}]
    if requested is not None:
        if requested not in supported:
            raise MarketingValidationError("Requested advertising channel is not in the campaign proposal")
        return requested
    if not supported:
        raise MarketingValidationError("Campaign has no supported advertising channel")
    return supported[0]


async def _trusted_campaign_audience(
    session: AsyncSession, *, business_id: UUID, campaign: Campaign
) -> tuple[list[str], int | None, int | None]:
    values = list(campaign.geographic_targeting)
    min_age: int | None = None
    max_age: int | None = None
    if campaign.audience_hypothesis_id is not None:
        try:
            hypothesis = await session.scalar(
                select(AudienceHypothesis).where(
                    AudienceHypothesis.business_id == business_id,
                    AudienceHypothesis.id == campaign.audience_hypothesis_id,
                )
            )
        except SQLAlchemyError:
            raise MarketingPersistenceError from None
        if hypothesis is not None:
            values.extend(hypothesis.geographic_areas)
            min_age = hypothesis.min_age
            max_age = hypothesis.max_age
    countries = list(dict.fromkeys(
        value.strip().upper()
        for value in values
        if isinstance(value, str) and len(value.strip()) == 2 and value.strip().isalpha()
    ))[:25]
    return countries, min_age, max_age


def _campaign_objective(value: str) -> str:
    normalized = value.casefold()
    if "sale" in normalized or "purchase" in normalized or "revenue" in normalized:
        return "sales"
    if "lead" in normalized or "appointment" in normalized or "booking" in normalized:
        return "leads"
    if "traffic" in normalized or "visit" in normalized:
        return "traffic"
    if "engage" in normalized:
        return "engagement"
    return "awareness"
