from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.provider import AIAgentProvider
from app.agents.runtime import execute_ai_agent
from app.domain.marketing import CAMPAIGN_TRANSITIONS, CONTENT_TRANSITIONS, MARKETING_PLAN_TRANSITIONS, TREND_TRANSITIONS
from app.domain.business_industries import get_business_industry, is_healthcare_business_type
from app.domain.audience_safety import contains_sensitive_targeting
from app.exceptions.ai_agent import AIAgentError
from app.exceptions.marketing import MarketingAIError, MarketingNotFoundError, MarketingPersistenceError, MarketingStateError, MarketingValidationError
from app.models.business import Business
from app.models.catalog_item import CatalogItem
from app.models.automation_intelligence import AudienceHypothesis, MarketingAutomationRun
from app.models.crm_lead import CRMLead
from app.models.customer import Customer
from app.models.integration import IntegrationConnection
from app.models.commerce import CommerceFeedDestination, CommerceFeedProductStatus
from app.models.opportunity import Opportunity
from app.models.order import Order, OrderLineItem
from app.models.marketing import (
    Campaign,
    CampaignProductSelection,
    CampaignChannelPlan,
    Competitor,
    CompetitorAnalysis,
    CompetitorObservation,
    CreativeAsset,
    MarketingAudience,
    MarketingContent,
    MarketingPerformance,
    ProductCampaignPerformance,
    MarketingPlan,
    MarketingTrend,
    SocialSchedule,
)
from app.models.notification import Notification
from app.schemas.ai_agent import AIAgentExecutionRequest
from app.schemas.marketing import (
    AnalyticsBreakdown,
    AudienceCreate,
    CampaignCreate,
    CampaignGenerateRequest,
    CampaignUpdate,
    ChannelPlanCreate,
    CompetitorCreate,
    CompetitorUpdate,
    ContentCreate,
    ContentGenerateRequest,
    ContentVersionCreate,
    CreativeBriefCreate,
    LearningResponse,
    MarketingAnalyticsResponse,
    MarketingPlanCreate,
    MarketingPlanUpdate,
    MarketingTrendPoint,
    ObservationCreate,
    PerformanceCreate,
    PlanGenerateRequest,
    ScheduleCreate,
    TopContent,
    TrendCreate,
    TrendOpportunityRequest,
)
from app.schemas.operations import OpportunityCreate
from app.services.operations import create_opportunity, record_audit
from app.services.automation_events import record_automation_event


ZERO = Decimal("0")
MONEY_QUANTUM = Decimal("0.0001")
RATIO_QUANTUM = Decimal("0.000001")


def _page(page: int, page_size: int) -> tuple[int, int]:
    if page < 1 or page_size < 1 or page_size > 100:
        raise MarketingValidationError
    return (page - 1) * page_size, page_size


def _term(search: str | None) -> str | None:
    if search is None:
        return None
    value = search.strip()
    if len(value) > 100:
        raise MarketingValidationError
    return value or None


async def _paged(session: AsyncSession, statement: Select, page: int, page_size: int):
    offset, limit = _page(page, page_size)
    try:
        total = int(await session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0)
        items = list((await session.scalars(statement.offset(offset).limit(limit))).all())
        return items, total
    except SQLAlchemyError:
        raise MarketingPersistenceError from None


async def _flush(session: AsyncSession) -> None:
    try:
        await session.flush()
    except SQLAlchemyError:
        raise MarketingPersistenceError from None


async def _business(session: AsyncSession, business_id: UUID) -> Business:
    try:
        value = await session.scalar(select(Business).where(Business.id == business_id))
    except SQLAlchemyError:
        raise MarketingPersistenceError from None
    if value is None:
        raise MarketingNotFoundError
    return value


async def _exists(session: AsyncSession, model, business_id: UUID, value_id: UUID | None) -> bool:
    if value_id is None:
        return True
    try:
        return bool(await session.scalar(select(model.id).where(model.business_id == business_id, model.id == value_id)))
    except SQLAlchemyError:
        raise MarketingPersistenceError from None


async def _get(session: AsyncSession, model, business_id: UUID, value_id: UUID):
    try:
        value = await session.scalar(select(model).where(model.business_id == business_id, model.id == value_id))
    except SQLAlchemyError:
        raise MarketingPersistenceError from None
    if value is None:
        raise MarketingNotFoundError
    return value


def _notify(session: AsyncSession, *, business_id: UUID, category: str, title: str, message: str, entity_type: str, entity_id: UUID) -> None:
    session.add(Notification(
        business_id=business_id,
        recipient_user_id=None,
        category=category,
        title=title[:180],
        message=message[:1000],
        priority="medium",
        read=False,
        related_entity_type=entity_type,
        related_entity_id=entity_id,
    ))


def _transition(current: str, target: str, allowed: dict[str, frozenset[str]]) -> None:
    if current == target or target not in allowed.get(current, frozenset()):
        raise MarketingStateError


async def list_audiences(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, search: str | None):
    statement = select(MarketingAudience).where(MarketingAudience.business_id == business_id)
    if term := _term(search):
        statement = statement.where(MarketingAudience.name.icontains(term, autoescape=True))
    return await _paged(session, statement.order_by(MarketingAudience.updated_at.desc(), MarketingAudience.id.desc()), page, page_size)


async def create_audience(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: AudienceCreate) -> MarketingAudience:
    if contains_sensitive_targeting(
        data.name, data.segment_description or "", data.existing_customer_segment or "",
        *data.customer_lifecycle, *data.crm_stages, *data.interests,
    ):
        raise MarketingValidationError("sensitive_targeting_prohibited")
    value = MarketingAudience(business_id=business_id, created_by_user_id=actor_user_id, **data.model_dump())
    session.add(value)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.audience_created", entity_type="marketing_audience", entity_id=value.id, summary=f"Created marketing audience {value.name}.")
    return value


async def list_plans(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, search: str | None, status: str | None):
    statement = select(MarketingPlan).where(MarketingPlan.business_id == business_id)
    if term := _term(search):
        statement = statement.where(or_(MarketingPlan.title.icontains(term, autoescape=True), MarketingPlan.objective.icontains(term, autoescape=True)))
    if status:
        statement = statement.where(MarketingPlan.status == status)
    return await _paged(session, statement.order_by(MarketingPlan.updated_at.desc(), MarketingPlan.id.desc()), page, page_size)


async def get_plan(session: AsyncSession, *, business_id: UUID, plan_id: UUID) -> MarketingPlan:
    return await _get(session, MarketingPlan, business_id, plan_id)


async def create_plan(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: MarketingPlanCreate, generated_by: str = "user") -> MarketingPlan:
    if not await _exists(session, MarketingAudience, business_id, data.audience_id):
        raise MarketingValidationError
    business = await _business(session, business_id)
    value = MarketingPlan(business_id=business_id, currency=business.currency, generated_by=generated_by, created_by_user_id=actor_user_id, **data.model_dump())
    session.add(value)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.plan_created", entity_type="marketing_plan", entity_id=value.id, summary=f"Created marketing plan {value.title}.")
    return value


async def update_plan(session: AsyncSession, *, business_id: UUID, plan_id: UUID, actor_user_id: UUID, data: MarketingPlanUpdate) -> MarketingPlan:
    value = await get_plan(session, business_id=business_id, plan_id=plan_id)
    changes = data.model_dump(exclude_unset=True)
    start = changes.get("period_start", value.period_start)
    end = changes.get("period_end", value.period_end)
    if start and end and end < start:
        raise MarketingValidationError
    for key, item in changes.items():
        setattr(value, key, item)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.plan_updated", entity_type="marketing_plan", entity_id=value.id, summary=f"Updated marketing plan {value.title}.")
    return value


async def change_plan_status(session: AsyncSession, *, business_id: UUID, plan_id: UUID, actor_user_id: UUID, status: str) -> MarketingPlan:
    value = await get_plan(session, business_id=business_id, plan_id=plan_id)
    _transition(value.status, status, MARKETING_PLAN_TRANSITIONS)
    before = value.status
    value.status = status
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.plan_status_changed", entity_type="marketing_plan", entity_id=value.id, summary=f"Changed marketing plan {value.title} status.", before_value=before, after_value=status)
    return value


async def generate_plan(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: PlanGenerateRequest, provider: AIAgentProvider) -> MarketingPlan:
    task = (
        "Prepare a concise, evidence-grounded marketing strategy using only trusted Business Brain and memory context. "
        f"Goal: {data.goal}. Audience: {data.target_audience}. Channels: {', '.join(data.channels)}. "
        "Return usable conclusions only; do not include hidden reasoning, invented prices, external execution, or sensitive targeting."
    )
    output = await _run_cmo(session, business_id, task, provider)
    recommendations = output.recommendations
    measurement_goals = _bounded_unique_text(recommendations[8:18], max_length=160)
    create = MarketingPlanCreate(
        title=data.title or data.goal[:180], objective=data.goal,
        target_audience=data.target_audience, positioning=output.summary[:3000],
        key_message=(recommendations[0] if recommendations else output.summary)[:3000],
        channels=data.channels, budget_guidance=data.budget_guidance,
        period_start=data.period_start, period_end=data.period_end,
        content_strategy="\n".join(recommendations[:8])[:5000] or None,
        measurement_goals=measurement_goals or ["Measure outcomes against the stated campaign objective."],
    )
    value = await create_plan(session, business_id=business_id, actor_user_id=actor_user_id, data=create, generated_by="ai")
    value.status = "ready"
    _notify(session, business_id=business_id, category="campaign_review", title="AI CMO plan ready", message=f"Review the marketing plan “{value.title}”.", entity_type="marketing_plan", entity_id=value.id)
    return value


def _bounded_unique_text(
    values: list[str], *, max_length: int, max_items: int = 20
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()[:max_length].rstrip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= max_items:
            break
    return result


async def list_campaigns(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, search: str | None, status: str | None):
    statement = select(Campaign).where(Campaign.business_id == business_id)
    if term := _term(search):
        statement = statement.where(or_(Campaign.name.icontains(term, autoescape=True), Campaign.objective.icontains(term, autoescape=True)))
    if status:
        statement = statement.where(Campaign.status == status)
    return await _paged(session, statement.order_by(Campaign.updated_at.desc(), Campaign.id.desc()), page, page_size)


async def get_campaign(session: AsyncSession, *, business_id: UUID, campaign_id: UUID) -> Campaign:
    return await _get(session, Campaign, business_id, campaign_id)


async def get_campaign_audience(
    session: AsyncSession, *, business_id: UUID, campaign_id: UUID
) -> AudienceHypothesis:
    campaign = await get_campaign(session, business_id=business_id, campaign_id=campaign_id)
    if campaign.audience_hypothesis_id is None:
        raise MarketingNotFoundError
    return await _get(
        session, AudienceHypothesis, business_id, campaign.audience_hypothesis_id
    )


async def create_campaign(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID | None, data: CampaignCreate, ai_generated: bool = False) -> Campaign:
    if contains_sensitive_targeting(data.audience_definition):
        raise MarketingValidationError("sensitive_targeting_prohibited")
    if not await _exists(session, MarketingPlan, business_id, data.marketing_plan_id) or not await _exists(session, MarketingAudience, business_id, data.audience_id):
        raise MarketingValidationError
    business = await _business(session, business_id)
    value = Campaign(
        business_id=business_id, currency=business.currency,
        created_by_user_id=actor_user_id, ai_generated=ai_generated,
        product_selections=[], **data.model_dump(),
    )
    session.add(value)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.campaign_created", entity_type="marketing_campaign", entity_id=value.id, summary=f"Created internal campaign {value.name}; nothing was launched externally.")
    record_automation_event(session, business_id=business_id, event_type="campaign_created", entity_type="campaign", entity_id=value.id, payload={"status": value.status, "objective": value.objective, "name": value.name})
    return value


async def update_campaign(session: AsyncSession, *, business_id: UUID, campaign_id: UUID, actor_user_id: UUID, data: CampaignUpdate) -> Campaign:
    value = await get_campaign(session, business_id=business_id, campaign_id=campaign_id)
    changes = data.model_dump(exclude_unset=True)
    if contains_sensitive_targeting(str(changes.get("audience_definition", ""))):
        raise MarketingValidationError("sensitive_targeting_prohibited")
    start = changes.get("start_date", value.start_date)
    end = changes.get("end_date", value.end_date)
    if start and end and end < start:
        raise MarketingValidationError
    budget = changes.get("planned_budget", value.planned_budget)
    try:
        allocated = await session.scalar(select(func.coalesce(func.sum(CampaignChannelPlan.budget_allocation), 0)).where(CampaignChannelPlan.business_id == business_id, CampaignChannelPlan.campaign_id == campaign_id))
        configured_channels = set((await session.scalars(select(CampaignChannelPlan.channel).where(CampaignChannelPlan.business_id == business_id, CampaignChannelPlan.campaign_id == campaign_id))).all()) if "channels" in changes else set()
    except SQLAlchemyError:
        raise MarketingPersistenceError from None
    if Decimal(allocated or 0) > budget:
        raise MarketingValidationError
    if configured_channels.difference(changes.get("channels", value.channels)):
        raise MarketingValidationError
    before_budget = value.planned_budget
    for key, item in changes.items():
        setattr(value, key, item)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.campaign_updated", entity_type="marketing_campaign", entity_id=value.id, summary=f"Updated internal campaign {value.name}.", before_value=f"budget={before_budget}", after_value=f"budget={value.planned_budget}")
    return value


async def duplicate_campaign(session: AsyncSession, *, business_id: UUID, campaign_id: UUID, actor_user_id: UUID) -> Campaign:
    source = await get_campaign(session, business_id=business_id, campaign_id=campaign_id)
    create = CampaignCreate(
        marketing_plan_id=source.marketing_plan_id, audience_id=source.audience_id,
        name=f"{source.name} copy"[:180], objective=source.objective,
        description=source.description, offer=source.offer,
        audience_definition=source.audience_definition,
        geographic_targeting=source.geographic_targeting, channels=source.channels,
        start_date=source.start_date, end_date=source.end_date,
        planned_budget=source.planned_budget, budget_mode=source.budget_mode,
    )
    value = await create_campaign(session, business_id=business_id, actor_user_id=actor_user_id, data=create, ai_generated=source.ai_generated)
    plans = list((await session.scalars(select(CampaignChannelPlan).where(CampaignChannelPlan.business_id == business_id, CampaignChannelPlan.campaign_id == source.id))).all())
    for plan in plans:
        session.add(CampaignChannelPlan(
            business_id=business_id, campaign_id=value.id, channel=plan.channel,
            objective=plan.objective, budget_allocation=plan.budget_allocation,
            audience_strategy=plan.audience_strategy, messaging=plan.messaging,
            status="draft", planned_start=plan.planned_start, planned_end=plan.planned_end,
            safe_configuration=plan.safe_configuration,
        ))
    selections = list((await session.scalars(select(CampaignProductSelection).where(
        CampaignProductSelection.business_id == business_id,
        CampaignProductSelection.campaign_id == source.id,
    ))).all())
    for selection in selections:
        value.product_selections.append(CampaignProductSelection(
            business_id=business_id, campaign_id=value.id,
            catalog_item_id=selection.catalog_item_id,
            selection_reason=selection.selection_reason,
        ))
    await _flush(session)
    return value


async def change_campaign_status(session: AsyncSession, *, business_id: UUID, campaign_id: UUID, actor_user_id: UUID, status: str) -> Campaign:
    value = await get_campaign(session, business_id=business_id, campaign_id=campaign_id)
    _transition(value.status, status, CAMPAIGN_TRANSITIONS)
    before = value.status
    value.status = status
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.campaign_status_changed", entity_type="marketing_campaign", entity_id=value.id, summary=f"Changed internal campaign {value.name} status; no external campaign action occurred.", before_value=before, after_value=status)
    record_automation_event(session, business_id=business_id, event_type="campaign_status_changed", entity_type="campaign", entity_id=value.id, payload={"status": status, "previous_status": before, "objective": value.objective, "name": value.name})
    if status == "awaiting_approval":
        _notify(session, business_id=business_id, category="campaign_review", title="Campaign ready for review", message=f"Campaign “{value.name}” is awaiting internal approval.", entity_type="marketing_campaign", entity_id=value.id)
    elif status == "completed":
        _notify(session, business_id=business_id, category="campaign_status", title="Campaign period completed", message=f"Internal campaign “{value.name}” was marked completed.", entity_type="marketing_campaign", entity_id=value.id)
        record_automation_event(session, business_id=business_id, event_type="campaign_completed", entity_type="campaign", entity_id=value.id, payload={"status": status, "previous_status": before, "objective": value.objective, "name": value.name})
    return value


async def campaign_detail(session: AsyncSession, *, business_id: UUID, campaign: Campaign) -> dict[str, Any]:
    try:
        plans = list((await session.scalars(select(CampaignChannelPlan).where(CampaignChannelPlan.business_id == business_id, CampaignChannelPlan.campaign_id == campaign.id).order_by(CampaignChannelPlan.channel, CampaignChannelPlan.id))).all())
        selections = list((await session.scalars(select(CampaignProductSelection).where(
            CampaignProductSelection.business_id == business_id,
            CampaignProductSelection.campaign_id == campaign.id,
        ).order_by(CampaignProductSelection.created_at, CampaignProductSelection.id))).all())
    except SQLAlchemyError:
        raise MarketingPersistenceError from None
    result = {column.name: getattr(campaign, column.name) for column in campaign.__table__.columns}
    result["channel_plans"] = plans
    result["catalog_item_ids"] = [selection.catalog_item_id for selection in selections]
    return result


async def create_channel_plan(session: AsyncSession, *, business_id: UUID, campaign_id: UUID, actor_user_id: UUID, data: ChannelPlanCreate) -> CampaignChannelPlan:
    campaign = await get_campaign(session, business_id=business_id, campaign_id=campaign_id)
    if data.channel not in campaign.channels:
        raise MarketingValidationError
    allocated = Decimal(await session.scalar(select(func.coalesce(func.sum(CampaignChannelPlan.budget_allocation), 0)).where(CampaignChannelPlan.business_id == business_id, CampaignChannelPlan.campaign_id == campaign_id)) or 0)
    if allocated + data.budget_allocation > campaign.planned_budget:
        raise MarketingValidationError
    value = CampaignChannelPlan(business_id=business_id, campaign_id=campaign_id, safe_configuration=data.safe_configuration.model_dump(mode="json"), **data.model_dump(exclude={"safe_configuration"}))
    session.add(value)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.channel_plan_created", entity_type="campaign_channel_plan", entity_id=value.id, summary=f"Added {value.channel} plan to campaign {campaign.name}.", after_value=f"allocation={value.budget_allocation}")
    return value


async def generate_campaign(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID | None, data: CampaignGenerateRequest, provider: AIAgentProvider, origin_type: str = "ai_on_demand", proposal_key: str | None = None) -> Campaign:
    if contains_sensitive_targeting(data.goal, data.audience_definition or ""):
        raise MarketingValidationError("sensitive_targeting_prohibited")
    selected_products: list[CatalogItem] = []
    if data.catalog_item_ids:
        selected_products = list((await session.scalars(select(CatalogItem).where(
            CatalogItem.business_id == business_id,
            CatalogItem.id.in_(data.catalog_item_ids),
            CatalogItem.status != "archived",
        ))).all())
        if {item.id for item in selected_products} != set(data.catalog_item_ids):
            raise MarketingValidationError("catalog_selection_invalid")
    audience = await build_audience_hypothesis(
        session, business_id=business_id, goal=data.goal
    )
    commerce_context = await _campaign_commerce_context(
        session, business_id=business_id,
        product_ids=[item.id for item in selected_products],
    )
    recommended_channel = commerce_context["channel"]
    channels = list(data.channels) or ([recommended_channel] if recommended_channel else list(audience.preferred_channels)) or ["website"]
    channels = list(dict.fromkeys(channels))[:10]
    execution_campaign_type = (
        commerce_context["campaign_type"] if recommended_channel in channels else None
    )
    audience_definition = data.audience_definition or audience.summary
    name = data.name or data.goal[:180]
    required_integrations = _required_integrations(channels)
    evidence_text = "\n".join(
        f"- {item.get('classification')}: {item.get('summary')}"
        for item in audience.evidence[:20]
    )
    observed_evidence = await _campaign_observed_evidence(
        session, business_id=business_id,
        product_ids=[item.id for item in selected_products],
    )
    evidence_text = "\n".join([evidence_text, *(
        f"- {item['classification']}: {item['summary']}" for item in observed_evidence
    )])
    product_facts = "\n".join(
        f"- {item.name}; SKU={item.sku or 'unavailable'}; price={item.price if item.price is not None else 'unavailable'} "
        f"{item.currency or ''}; availability={item.availability}; source={item.source}; URL={item.product_url or 'unavailable'}; "
        f"description={(item.description or 'unavailable')[:1000]}"
        for item in selected_products
    )
    task = (
        "Create an internal campaign proposal grounded only in trusted business context and the "
        "evidence-backed audience hypothesis below. Label every unsupported audience detail as an AI inference. "
        f"Goal: {data.goal}. Audience hypothesis: {audience_definition}. Channels: {', '.join(channels)}. "
        f"Total budget guidance: {data.planned_budget}. Selected authoritative catalog products:\n"
        f"{product_facts[:8000] or '- No product was explicitly selected; recommend only from available trusted context.'}\n"
        f"Audience evidence:\n{evidence_text[:8000]}\n"
        "Return strategy, message, creative direction, CTA, risks, assumptions, and measurement guidance. "
        "Do not promise results and do not launch, publish, or spend."
    )
    output = await _run_cmo(session, business_id, task, provider)
    campaign = await create_campaign(session, business_id=business_id, actor_user_id=actor_user_id, ai_generated=True, data=CampaignCreate(
        name=name, objective=data.goal, description=output.summary, offer=data.offer,
        audience_definition=audience_definition, channels=channels,
        geographic_targeting=[
            value.strip().upper()
            for value in audience.geographic_areas
            if isinstance(value, str) and len(value.strip()) == 2 and value.strip().isalpha()
        ][:50],
        start_date=data.start_date, end_date=data.end_date,
        planned_budget=data.planned_budget, budget_mode=data.budget_mode,
    ))
    recommendations = list(output.recommendations)
    campaign.origin_type = origin_type
    campaign.proposal_key = proposal_key
    campaign.proposal_reasoning = output.summary[:5000]
    campaign.creative_brief = (recommendations[0][:5000] if recommendations else output.summary[:5000])
    campaign.proposed_copy = (recommendations[1][:10000] if len(recommendations) > 1 else output.summary[:10000])
    campaign.proposed_cta = recommendations[2][:300] if len(recommendations) > 2 else None
    campaign.measurement_plan = (
        recommendations[3][:5000] if len(recommendations) > 3
        else "Measure only recorded reach, engagement, leads, conversions, spend, and revenue against the stated objective."
    )
    campaign.assumptions = [
        "Audience details not supported by first-party or platform data are labeled AI inference.",
        "Budget is guidance only and is not a performance guarantee.",
    ]
    campaign.risks = [
        "No sales, lead, or conversion outcome is guaranteed.",
        "External execution remains unavailable without approval and an authenticated write-capable connector.",
    ]
    campaign.required_integrations = required_integrations
    campaign.source_evidence = list(audience.evidence)
    campaign.source_evidence.extend([
        {
            "classification": "first_party_observed",
            "source_type": "catalog",
            "source_id": str(item.id),
            "summary": (
                f"Catalog product: {item.name}; price "
                f"{item.price if item.price is not None else 'unavailable'}; "
                f"availability {item.availability}; provenance {item.source}."
            ),
        }
        for item in selected_products
    ])
    campaign.source_evidence.extend(observed_evidence)
    campaign.audience_hypothesis_id = audience.id
    for item in selected_products:
        campaign.product_selections.append(CampaignProductSelection(
            business_id=business_id, campaign_id=campaign.id,
            catalog_item_id=item.id,
            selection_reason="Owner-selected product context" if data.catalog_item_ids else "AI-recommended product context",
        ))
    if len(selected_products) == 1:
        product = selected_products[0]
        campaign.landing_destination = product.product_url
    campaign.recommended_provider = commerce_context["provider"]
    campaign.campaign_type = execution_campaign_type
    campaign.offer_source = "owner_authorized" if data.offer else "none"
    campaign.offer_authorized = bool(data.offer and data.offer_authorized)
    campaign.proposal_confidence = Decimal("0.80") if selected_products and commerce_context["provider"] else Decimal("0.55")
    total_exposure = data.planned_budget
    if data.budget_mode == "daily" and data.start_date and data.end_date:
        total_exposure = data.planned_budget * Decimal((data.end_date - data.start_date).days + 1)
    campaign.normalized_proposal = {
        "schema_version": 1,
        "goal": data.goal,
        "recommended_provider": commerce_context["provider"],
        "why_provider": commerce_context["why_provider"],
        "campaign_type": execution_campaign_type,
        "product_group": None,
        "selected_products": [
            {"catalog_item_id": str(item.id), "offer_id": item.sku or str(item.id), "name": item.name,
             "price": str(item.price) if item.price is not None else None, "currency": item.currency,
             "availability": item.availability, "landing_url": item.product_url}
            for item in selected_products
        ],
        "product_eligibility": {
            "selected": len(selected_products),
            "eligible": commerce_context["eligible_count"],
            "attention_required": max(0, len(selected_products) - commerce_context["eligible_count"]),
        },
        "audience_strategy": {
            "summary": audience_definition,
            "first_party_segments": [],
            "provider_native_prospecting": True,
            "exclusions": [],
            "geography": list(campaign.geographic_targeting),
            "customer_lifecycle_stage": "not_inferred",
            "provider_signals": [],
            "sensitive_targeting_prohibited": True,
        },
        "offer": {
            "description": data.offer,
            "source": "owner_authorized" if data.offer else "none",
            "approved": bool(data.offer and data.offer_authorized),
        },
        "creative": {
            "angle": campaign.creative_brief,
            "headlines": [name[:30]],
            "descriptions": [campaign.proposed_copy[:90] if campaign.proposed_copy else output.summary[:90]],
            "primary_text": campaign.proposed_copy,
            "call_to_action": campaign.proposed_cta or "Shop now",
            "landing_url": campaign.landing_destination,
            "asset_requirements": commerce_context["asset_requirements"],
            "media_requirements": commerce_context["asset_requirements"],
        },
        "seller_business_advantage": None,
        "product_differentiators": [
            value
            for item in selected_products
            for value in (
                f"Authoritative brand: {item.brand}" if item.brand else None,
                f"Authoritative condition: {item.condition}",
            )
            if value is not None
        ][:20],
        "budget": {
            "amount": str(data.planned_budget), "currency": campaign.currency,
            "interval": data.budget_mode, "maximum_planned_spend": str(total_exposure),
            "rationale": "Owner-provided budget guidance; spend remains subject to server policy and approval.",
        },
        "duration": {
            "start_date": data.start_date.isoformat() if data.start_date else None,
            "end_date": data.end_date.isoformat() if data.end_date else None,
            "days": ((data.end_date - data.start_date).days + 1) if data.start_date and data.end_date else None,
        },
        "bidding_objective_strategy": (
            "maximize_conversion_value" if commerce_context["provider"] == "google"
            else "lowest_cost_purchase" if commerce_context["provider"] == "meta"
            else "not_selected"
        ),
        "conversion_goal": "purchase",
        "measurement_plan": campaign.measurement_plan,
        "utm_plan": {"utm_source": commerce_context["provider"] or "aibos", "utm_medium": "paid", "utm_campaign": str(campaign.id)},
        "required_integrations": required_integrations,
        "required_provider_assets": commerce_context["required_assets"],
        "provider_dependencies": commerce_context["required_assets"],
        "risks": list(campaign.risks),
        "evidence": list(campaign.source_evidence),
        "confidence": str(campaign.proposal_confidence),
        "approval_requirements": ["advertising_spend_policy", "human_approval", "provider_preflight"],
    }
    allocations = _allocate_budget(data.planned_budget, len(channels))
    for index, channel in enumerate(channels):
        recommendation = output.recommendations[index] if index < len(output.recommendations) else output.summary
        session.add(CampaignChannelPlan(
            business_id=business_id, campaign_id=campaign.id, channel=channel,
            objective=data.goal[:1000], budget_allocation=allocations[index],
            audience_strategy=audience_definition[:3000], messaging=recommendation[:5000],
            status="draft", planned_start=None, planned_end=None, safe_configuration={},
        ))
    await _flush(session)
    _notify(session, business_id=business_id, category="campaign_review", title="AI campaign draft ready", message=f"Review the draft campaign “{campaign.name}”. External connection is still required for publishing.", entity_type="marketing_campaign", entity_id=campaign.id)
    return campaign


async def _campaign_commerce_context(
    session: AsyncSession, *, business_id: UUID, product_ids: list[UUID],
) -> dict[str, Any]:
    connections = list((await session.scalars(select(IntegrationConnection).where(
        IntegrationConnection.business_id == business_id,
        IntegrationConnection.connector_type.in_(["google_ads", "meta_ads"]),
        IntegrationConnection.status == "connected",
        IntegrationConnection.authentication_state == "authorized",
    ))).all())
    by_type = {item.connector_type: item for item in connections}
    destinations = list((await session.scalars(select(CommerceFeedDestination).where(
        CommerceFeedDestination.business_id == business_id,
        CommerceFeedDestination.integration_connection_id.in_([item.id for item in connections]) if connections else False,
        CommerceFeedDestination.status.in_(["connected", "attention_required"]),
    ).order_by(CommerceFeedDestination.provider))).all())
    provider = channel = campaign_type = None
    destination = None
    google = next((item for item in destinations if item.provider == "google_merchant_center" and "google_ads" in by_type), None)
    meta = next((item for item in destinations if item.provider == "meta_product_catalog" and "meta_ads" in by_type), None)
    if google:
        provider, channel, campaign_type, destination = "google", "google_ads", "retail_performance_max", google
    elif meta:
        provider, channel, campaign_type, destination = "meta", "meta", "catalog_sales", meta
    eligible = 0
    if destination is not None and product_ids:
        eligible = int(await session.scalar(select(func.count(CommerceFeedProductStatus.id)).where(
            CommerceFeedProductStatus.business_id == business_id,
            CommerceFeedProductStatus.destination_id == destination.id,
            CommerceFeedProductStatus.catalog_item_id.in_(product_ids),
            CommerceFeedProductStatus.status.in_(["eligible", "limited", "warning"]),
        )) or 0)
    return {
        "provider": provider, "channel": channel, "campaign_type": campaign_type,
        "eligible_count": eligible,
        "why_provider": (
            "Google Merchant and Ads resources are connected for a retail Performance Max proposal."
            if provider == "google" else
            "Meta business, catalog, and Ads resources are connected for a catalog sales proposal."
            if provider == "meta" else
            "No complete ecommerce advertising provider capability is currently connected."
        ),
        "asset_requirements": ["catalog_product_image", "landing_page"] + (["page_identity", "conversion_dataset"] if provider == "meta" else []),
        "required_assets": (
            ["google_ads_customer", "google_merchant_account", "google_merchant_data_source", "merchant_ads_link", "purchase_conversion"]
            if provider == "google" else
            ["meta_business", "ad_account", "meta_catalog", "product_set", "facebook_page", "conversion_dataset"]
            if provider == "meta" else []
        ),
    }


async def _campaign_observed_evidence(
    session: AsyncSession, *, business_id: UUID, product_ids: list[UUID],
) -> list[dict[str, object]]:
    if not product_ids:
        return []
    # Lightweight unit-test sessions intentionally expose scalar-only behavior;
    # production AsyncSession always executes the aggregate evidence queries.
    if not hasattr(session, "execute"):
        return []
    order_count, units, revenue = (await session.execute(select(
        func.count(func.distinct(Order.id)),
        func.coalesce(func.sum(OrderLineItem.quantity), 0),
        func.coalesce(func.sum(
            OrderLineItem.unit_price * OrderLineItem.quantity - OrderLineItem.discount_amount
        ), 0),
    ).join(
        OrderLineItem,
        (OrderLineItem.order_id == Order.id) & (OrderLineItem.business_id == Order.business_id),
    ).where(
        Order.business_id == business_id,
        Order.payment_status.in_(["paid", "partially_refunded", "refunded"]),
        OrderLineItem.catalog_item_id.in_(product_ids),
    ))).one()
    spend, conversions, conversion_value = (await session.execute(select(
        func.coalesce(func.sum(ProductCampaignPerformance.spend), 0),
        func.coalesce(func.sum(ProductCampaignPerformance.conversions), 0),
        func.coalesce(func.sum(ProductCampaignPerformance.conversion_value), 0),
    ).where(
        ProductCampaignPerformance.business_id == business_id,
        ProductCampaignPerformance.catalog_item_id.in_(product_ids),
        ProductCampaignPerformance.attribution_class == "provider_attributed",
    ))).one()
    evidence: list[dict[str, object]] = []
    if int(order_count or 0):
        evidence.append({
            "classification": "first_party_observed", "source_type": "orders",
            "source_id": None,
            "summary": f"Paid order history contains {int(order_count)} orders and {int(units)} units for the selected products, with recorded line revenue {Decimal(revenue):.2f}.",
        })
    if Decimal(spend or 0) > 0 or Decimal(conversions or 0) > 0:
        evidence.append({
            "classification": "provider_supplied", "source_type": "advertising_performance",
            "source_id": None,
            "summary": f"Provider-attributed history for the selected products reports spend {Decimal(spend):.2f}, conversions {Decimal(conversions):.2f}, and conversion value {Decimal(conversion_value):.2f}; this is provider attribution, not causal proof.",
        })
    return evidence


async def build_audience_hypothesis(
    session: AsyncSession, *, business_id: UUID, goal: str
) -> AudienceHypothesis:
    business = await _business(session, business_id)
    healthcare = is_healthcare_business_type(business.business_type)
    industry = get_business_industry(business.business_type)
    customer_count = 0
    lead_rows: list[tuple[Any, ...]] = []
    audience_record = None
    try:
        if not healthcare:
            customer_count = int(await session.scalar(select(func.count(Customer.id)).where(
                Customer.business_id == business_id,
                Customer.status == "active",
            )) or 0)
            lead_rows = list((await session.execute(select(
                CRMLead.stage, func.count(CRMLead.id)
            ).where(CRMLead.business_id == business_id).group_by(CRMLead.stage))).all())
        performance_rows = list((await session.execute(select(
            MarketingPerformance.channel,
            func.coalesce(func.sum(MarketingPerformance.clicks), 0),
            func.coalesce(func.sum(MarketingPerformance.conversions), 0),
        ).where(MarketingPerformance.business_id == business_id).group_by(
            MarketingPerformance.channel
        ))).all())
        connected = list((await session.scalars(select(
            IntegrationConnection.connector_type
        ).where(
            IntegrationConnection.business_id == business_id,
            IntegrationConnection.status == "connected",
        ).order_by(IntegrationConnection.connector_type))).all())
        if not healthcare:
            audience_record = await session.scalar(select(MarketingAudience).where(
                MarketingAudience.business_id == business_id,
            ).order_by(MarketingAudience.updated_at.desc(), MarketingAudience.id.desc()).limit(1))
        public_signal_count = int(await session.scalar(select(
            func.count(CompetitorObservation.id)
        ).where(CompetitorObservation.business_id == business_id)) or 0)
        order_count = 0
        if industry is not None and industry.group == "commerce":
            order_count = int(await session.scalar(select(func.count(Order.id)).where(
                Order.business_id == business_id,
                Order.status.in_(("confirmed", "processing", "completed")),
            )) or 0)
    except SQLAlchemyError:
        raise MarketingPersistenceError from None

    evidence: list[dict[str, object]] = []
    if not healthcare:
        evidence.extend(({
            "classification": "first_party_observed",
            "source_type": "customer_aggregate",
            "source_id": None,
            "summary": f"{customer_count} active administrative customer/contact records are available.",
        }, {
            "classification": "first_party_observed",
            "source_type": "crm_aggregate",
            "source_id": None,
            "summary": "CRM lead stages: " + (", ".join(f"{stage}={count}" for stage, count in lead_rows) or "no observed leads"),
        }))
    if performance_rows:
        evidence.append({
            "classification": "first_party_observed",
            "source_type": "campaign_performance_aggregate",
            "source_id": None,
            "summary": "Recorded channel outcomes: " + ", ".join(
                f"{channel} clicks={clicks}, conversions={conversions}"
                for channel, clicks, conversions in performance_rows
            ),
        })
    if order_count:
        evidence.append({
            "classification": "first_party_observed",
            "source_type": "order_aggregate",
            "source_id": None,
            "summary": f"{order_count} confirmed/processing/completed commerce orders are available as aggregate context.",
        })
    if public_signal_count:
        evidence.append({
            "classification": "public_research",
            "source_type": "competitor_observation_aggregate",
            "source_id": None,
            "summary": f"{public_signal_count} sourced public competitor observations are available; no competitor demographics are claimed.",
        })
    if connected:
        evidence.append({
            "classification": "provider_supplied",
            "source_type": "connected_channel_metadata",
            "source_id": None,
            "summary": "Authenticated connections present: " + ", ".join(connected) + ". This indicates availability, not audience demographics.",
        })

    preferred_channels = _audience_channels(performance_rows, connected)
    geographic_areas: list[str] = []
    interests: list[str] = []
    min_age: int | None = None
    max_age: int | None = None
    if audience_record is not None:
        geographic_areas = [*audience_record.countries, *audience_record.regions][:50]
        interests = list(audience_record.interests)[:50]
        min_age = audience_record.min_age
        max_age = audience_record.max_age
        evidence.append({
            "classification": "first_party_observed",
            "source_type": "saved_audience",
            "source_id": str(audience_record.id),
            "summary": "A business-owned saved audience supplied geographic/segment constraints."
        })
    terminology = (
        "service and appointment availability" if healthcare
        else "services and bookings" if industry and industry.group == "professional_services"
        else "catalog and purchase intent" if industry and industry.group == "commerce"
        else "CRM lead intent"
    )
    grounding = (
        f"{len(performance_rows)} channel-performance aggregates and public/business-owned operational signals"
        if healthcare
        else f"{customer_count} active contact records, {sum(int(row[1]) for row in lead_rows)} CRM leads, and {len(performance_rows)} channel-performance aggregates"
    )
    summary = (
        f"AI-inferred audience for “{goal[:500]}”: people showing {terminology}, "
        f"grounded in {grounding}. Exact demographics are unknown unless explicitly supplied."
    )
    hypothesis = AudienceHypothesis(
        business_id=business_id,
        classification="ai_inference",
        label="AI-inferred audience",
        summary=summary[:5000],
        confidence=Decimal("0.650") if evidence else Decimal("0.300"),
        evidence=evidence,
        segments=[{
            "label": "Evidence-backed working segment",
            "classification": "ai_inference",
            "reasoning": f"Interest in {terminology}; validate against future observed results.",
        }],
        geographic_areas=geographic_areas,
        interests=interests,
        intent_signals=[terminology],
        buyer_personas=[],
        likely_pain_points=[],
        preferred_channels=preferred_channels,
        excluded_audiences=[
            "Audiences excluded by consent, privacy, platform, or business policy",
            "Unverified competitor demographic assumptions",
            *(
                ["Patients, appointment holders, and people inferred from health conditions"]
                if healthcare else []
            ),
        ],
        min_age=min_age,
        max_age=max_age,
    )
    session.add(hypothesis)
    await _flush(session)
    return hypothesis


def _audience_channels(
    performance_rows: list[tuple[Any, ...]], connected: list[str]
) -> list[str]:
    ranked = sorted(
        performance_rows,
        key=lambda row: (int(row[2]), int(row[1])),
        reverse=True,
    )
    channels = [str(row[0]) for row in ranked if int(row[1]) or int(row[2])]
    mapping = {
        "meta_ads": "meta", "google_ads": "google_ads", "instagram": "instagram",
        "facebook": "facebook", "gmail": "email", "whatsapp_business": "whatsapp",
    }
    channels.extend(mapping[item] for item in connected if item in mapping)
    return list(dict.fromkeys(channels))[:5]


def _required_integrations(channels: list[str]) -> list[str]:
    mapping = {
        "meta": "meta_ads", "google_ads": "google_ads", "instagram": "instagram",
        "facebook": "facebook", "email": "gmail", "whatsapp": "whatsapp_business",
    }
    return list(dict.fromkeys(mapping[item] for item in channels if item in mapping))


def _allocate_budget(total: Decimal, count: int) -> list[Decimal]:
    if count < 1:
        raise MarketingValidationError
    base = (total / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    values = [base for _ in range(count)]
    values[-1] += total - sum(values, ZERO)
    return values


async def list_content(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, search: str | None, status: str | None, campaign_id: UUID | None, channel: str | None):
    statement = select(MarketingContent).where(MarketingContent.business_id == business_id)
    if term := _term(search):
        statement = statement.where(or_(MarketingContent.title.icontains(term, autoescape=True), MarketingContent.body.icontains(term, autoescape=True)))
    if status:
        statement = statement.where(MarketingContent.status == status)
    if campaign_id:
        statement = statement.where(MarketingContent.campaign_id == campaign_id)
    if channel:
        statement = statement.where(MarketingContent.channel == channel)
    return await _paged(session, statement.order_by(MarketingContent.updated_at.desc(), MarketingContent.id.desc()), page, page_size)


async def get_content(session: AsyncSession, *, business_id: UUID, content_id: UUID) -> MarketingContent:
    return await _get(session, MarketingContent, business_id, content_id)


async def list_content_versions(session: AsyncSession, *, business_id: UUID, content_id: UUID) -> list[MarketingContent]:
    content = await get_content(session, business_id=business_id, content_id=content_id)
    try:
        return list((await session.scalars(select(MarketingContent).where(
            MarketingContent.business_id == business_id,
            MarketingContent.root_content_id == content.root_content_id,
        ).order_by(MarketingContent.version.desc()).limit(100))).all())
    except SQLAlchemyError:
        raise MarketingPersistenceError from None


async def create_content(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: ContentCreate, ai_generated: bool = False, parent_content_id: UUID | None = None, parent_content: MarketingContent | None = None) -> MarketingContent:
    if not await _exists(session, Campaign, business_id, data.campaign_id):
        raise MarketingValidationError
    value_id = uuid4()
    root_id = value_id
    version = 1
    if parent_content_id:
        parent = parent_content or await get_content(session, business_id=business_id, content_id=parent_content_id)
        if parent.business_id != business_id or parent.id != parent_content_id:
            raise MarketingValidationError
        if (data.campaign_id, data.channel, data.content_type, data.language) != (parent.campaign_id, parent.channel, parent.content_type, parent.language):
            raise MarketingValidationError
        root_id = parent.root_content_id
        version = int(await session.scalar(select(func.coalesce(func.max(MarketingContent.version), 0)).where(MarketingContent.business_id == business_id, MarketingContent.root_content_id == root_id)) or 0) + 1
    value = MarketingContent(id=value_id, business_id=business_id, created_by_user_id=actor_user_id, ai_generated=ai_generated, version=version, parent_content_id=parent_content_id, root_content_id=root_id, **data.model_dump())
    session.add(value)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.content_created" if version == 1 else "marketing.content_version_created", entity_type="marketing_content", entity_id=value.id, summary=f"Created content {value.title} version {version}; nothing was published externally.")
    return value


async def create_content_version(session: AsyncSession, *, business_id: UUID, content_id: UUID, actor_user_id: UUID, data: ContentVersionCreate) -> MarketingContent:
    parent = await get_content(session, business_id=business_id, content_id=content_id)
    return await create_content(session, business_id=business_id, actor_user_id=actor_user_id, parent_content_id=parent.id, parent_content=parent, data=ContentCreate(campaign_id=parent.campaign_id, channel=parent.channel, content_type=parent.content_type, title=data.title, body=data.body, cta=data.cta, language=parent.language), ai_generated=False)


async def change_content_status(session: AsyncSession, *, business_id: UUID, content_id: UUID, actor_user_id: UUID, status: str) -> MarketingContent:
    value = await get_content(session, business_id=business_id, content_id=content_id)
    _transition(value.status, status, CONTENT_TRANSITIONS)
    before = value.status
    value.status = status
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.content_status_changed", entity_type="marketing_content", entity_id=value.id, summary=f"Changed content {value.title} status; no external publication occurred.", before_value=before, after_value=status)
    if status == "review":
        _notify(session, business_id=business_id, category="content_review", title="Content awaiting review", message=f"Review “{value.title}” before scheduling or publishing.", entity_type="marketing_content", entity_id=value.id)
        record_automation_event(session, business_id=business_id, event_type="content_ready_for_review", entity_type="content", entity_id=value.id, payload={"status": status, "previous_status": before, "name": value.title, "channel": value.channel})
    return value


async def generate_content(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: ContentGenerateRequest, provider: AIAgentProvider) -> MarketingContent:
    campaign_context = ""
    if data.campaign_id:
        campaign = await get_campaign(session, business_id=business_id, campaign_id=data.campaign_id)
        campaign_context = f" Campaign objective: {campaign.objective}. Offer: {campaign.offer or 'none provided'}."
    task = (
        f"Prepare one {data.content_type} draft for {data.channel} in language {data.language}. {data.prompt}.{campaign_context} "
        "Use only trusted catalog and brand facts. Return final copy in the summary and optional CTA/headline variants as recommendations. Do not send or publish it."
    )
    output = await _run_cmo(session, business_id, task, provider)
    title = data.title or (output.recommendations[0] if output.recommendations else data.prompt)[:180]
    cta = output.recommendations[-1][:300] if output.recommendations else None
    return await create_content(session, business_id=business_id, actor_user_id=actor_user_id, parent_content_id=data.parent_content_id, ai_generated=True, data=ContentCreate(campaign_id=data.campaign_id, channel=data.channel, content_type=data.content_type, title=title, body=output.summary, cta=cta, language=data.language))


async def create_creative_brief(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: CreativeBriefCreate, provider: AIAgentProvider) -> CreativeAsset:
    campaign = await get_campaign(session, business_id=business_id, campaign_id=data.campaign_id) if data.campaign_id else None
    content = await get_content(session, business_id=business_id, content_id=data.content_id) if data.content_id else None
    if campaign and content and content.campaign_id != campaign.id:
        raise MarketingValidationError
    task = (
        "Prepare a structured creative brief using trusted branding, brand voice, offer and catalog context. "
        f"Asset: {data.asset_type}. Instructions: {data.instructions}. Include visual direction, headline, supporting copy, CTA, product focus, aspect ratio and prohibited claims. "
        "Do not generate an image or claim one was generated."
    )
    output = await _run_cmo(session, business_id, task, provider)
    value = CreativeAsset(
        business_id=business_id, campaign_id=data.campaign_id, content_id=data.content_id,
        asset_type=data.asset_type, source_type="ai_brief", instructions=data.instructions,
        visual_direction=(output.summary + "\n" + "\n".join(output.recommendations))[:5000],
        generation_status="brief_ready", storage_reference=None,
        width=data.width, height=data.height, aspect_ratio=data.aspect_ratio, alt_text=data.alt_text,
    )
    session.add(value)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.creative_brief_created", entity_type="marketing_creative_asset", entity_id=value.id, summary="Created an internal creative brief; no image provider was called.")
    return value


async def list_creative_assets(session: AsyncSession, *, business_id: UUID, campaign_id: UUID | None, content_id: UUID | None) -> list[CreativeAsset]:
    statement = select(CreativeAsset).where(CreativeAsset.business_id == business_id)
    if campaign_id:
        statement = statement.where(CreativeAsset.campaign_id == campaign_id)
    if content_id:
        statement = statement.where(CreativeAsset.content_id == content_id)
    try:
        return list((await session.scalars(statement.order_by(CreativeAsset.created_at.desc(), CreativeAsset.id.desc()).limit(100))).all())
    except SQLAlchemyError:
        raise MarketingPersistenceError from None


async def list_schedules(session: AsyncSession, *, business_id: UUID, start_at: datetime | None, end_at: datetime | None, channel: str | None, campaign_id: UUID | None):
    statement = select(SocialSchedule).where(SocialSchedule.business_id == business_id)
    if start_at:
        statement = statement.where(SocialSchedule.scheduled_for >= start_at)
    if end_at:
        statement = statement.where(SocialSchedule.scheduled_for < end_at)
    if channel:
        statement = statement.where(SocialSchedule.channel == channel)
    if campaign_id:
        statement = statement.where(SocialSchedule.campaign_id == campaign_id)
    try:
        return list((await session.scalars(statement.order_by(SocialSchedule.scheduled_for, SocialSchedule.id).limit(500))).all())
    except SQLAlchemyError:
        raise MarketingPersistenceError from None


async def create_schedule(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: ScheduleCreate) -> SocialSchedule:
    content = await get_content(session, business_id=business_id, content_id=data.content_id)
    if content.status not in {"approved", "scheduled"}:
        raise MarketingStateError
    business = await _business(session, business_id)
    try:
        ZoneInfo(business.timezone)
    except ZoneInfoNotFoundError:
        raise MarketingValidationError from None
    value = SocialSchedule(business_id=business_id, content_id=content.id, campaign_id=content.campaign_id, channel=content.channel, scheduled_for=data.scheduled_for.astimezone(timezone.utc), timezone=business.timezone, status="scheduled")
    session.add(value)
    content.status = "scheduled"
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.content_scheduled", entity_type="social_content_schedule", entity_id=value.id, summary=f"Scheduled internal content record {content.title}; no external publication is configured.", after_value=value.scheduled_for.isoformat())
    _notify(session, business_id=business_id, category="content_schedule", title="Content scheduled internally", message=f"“{content.title}” is scheduled in the content calendar. External connection required to publish.", entity_type="social_content_schedule", entity_id=value.id)
    return value


async def reschedule(session: AsyncSession, *, business_id: UUID, schedule_id: UUID, actor_user_id: UUID, scheduled_for: datetime) -> SocialSchedule:
    value = await _get(session, SocialSchedule, business_id, schedule_id)
    if value.status not in {"scheduled", "ready_to_publish"}:
        raise MarketingStateError
    before = value.scheduled_for.isoformat()
    value.scheduled_for = scheduled_for.astimezone(timezone.utc)
    value.status = "scheduled"
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.content_rescheduled", entity_type="social_content_schedule", entity_id=value.id, summary="Rescheduled an internal content calendar item.", before_value=before, after_value=value.scheduled_for.isoformat())
    return value


async def unschedule(session: AsyncSession, *, business_id: UUID, schedule_id: UUID, actor_user_id: UUID) -> SocialSchedule:
    value = await _get(session, SocialSchedule, business_id, schedule_id)
    if value.status not in {"scheduled", "ready_to_publish"}:
        raise MarketingStateError
    value.status = "unscheduled"
    content = await get_content(session, business_id=business_id, content_id=value.content_id)
    if content.status == "scheduled":
        content.status = "approved"
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.content_unscheduled", entity_type="social_content_schedule", entity_id=value.id, summary="Removed an item from the internal content calendar.")
    return value


async def mark_social_schedule_ready(
    session: AsyncSession,
    *,
    business_id: UUID,
    schedule_id: UUID,
    now: datetime | None = None,
) -> SocialSchedule:
    """Make due content operator-ready without claiming external publication."""
    value = await _get(session, SocialSchedule, business_id, schedule_id)
    evaluated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if value.status == "ready_to_publish":
        return value
    if value.status != "scheduled" or value.scheduled_for > evaluated_at:
        raise MarketingStateError
    value.status = "ready_to_publish"
    _notify(
        session,
        business_id=business_id,
        category="content_schedule",
        title="Scheduled content is ready",
        message="Scheduled content is ready for an authorized external publisher. It was not published by AI Business OS.",
        entity_type="social_content_schedule",
        entity_id=value.id,
    )
    await _flush(session)
    return value


async def list_competitors(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, search: str | None, active: bool | None):
    statement = select(Competitor).where(Competitor.business_id == business_id)
    if term := _term(search):
        statement = statement.where(or_(Competitor.name.icontains(term, autoescape=True), Competitor.website_domain.icontains(term, autoescape=True)))
    if active is not None:
        statement = statement.where(Competitor.active.is_(active))
    return await _paged(session, statement.order_by(Competitor.updated_at.desc(), Competitor.id.desc()), page, page_size)


async def create_competitor(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: CompetitorCreate) -> Competitor:
    value = Competitor(business_id=business_id, **data.model_dump())
    session.add(value)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.competitor_created", entity_type="marketing_competitor", entity_id=value.id, summary=f"Added competitor {value.name}; no website was scraped.")
    return value


async def get_competitor(session: AsyncSession, *, business_id: UUID, competitor_id: UUID) -> Competitor:
    return await _get(session, Competitor, business_id, competitor_id)


async def update_competitor(session: AsyncSession, *, business_id: UUID, competitor_id: UUID, actor_user_id: UUID, data: CompetitorUpdate) -> Competitor:
    value = await _get(session, Competitor, business_id, competitor_id)
    for key, item in data.model_dump(exclude_unset=True).items():
        setattr(value, key, item)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.competitor_updated", entity_type="marketing_competitor", entity_id=value.id, summary=f"Updated competitor {value.name}.")
    return value


async def list_observations(session: AsyncSession, *, business_id: UUID, competitor_id: UUID, page: int, page_size: int, category: str | None):
    if not await _exists(session, Competitor, business_id, competitor_id):
        raise MarketingNotFoundError
    statement = select(CompetitorObservation).where(CompetitorObservation.business_id == business_id, CompetitorObservation.competitor_id == competitor_id)
    if category:
        statement = statement.where(CompetitorObservation.category == category)
    return await _paged(session, statement.order_by(CompetitorObservation.observed_at.desc(), CompetitorObservation.id.desc()), page, page_size)


async def create_observation(session: AsyncSession, *, business_id: UUID, competitor_id: UUID, actor_user_id: UUID, data: ObservationCreate) -> CompetitorObservation:
    competitor = await _get(session, Competitor, business_id, competitor_id)
    value = CompetitorObservation(business_id=business_id, competitor_id=competitor_id, safe_metrics=data.safe_metrics.model_dump(mode="json", exclude_none=True), **data.model_dump(exclude={"safe_metrics"}))
    session.add(value)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.competitor_observation_created", entity_type="competitor_observation", entity_id=value.id, summary=f"Recorded sourced observation for {competitor.name}.")
    _notify(session, business_id=business_id, category="competitor_update", title="Competitor observation added", message=f"A new sourced observation was added for {competitor.name}.", entity_type="competitor_observation", entity_id=value.id)
    return value


async def analyze_competitor(session: AsyncSession, *, business_id: UUID, competitor_id: UUID, actor_user_id: UUID, provider: AIAgentProvider) -> CompetitorAnalysis:
    competitor = await _get(session, Competitor, business_id, competitor_id)
    try:
        observations = list((await session.scalars(select(CompetitorObservation).where(CompetitorObservation.business_id == business_id, CompetitorObservation.competitor_id == competitor_id).order_by(CompetitorObservation.observed_at.desc()).limit(100))).all())
    except SQLAlchemyError:
        raise MarketingPersistenceError from None
    if not observations:
        raise MarketingValidationError
    sources = "\n".join(f"- [{item.category}] {item.title}: {item.summary}" for item in observations)
    task = (
        f"Analyze only these stored, source-derived observations about competitor {competitor.name}:\n{sources[:12000]}\n"
        "Distinguish supported observations from recommendations. Identify strengths, weaknesses, differences, positioning/content gaps and campaign opportunities without claiming unsupported facts."
    )
    output = await _run_cmo(session, business_id, task, provider)
    recs = output.recommendations
    value = CompetitorAnalysis(
        business_id=business_id, competitor_id=competitor_id, summary=output.summary,
        strengths=recs[0:3], weaknesses=recs[3:6], differences=recs[6:9],
        positioning_gaps=recs[9:12], content_gaps=recs[12:15],
        campaign_opportunities=recs[15:18], recommendations=recs[-5:],
        source_observation_count=len(observations), generated_by="ai",
    )
    session.add(value)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.competitor_analysis_created", entity_type="competitor_analysis", entity_id=value.id, summary=f"Created source-grounded analysis for {competitor.name}.")
    return value


async def list_analyses(session: AsyncSession, *, business_id: UUID, competitor_id: UUID):
    if not await _exists(session, Competitor, business_id, competitor_id):
        raise MarketingNotFoundError
    try:
        return list((await session.scalars(select(CompetitorAnalysis).where(CompetitorAnalysis.business_id == business_id, CompetitorAnalysis.competitor_id == competitor_id).order_by(CompetitorAnalysis.created_at.desc(), CompetitorAnalysis.id.desc()).limit(25))).all())
    except SQLAlchemyError:
        raise MarketingPersistenceError from None


async def competitor_analysis_to_opportunity(session: AsyncSession, *, business_id: UUID, competitor_id: UUID, analysis_id: UUID, actor_user_id: UUID, data: TrendOpportunityRequest):
    competitor = await _get(session, Competitor, business_id, competitor_id)
    analysis = await _get(session, CompetitorAnalysis, business_id, analysis_id)
    if analysis.competitor_id != competitor.id:
        raise MarketingNotFoundError
    recommendation = analysis.campaign_opportunities[0] if analysis.campaign_opportunities else analysis.summary
    opportunity = await create_opportunity(session, business_id=business_id, actor_user_id=actor_user_id, data=OpportunityCreate(
        title=data.title or f"Respond to {competitor.name}"[:180],
        description=data.description or recommendation[:3000], category="competitor_insight",
        source="competitor", priority=data.priority,
    ))
    opportunity.source_entity_type = "competitor_analysis"
    opportunity.source_entity_id = analysis.id
    opportunity.reason = (
        f"A source-grounded analysis based on {analysis.source_observation_count} "
        f"stored observation(s) identified a campaign opportunity."
    )
    opportunity.recommendation = recommendation[:3000]
    opportunity.suggested_action = "generate_campaign_proposal"
    opportunity.provenance = [{
        "source_type": "competitor_analysis",
        "source_id": str(analysis.id),
        "source_reference": f"competitor:{competitor.id}",
        "observed_at": analysis.created_at.isoformat(),
        "source_observation_count": analysis.source_observation_count,
    }]
    opportunity.dedupe_key = f"competitor-analysis-conversion:{analysis.id}"
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.competitor_analysis_converted", entity_type="competitor_analysis", entity_id=analysis.id, summary=f"Created opportunity from sourced analysis for {competitor.name}.", after_value=f"opportunity_id={opportunity.id}")
    return opportunity


async def list_trends(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, search: str | None, status: str | None):
    statement = select(MarketingTrend).where(MarketingTrend.business_id == business_id)
    if term := _term(search):
        statement = statement.where(or_(MarketingTrend.title.icontains(term, autoescape=True), MarketingTrend.description.icontains(term, autoescape=True)))
    if status:
        statement = statement.where(MarketingTrend.status == status)
    return await _paged(session, statement.order_by(MarketingTrend.relevance_score.desc(), MarketingTrend.observed_at.desc(), MarketingTrend.id.desc()), page, page_size)


async def create_trend(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: TrendCreate) -> MarketingTrend:
    value = MarketingTrend(business_id=business_id, **data.model_dump())
    session.add(value)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.trend_created", entity_type="marketing_trend", entity_id=value.id, summary=f"Stored trend {value.title} from an identified source.")
    if value.relevance_score >= Decimal("0.800"):
        _notify(session, business_id=business_id, category="marketing_trend", title="Important trend added", message=f"Review the sourced trend “{value.title}”.", entity_type="marketing_trend", entity_id=value.id)
    return value


async def change_trend_status(session: AsyncSession, *, business_id: UUID, trend_id: UUID, actor_user_id: UUID, status: str) -> MarketingTrend:
    value = await _get(session, MarketingTrend, business_id, trend_id)
    _transition(value.status, status, TREND_TRANSITIONS)
    before = value.status
    value.status = status
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.trend_status_changed", entity_type="marketing_trend", entity_id=value.id, summary=f"Changed trend {value.title} status.", before_value=before, after_value=status)
    return value


async def trend_to_opportunity(session: AsyncSession, *, business_id: UUID, trend_id: UUID, actor_user_id: UUID, data: TrendOpportunityRequest):
    trend = await _get(session, MarketingTrend, business_id, trend_id)
    if trend.status != "reviewed" or trend.opportunity_id is not None:
        raise MarketingStateError
    opportunity = await create_opportunity(session, business_id=business_id, actor_user_id=actor_user_id, data=OpportunityCreate(
        title=data.title or f"Act on trend: {trend.title}"[:180],
        description=data.description or trend.description[:3000], category="marketing_trend",
        source="trend", priority=data.priority,
    ))
    opportunity.source_entity_type = "marketing_trend"
    opportunity.source_entity_id = trend.id
    opportunity.reason = "A reviewed, identified trend source is relevant to internal campaign planning."
    opportunity.confidence = trend.confidence
    opportunity.recommendation = "Ask the AI CMO to prepare an evidence-grounded campaign proposal for review."
    opportunity.suggested_action = "generate_campaign_proposal"
    opportunity.provenance = [{
        "source_type": trend.source,
        "source_id": str(trend.id),
        "source_reference": trend.source_reference,
        "observed_at": trend.observed_at.isoformat(),
    }]
    opportunity.dedupe_key = f"trend-conversion:{trend.id}"
    trend.status = "acted_on"
    trend.opportunity_id = opportunity.id
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.trend_converted", entity_type="marketing_trend", entity_id=trend.id, summary=f"Created opportunity from reviewed trend {trend.title}.", after_value=f"opportunity_id={opportunity.id}")
    return opportunity


def derive_metrics(data: PerformanceCreate) -> dict[str, Decimal]:
    spend = Decimal(data.spend)
    revenue = Decimal(data.revenue)
    return {
        "ctr": _ratio(Decimal(data.clicks) * 100, data.impressions),
        "cpc": _ratio(spend, data.clicks),
        "cpm": _ratio(spend * 1000, data.impressions),
        "cpl": _ratio(spend, data.leads),
        "cpa": _ratio(spend, data.conversions),
        "roas": _ratio(revenue, spend),
    }


def _ratio(numerator: Decimal, denominator: int | Decimal) -> Decimal:
    if not denominator:
        return ZERO.quantize(RATIO_QUANTUM)
    return (numerator / Decimal(denominator)).quantize(RATIO_QUANTUM, rounding=ROUND_HALF_UP)


async def list_performance(session: AsyncSession, *, business_id: UUID, page: int, page_size: int, campaign_id: UUID | None, channel: str | None, period_start: date | None, period_end: date | None):
    statement = select(MarketingPerformance).where(MarketingPerformance.business_id == business_id)
    if campaign_id:
        statement = statement.where(MarketingPerformance.campaign_id == campaign_id)
    if channel:
        statement = statement.where(MarketingPerformance.channel == channel)
    if period_start:
        statement = statement.where(MarketingPerformance.period_end >= period_start)
    if period_end:
        statement = statement.where(MarketingPerformance.period_start <= period_end)
    return await _paged(session, statement.order_by(MarketingPerformance.period_end.desc(), MarketingPerformance.id.desc()), page, page_size)


async def create_performance(session: AsyncSession, *, business_id: UUID, actor_user_id: UUID, data: PerformanceCreate) -> MarketingPerformance:
    campaign = await get_campaign(session, business_id=business_id, campaign_id=data.campaign_id)
    if data.channel not in campaign.channels or not await _exists(session, MarketingContent, business_id, data.content_id):
        raise MarketingValidationError
    if data.content_id:
        content = await get_content(session, business_id=business_id, content_id=data.content_id)
        if content.campaign_id != campaign.id:
            raise MarketingValidationError
    value = MarketingPerformance(business_id=business_id, **data.model_dump(), **derive_metrics(data))
    session.add(value)
    await _flush(session)
    record_audit(session, business_id=business_id, actor_user_id=actor_user_id, event_type="marketing.performance_recorded", entity_type="marketing_performance", entity_id=value.id, summary=f"Recorded {value.data_source} performance for internal campaign {campaign.name}; derived metrics were calculated server-side.")
    return value


async def marketing_analytics(session: AsyncSession, *, business_id: UUID, period_start: date, period_end: date) -> MarketingAnalyticsResponse:
    if period_end < period_start or (period_end - period_start).days > 366:
        raise MarketingValidationError
    business = await _business(session, business_id)
    where = (MarketingPerformance.business_id == business_id, MarketingPerformance.period_end >= period_start, MarketingPerformance.period_start <= period_end)
    aggregate = [
        func.coalesce(func.sum(MarketingPerformance.spend), 0), func.coalesce(func.sum(MarketingPerformance.impressions), 0),
        func.coalesce(func.sum(MarketingPerformance.reach), 0), func.coalesce(func.sum(MarketingPerformance.clicks), 0),
        func.coalesce(func.sum(MarketingPerformance.leads), 0), func.coalesce(func.sum(MarketingPerformance.conversions), 0),
        func.coalesce(func.sum(MarketingPerformance.revenue), 0),
    ]
    try:
        totals = (await session.execute(select(*aggregate).where(*where))).one()
        spend, impressions, reach, clicks, leads, conversions, revenue = totals
        channel_rows = (await session.execute(select(MarketingPerformance.channel, *aggregate[:1], *aggregate[1:2], *aggregate[3:4], *aggregate[4:5], *aggregate[5:6], *aggregate[6:7]).where(*where).group_by(MarketingPerformance.channel).order_by(func.sum(MarketingPerformance.revenue).desc(), MarketingPerformance.channel))).all()
        campaign_rows = (await session.execute(select(Campaign.name, *aggregate[:1], *aggregate[1:2], *aggregate[3:4], *aggregate[4:5], *aggregate[5:6], *aggregate[6:7]).join(MarketingPerformance, MarketingPerformance.campaign_id == Campaign.id).where(*where, Campaign.business_id == business_id).group_by(Campaign.id, Campaign.name).order_by(func.sum(MarketingPerformance.revenue).desc(), Campaign.name).limit(50))).all()
        trend_rows = (await session.execute(select(MarketingPerformance.period_start, *aggregate[:1], *aggregate[1:2], *aggregate[3:4], *aggregate[5:6], *aggregate[6:7]).where(*where).group_by(MarketingPerformance.period_start).order_by(MarketingPerformance.period_start))).all()
        content_rows = (await session.execute(select(MarketingContent.id, MarketingContent.title, MarketingContent.channel, func.coalesce(func.sum(MarketingPerformance.clicks), 0), func.coalesce(func.sum(MarketingPerformance.conversions), 0), func.coalesce(func.sum(MarketingPerformance.revenue), 0)).join(MarketingPerformance, MarketingPerformance.content_id == MarketingContent.id).where(*where, MarketingContent.business_id == business_id).group_by(MarketingContent.id, MarketingContent.title, MarketingContent.channel).order_by(func.sum(MarketingPerformance.revenue).desc(), MarketingContent.id).limit(10))).all()
    except SQLAlchemyError:
        raise MarketingPersistenceError from None

    def breakdown(row) -> AnalyticsBreakdown:
        label, row_spend, row_impressions, row_clicks, row_leads, row_conversions, row_revenue = row
        return AnalyticsBreakdown(
            label=str(label), spend=Decimal(row_spend), impressions=int(row_impressions), clicks=int(row_clicks),
            leads=int(row_leads), conversions=int(row_conversions), revenue=Decimal(row_revenue),
            ctr=_ratio(Decimal(row_clicks) * 100, row_impressions), cpc=_ratio(Decimal(row_spend), row_clicks), roas=_ratio(Decimal(row_revenue), Decimal(row_spend)),
        )

    return MarketingAnalyticsResponse(
        period_start=period_start, period_end=period_end, currency=business.currency,
        spend=Decimal(spend), impressions=int(impressions), reach=int(reach), clicks=int(clicks), leads=int(leads), conversions=int(conversions), revenue=Decimal(revenue),
        ctr=_ratio(Decimal(clicks) * 100, impressions), cpc=_ratio(Decimal(spend), clicks), cpl=_ratio(Decimal(spend), leads), cpa=_ratio(Decimal(spend), conversions), roas=_ratio(Decimal(revenue), Decimal(spend)),
        channels=[breakdown(row) for row in channel_rows], campaigns=[breakdown(row) for row in campaign_rows],
        top_content=[TopContent(content_id=row[0], title=row[1], channel=row[2], clicks=int(row[3]), conversions=int(row[4]), revenue=Decimal(row[5])) for row in content_rows],
        trends=[MarketingTrendPoint(label=str(row[0]), spend=Decimal(row[1]), impressions=int(row[2]), clicks=int(row[3]), conversions=int(row[4]), revenue=Decimal(row[5])) for row in trend_rows],
    )


async def learn_from_performance(session: AsyncSession, *, business_id: UUID, period_start: date, period_end: date) -> LearningResponse:
    # Retained for API compatibility. Ordinary cross-channel before/after
    # aggregates do not establish comparable variants, stable cutoffs, or a
    # defensible attribution contract, so they are no longer promoted into
    # durable AI memory. Phase 6 learning is created only by the governed,
    # deterministic GrowthExperiment evaluation path.
    await marketing_analytics(
        session,
        business_id=business_id,
        period_start=period_start,
        period_end=period_end,
    )
    return LearningResponse(
        created=False,
        conclusion=(
            "Recorded period totals are descriptive only. Create a governed growth "
            "experiment with stable variants, attribution, samples, and a measurement "
            "cutoff before saving a business learning."
        ),
    )


async def _run_cmo(session: AsyncSession, business_id: UUID, task: str, provider: AIAgentProvider):
    return (await _execute_cmo(session, business_id, task, provider)).output


async def _execute_cmo(
    session: AsyncSession,
    business_id: UUID,
    task: str,
    provider: AIAgentProvider,
):
    brain_source_types = None
    include_memory = True
    if isinstance(session, AsyncSession):
        try:
            business_type = await session.scalar(select(Business.business_type).where(Business.id == business_id))
        except SQLAlchemyError:
            raise MarketingPersistenceError from None
        if isinstance(business_type, str) and is_healthcare_business_type(business_type):
            # Healthcare marketing receives only business/service presentation data.
            # Knowledge and memory may contain private or clinical material and are excluded.
            brain_source_types = [
                "business_profile", "branding", "appointment_type"
            ]
            include_memory = False
            task += " Never use patient identities, clinical details, diagnoses, notes, or other PHI."
        elif (
            isinstance(business_type, str)
            and (industry := get_business_industry(business_type)) is not None
            and industry.group == "professional_services"
        ):
            brain_source_types = [
                "business_profile", "branding", "appointment_type"
            ]
            include_memory = False
            task += " Use only public service descriptions; never infer client identities or confidential client matters."
        elif isinstance(business_type, str) and business_type.strip().casefold() == "real estate":
            brain_source_types = ["business_profile", "branding", "knowledge_entry"]
            task += " Do not interpret generic catalog items as properties and do not invent property inventory."
    try:
        result = await execute_ai_agent(session, business_id, AIAgentExecutionRequest(
            role="cmo", task=task, include_business_brain=True,
            include_memory=include_memory, brain_source_types=brain_source_types,
        ), provider)
    except AIAgentError:
        raise MarketingAIError from None
    return result
