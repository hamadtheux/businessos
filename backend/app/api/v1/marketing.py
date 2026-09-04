from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Awaitable, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.ai_agent import AIAgentProviderDependency
from app.api.dependencies.creative import CreativeGenerationProviderDependency
from app.storage.factory import ObjectStorageDependency
from app.api.dependencies.business import BusinessAccessDependency, require_business_role
from app.api.response_materialization import materialize_response_before_commit
from app.db.session import get_db_session
from app.exceptions.marketing import MarketingAIError, MarketingNotFoundError, MarketingPersistenceError, MarketingStateError, MarketingValidationError
from app.exceptions.automation_intelligence import (
    AutomationIntelligenceNotFoundError,
    AutomationIntelligencePersistenceError,
    AutomationIntelligenceValidationError,
)
from app.exceptions.ai_action import AIActionError
from app.exceptions.ai_agent_execution import AIAgentExecutionLedgerError
from app.exceptions.approval import ApprovalError
from app.exceptions.action_execution_attempt import (
    ActionExecutionAttemptPersistenceError,
    ActionExecutionAttemptValidationError,
)
from app.integrations.automation_registry import default_competitor_research_provider
from app.schemas.marketing import (
    AudienceCreate,
    AudienceResponse,
    CampaignCreate,
    CampaignDetail,
    CampaignGenerateRequest,
    CampaignPreflightResponse,
    CampaignResponse,
    CampaignStatus,
    CampaignUpdate,
    Channel,
    ChannelPlanCreate,
    ChannelPlanResponse,
    CompetitorAnalysisResponse,
    CompetitorCandidateResponse,
    CompetitorCandidateStatusUpdate,
    CompetitorCreate,
    CompetitorDiscoveryRunResponse,
    CompetitorDiscoveryStatusResponse,
    CompetitorEvidenceResponse,
    CompetitorResponse,
    CompetitorUpdate,
    ContentCreate,
    ContentGenerateRequest,
    ContentResponse,
    ContentStatus,
    ContentVersionCreate,
    CreativeAssetResponse,
    CreativeBriefCreate,
    LearningResponse,
    MarketingAutomationRunResponse,
    MarketingAnalyticsResponse,
    MarketingPlanCreate,
    MarketingPlanResponse,
    MarketingPlanStatus,
    MarketingPlanUpdate,
    ObservationCreate,
    ObservationResponse,
    PerformanceCreate,
    PerformanceResponse,
    PlanGenerateRequest,
    ScheduleCreate,
    ScheduleResponse,
    ScheduleUpdate,
    StatusUpdate,
    TrendCreate,
    TrendOpportunityRequest,
    TrendResponse,
    TrendStatus,
    AudienceHypothesisResponse,
    MarketingActionProposalResponse,
    PrepareCampaignActionRequest,
    PrepareContentActionRequest,
    AdvertisingSpendPolicyResponse,
    AdvertisingSpendPolicyUpdate,
)
from app.schemas.operations import OpportunityResponse, PageResponse
from app.services import marketing as service
from app.services.billing import require_capacity, require_feature
from app.services.automation_intelligence import (
    candidate_evidence,
    change_candidate_status,
    latest_discovery_run,
    manual_refresh_available_at,
    latest_marketing_run,
    list_candidates,
    schedule_competitor_discovery,
    schedule_marketing_automation,
)
from app.services.marketing_actions import (
    preflight_campaign,
    prepare_campaign_action,
    prepare_content_publish_action,
)
from app.services.advertising_spend_policy import (
    get_advertising_spend_policy,
    set_advertising_spend_policy,
)
from app.services.operations import record_audit


router = APIRouter(prefix="/businesses/{business_id}/marketing", tags=["Marketing OS"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Search = Annotated[str | None, Query(max_length=100)]
AwareDateTimeQuery = Annotated[datetime | None, Query()]


def _page(items, total: int, page: int, page_size: int):
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def _guard(session: AsyncSession, business_id: UUID, feature: str, *, ai: bool = False) -> None:
    if not isinstance(session, AsyncSession):
        return
    await require_feature(session, business_id=business_id, key=feature)
    if ai:
        await require_capacity(session, business_id=business_id, key="max_ai_executions_month")
        await require_capacity(session, business_id=business_id, key="max_ai_input_tokens_month")
        await require_capacity(session, business_id=business_id, key="max_ai_output_tokens_month")


@router.get("/audiences", response_model=PageResponse[AudienceResponse])
async def read_audiences(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, search: Search = None):
    items, total = await _read(response, service.list_audiences(session, business_id=access.business.id, page=page, page_size=page_size, search=search))
    return _page(items, total, page, page_size)


@router.post("/audiences", response_model=AudienceResponse, status_code=status.HTTP_201_CREATED)
async def create_audience(data: AudienceCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.create_audience(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


@router.get("/plans", response_model=PageResponse[MarketingPlanResponse])
async def read_plans(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, search: Search = None, plan_status: Annotated[MarketingPlanStatus | None, Query(alias="status")] = None):
    items, total = await _read(response, service.list_plans(session, business_id=access.business.id, page=page, page_size=page_size, search=search, status=plan_status))
    return _page(items, total, page, page_size)


@router.post("/plans", response_model=MarketingPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(data: MarketingPlanCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    await _guard(session, access.business.id, "marketing_cmo")
    return await _mutate(response, session, service.create_plan(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


@router.post("/plans/generate", response_model=MarketingPlanResponse, status_code=status.HTTP_201_CREATED)
async def generate_plan(data: PlanGenerateRequest, access: BusinessAccessDependency, response: Response, session: SessionDependency, provider: AIAgentProviderDependency):
    await _guard(session, access.business.id, "marketing_cmo", ai=True)
    return await _mutate(response, session, service.generate_plan(session, business_id=access.business.id, actor_user_id=access.user.id, data=data, provider=provider))


@router.get("/plans/{plan_id}", response_model=MarketingPlanResponse)
async def read_plan(plan_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _read(response, service.get_plan(session, business_id=access.business.id, plan_id=plan_id))


@router.patch("/plans/{plan_id}", response_model=MarketingPlanResponse)
async def patch_plan(plan_id: UUID, data: MarketingPlanUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.update_plan(session, business_id=access.business.id, plan_id=plan_id, actor_user_id=access.user.id, data=data))


@router.post("/plans/{plan_id}/status", response_model=MarketingPlanResponse)
async def change_plan_status(plan_id: UUID, data: StatusUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.change_plan_status(session, business_id=access.business.id, plan_id=plan_id, actor_user_id=access.user.id, status=data.status))


@router.get("/campaigns", response_model=PageResponse[CampaignResponse])
async def read_campaigns(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, search: Search = None, campaign_status: Annotated[CampaignStatus | None, Query(alias="status")] = None):
    items, total = await _read(response, service.list_campaigns(session, business_id=access.business.id, page=page, page_size=page_size, search=search, status=campaign_status))
    return _page(items, total, page, page_size)


@router.post("/campaigns", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign(data: CampaignCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    await _guard(session, access.business.id, "campaigns")
    return await _mutate(response, session, service.create_campaign(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


@router.post("/campaigns/generate", response_model=CampaignDetail, status_code=status.HTTP_201_CREATED)
async def generate_campaign(data: CampaignGenerateRequest, access: BusinessAccessDependency, response: Response, session: SessionDependency, provider: AIAgentProviderDependency):
    await _guard(session, access.business.id, "campaigns", ai=True)
    campaign = await _mutate(None, session, service.generate_campaign(session, business_id=access.business.id, actor_user_id=access.user.id, data=data, provider=provider))
    return await _read(response, service.campaign_detail(session, business_id=access.business.id, campaign=campaign))


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetail)
async def read_campaign(campaign_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    campaign = await _read(None, service.get_campaign(session, business_id=access.business.id, campaign_id=campaign_id))
    return await _read(response, service.campaign_detail(session, business_id=access.business.id, campaign=campaign))


@router.get("/campaigns/{campaign_id}/preflight", response_model=CampaignPreflightResponse)
async def read_campaign_preflight(
    campaign_id: UUID, access: BusinessAccessDependency,
    response: Response, session: SessionDependency,
    channel: Literal["meta", "google_ads"] | None = None,
):
    return await _read(response, preflight_campaign(
        session, business_id=access.business.id, campaign_id=campaign_id, channel=channel,
    ))


@router.get(
    "/campaigns/{campaign_id}/audience-intelligence",
    response_model=AudienceHypothesisResponse,
)
async def read_campaign_audience_intelligence(
    campaign_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    return await _read(response, service.get_campaign_audience(
        session, business_id=access.business.id, campaign_id=campaign_id
    ))


@router.post(
    "/campaigns/{campaign_id}/prepare-action",
    response_model=MarketingActionProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_campaign_governed_action(
    campaign_id: UUID,
    data: PrepareCampaignActionRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    await _guard(session, access.business.id, "campaigns")
    return await _mutate(response, session, prepare_campaign_action(
        session,
        business_id=access.business.id,
        campaign_id=campaign_id,
        requested_by_user_id=access.user.id,
        channel=data.channel,
    ))


@router.patch("/campaigns/{campaign_id}", response_model=CampaignResponse)
async def patch_campaign(campaign_id: UUID, data: CampaignUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.update_campaign(session, business_id=access.business.id, campaign_id=campaign_id, actor_user_id=access.user.id, data=data))


@router.post("/campaigns/{campaign_id}/duplicate", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def duplicate_campaign(campaign_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.duplicate_campaign(session, business_id=access.business.id, campaign_id=campaign_id, actor_user_id=access.user.id))


@router.post("/campaigns/{campaign_id}/status", response_model=CampaignResponse)
async def change_campaign_status(campaign_id: UUID, data: StatusUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.change_campaign_status(session, business_id=access.business.id, campaign_id=campaign_id, actor_user_id=access.user.id, status=data.status))


@router.post("/campaigns/{campaign_id}/channels", response_model=ChannelPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_channel_plan(campaign_id: UUID, data: ChannelPlanCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.create_channel_plan(session, business_id=access.business.id, campaign_id=campaign_id, actor_user_id=access.user.id, data=data))


@router.get("/content", response_model=PageResponse[ContentResponse])
async def read_content(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, search: Search = None, content_status: Annotated[ContentStatus | None, Query(alias="status")] = None, campaign_id: UUID | None = None, channel: Channel | None = None):
    items, total = await _read(response, service.list_content(session, business_id=access.business.id, page=page, page_size=page_size, search=search, status=content_status, campaign_id=campaign_id, channel=channel))
    return _page(items, total, page, page_size)


@router.post("/content", response_model=ContentResponse, status_code=status.HTTP_201_CREATED)
async def create_content(data: ContentCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.create_content(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


@router.post("/content/generate", response_model=ContentResponse, status_code=status.HTTP_201_CREATED)
async def generate_content(data: ContentGenerateRequest, access: BusinessAccessDependency, response: Response, session: SessionDependency, provider: AIAgentProviderDependency):
    await _guard(session, access.business.id, "marketing_cmo", ai=True)
    return await _mutate(response, session, service.generate_content(session, business_id=access.business.id, actor_user_id=access.user.id, data=data, provider=provider))


@router.get("/content/{content_id}", response_model=ContentResponse)
async def read_content_item(content_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _read(response, service.get_content(session, business_id=access.business.id, content_id=content_id))


@router.post(
    "/content/{content_id}/prepare-publish",
    response_model=MarketingActionProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_content_governed_action(
    content_id: UUID,
    data: PrepareContentActionRequest,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    await _guard(session, access.business.id, "marketing_cmo")
    return await _mutate(response, session, prepare_content_publish_action(
        session,
        business_id=access.business.id,
        content_id=content_id,
        requested_by_user_id=access.user.id,
        channel=data.channel,
    ))


@router.get("/content/{content_id}/versions", response_model=list[ContentResponse])
async def read_content_versions(content_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _read(response, service.list_content_versions(session, business_id=access.business.id, content_id=content_id))


@router.post("/content/{content_id}/versions", response_model=ContentResponse, status_code=status.HTTP_201_CREATED)
async def create_content_version(content_id: UUID, data: ContentVersionCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.create_content_version(session, business_id=access.business.id, content_id=content_id, actor_user_id=access.user.id, data=data))


@router.post("/content/{content_id}/status", response_model=ContentResponse)
async def change_content_status(content_id: UUID, data: StatusUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.change_content_status(session, business_id=access.business.id, content_id=content_id, actor_user_id=access.user.id, status=data.status))


@router.get("/creative-assets", response_model=list[CreativeAssetResponse])
async def read_creative_assets(access: BusinessAccessDependency, response: Response, session: SessionDependency, campaign_id: UUID | None = None, content_id: UUID | None = None):
    return await _read(response, service.list_creative_assets(session, business_id=access.business.id, campaign_id=campaign_id, content_id=content_id))


@router.post("/creative-assets/brief", response_model=CreativeAssetResponse, status_code=status.HTTP_201_CREATED)
async def create_creative_brief(data: CreativeBriefCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency, provider: AIAgentProviderDependency):
    await _guard(session, access.business.id, "marketing_cmo", ai=True)
    return await _mutate(response, session, service.create_creative_brief(session, business_id=access.business.id, actor_user_id=access.user.id, data=data, provider=provider))


@router.post(
    "/creative-assets/{creative_asset_id}/generate",
    response_model=CreativeAssetResponse,
)
async def generate_creative_asset(
    creative_asset_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    provider: CreativeGenerationProviderDependency,
    storage: ObjectStorageDependency,
):
    await _guard(
        session,
        access.business.id,
        "marketing_cmo",
        ai=True,
    )
    return await _mutate(
        response,
        session,
        service.generate_creative_asset(
            session,
            business_id=access.business.id,
            creative_asset_id=creative_asset_id,
            actor_user_id=access.user.id,
            provider=provider,
            storage=storage,
        ),
    )


@router.post(
    "/creative-assets/{creative_asset_id}/regenerate",
    response_model=CreativeAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def regenerate_creative_asset(
    creative_asset_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    provider: CreativeGenerationProviderDependency,
    storage: ObjectStorageDependency,
):
    await _guard(
        session,
        access.business.id,
        "marketing_cmo",
        ai=True,
    )
    return await _mutate(
        response,
        session,
        service.regenerate_creative_asset(
            session,
            business_id=access.business.id,
            creative_asset_id=creative_asset_id,
            actor_user_id=access.user.id,
            provider=provider,
            storage=storage,
        ),
    )


@router.get("/calendar", response_model=list[ScheduleResponse])
async def read_calendar(access: BusinessAccessDependency, response: Response, session: SessionDependency, start_at: AwareDateTimeQuery = None, end_at: AwareDateTimeQuery = None, channel: Channel | None = None, campaign_id: UUID | None = None):
    return await _read(response, service.list_schedules(session, business_id=access.business.id, start_at=start_at, end_at=end_at, channel=channel, campaign_id=campaign_id))


@router.post("/calendar", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule(data: ScheduleCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.create_schedule(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


@router.patch("/calendar/{schedule_id}", response_model=ScheduleResponse)
async def reschedule(schedule_id: UUID, data: ScheduleUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.reschedule(session, business_id=access.business.id, schedule_id=schedule_id, actor_user_id=access.user.id, scheduled_for=data.scheduled_for))


@router.post("/calendar/{schedule_id}/unschedule", response_model=ScheduleResponse)
async def unschedule(schedule_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.unschedule(session, business_id=access.business.id, schedule_id=schedule_id, actor_user_id=access.user.id))


@router.get(
    "/automation/{run_type}",
    response_model=MarketingAutomationRunResponse | None,
)
async def read_marketing_automation_status(
    run_type: str,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    return await _read(response, latest_marketing_run(
        session, business_id=access.business.id, run_type=run_type
    ))


@router.post(
    "/automation/{run_type}/refresh",
    response_model=MarketingAutomationRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_marketing_automation(
    run_type: str,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    feature = "marketing_cmo" if run_type == "content_plan" else "campaigns"
    await _guard(session, access.business.id, feature)
    async def schedule():
        run, _created = await schedule_marketing_automation(
            session, business_id=access.business.id, run_type=run_type
        )
        return run
    return await _mutate(response, session, schedule())


@router.get("/competitors", response_model=PageResponse[CompetitorResponse])
async def read_competitors(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, search: Search = None, active: bool | None = None):
    items, total = await _read(response, service.list_competitors(session, business_id=access.business.id, page=page, page_size=page_size, search=search, active=active))
    return _page(items, total, page, page_size)


@router.get(
    "/competitor-discovery",
    response_model=CompetitorDiscoveryStatusResponse,
)
async def read_competitor_discovery_status(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    run = await _read(
        response, latest_discovery_run(session, business_id=access.business.id)
    )
    suggested = await _read(
        response, list_candidates(
            session, business_id=access.business.id, status="suggested", limit=100
        )
    )
    monitored = await _read(
        response, list_candidates(
            session, business_id=access.business.id, status="monitoring", limit=100
        )
    )
    refresh_available_at = await _read(
        response,
        manual_refresh_available_at(session, business_id=access.business.id),
    )
    if refresh_available_at is not None and refresh_available_at <= datetime.now(UTC):
        refresh_available_at = None
    return CompetitorDiscoveryStatusResponse(
        latest_run=run,
        provider_available=default_competitor_research_provider() is not None,
        suggested_count=len(suggested),
        monitored_count=len(monitored),
        manual_refresh_available_at=refresh_available_at,
    )


@router.post(
    "/competitor-discovery/refresh",
    response_model=CompetitorDiscoveryRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_competitor_discovery(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    await _guard(session, access.business.id, "competitor_intelligence")
    async def schedule():
        run, _created = await schedule_competitor_discovery(
            session,
            business_id=access.business.id,
            trigger_type="manual_refresh",
        )
        return run
    return await _mutate(response, session, schedule())


@router.get(
    "/competitor-candidates",
    response_model=list[CompetitorCandidateResponse],
)
async def read_competitor_candidates(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
    candidate_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return await _read(response, list_candidates(
        session,
        business_id=access.business.id,
        status=candidate_status,
        limit=limit,
    ))


@router.get(
    "/competitor-candidates/{candidate_id}/evidence",
    response_model=list[CompetitorEvidenceResponse],
)
async def read_competitor_candidate_evidence(
    candidate_id: UUID,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    return await _read(response, candidate_evidence(
        session, business_id=access.business.id, candidate_id=candidate_id
    ))


@router.post(
    "/competitor-candidates/{candidate_id}/status",
    response_model=CompetitorCandidateResponse,
)
async def update_competitor_candidate_status(
    candidate_id: UUID,
    data: CompetitorCandidateStatusUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    await _guard(session, access.business.id, "competitor_intelligence")
    return await _mutate(response, session, change_candidate_status(
        session,
        business_id=access.business.id,
        candidate_id=candidate_id,
        actor_user_id=access.user.id,
        status=data.status,
    ))


@router.post("/competitors", response_model=CompetitorResponse, status_code=status.HTTP_201_CREATED)
async def create_competitor(data: CompetitorCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    await _guard(session, access.business.id, "competitor_intelligence")
    return await _mutate(response, session, service.create_competitor(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


@router.get("/competitors/{competitor_id}", response_model=CompetitorResponse)
async def read_competitor(competitor_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _read(response, service.get_competitor(session, business_id=access.business.id, competitor_id=competitor_id))


@router.patch("/competitors/{competitor_id}", response_model=CompetitorResponse)
async def patch_competitor(competitor_id: UUID, data: CompetitorUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.update_competitor(session, business_id=access.business.id, competitor_id=competitor_id, actor_user_id=access.user.id, data=data))


@router.get("/competitors/{competitor_id}/observations", response_model=PageResponse[ObservationResponse])
async def read_observations(competitor_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, category: str | None = None):
    items, total = await _read(response, service.list_observations(session, business_id=access.business.id, competitor_id=competitor_id, page=page, page_size=page_size, category=category))
    return _page(items, total, page, page_size)


@router.post("/competitors/{competitor_id}/observations", response_model=ObservationResponse, status_code=status.HTTP_201_CREATED)
async def create_observation(competitor_id: UUID, data: ObservationCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.create_observation(session, business_id=access.business.id, competitor_id=competitor_id, actor_user_id=access.user.id, data=data))


@router.get("/competitors/{competitor_id}/analyses", response_model=list[CompetitorAnalysisResponse])
async def read_analyses(competitor_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _read(response, service.list_analyses(session, business_id=access.business.id, competitor_id=competitor_id))


@router.post("/competitors/{competitor_id}/analyze", response_model=CompetitorAnalysisResponse, status_code=status.HTTP_201_CREATED)
async def analyze_competitor(competitor_id: UUID, access: BusinessAccessDependency, response: Response, session: SessionDependency, provider: AIAgentProviderDependency):
    await _guard(session, access.business.id, "competitor_intelligence", ai=True)
    return await _mutate(response, session, service.analyze_competitor(session, business_id=access.business.id, competitor_id=competitor_id, actor_user_id=access.user.id, provider=provider))


@router.post("/competitors/{competitor_id}/analyses/{analysis_id}/opportunity", response_model=OpportunityResponse, status_code=status.HTTP_201_CREATED)
async def competitor_analysis_to_opportunity(competitor_id: UUID, analysis_id: UUID, data: TrendOpportunityRequest, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.competitor_analysis_to_opportunity(session, business_id=access.business.id, competitor_id=competitor_id, analysis_id=analysis_id, actor_user_id=access.user.id, data=data))


@router.get("/trends", response_model=PageResponse[TrendResponse])
async def read_trends(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, search: Search = None, trend_status: Annotated[TrendStatus | None, Query(alias="status")] = None):
    items, total = await _read(response, service.list_trends(session, business_id=access.business.id, page=page, page_size=page_size, search=search, status=trend_status))
    return _page(items, total, page, page_size)


@router.post("/trends", response_model=TrendResponse, status_code=status.HTTP_201_CREATED)
async def create_trend(data: TrendCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    await _guard(session, access.business.id, "trend_intelligence")
    return await _mutate(response, session, service.create_trend(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


@router.post("/trends/{trend_id}/status", response_model=TrendResponse)
async def change_trend_status(trend_id: UUID, data: StatusUpdate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.change_trend_status(session, business_id=access.business.id, trend_id=trend_id, actor_user_id=access.user.id, status=data.status))


@router.post("/trends/{trend_id}/opportunity", response_model=OpportunityResponse, status_code=status.HTTP_201_CREATED)
async def trend_to_opportunity(trend_id: UUID, data: TrendOpportunityRequest, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.trend_to_opportunity(session, business_id=access.business.id, trend_id=trend_id, actor_user_id=access.user.id, data=data))


@router.get(
    "/spend-policy",
    response_model=AdvertisingSpendPolicyResponse | None,
)
async def read_advertising_spend_policy(
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    require_business_role(access)
    try:
        value = await get_advertising_spend_policy(
            session, business_id=access.business.id
        )
    except ActionExecutionAttemptPersistenceError:
        raise HTTPException(
            503,
            {"code": "temporary_failure", "message": "Spend policy could not be loaded."},
            headers=_PRIVATE_HEADERS,
        ) from None
    _private(response)
    return value


@router.put(
    "/spend-policy",
    response_model=AdvertisingSpendPolicyResponse,
)
async def replace_advertising_spend_policy(
    data: AdvertisingSpendPolicyUpdate,
    access: BusinessAccessDependency,
    response: Response,
    session: SessionDependency,
):
    require_business_role(access)
    try:
        before = await get_advertising_spend_policy(
            session, business_id=access.business.id
        )
        before_summary = _spend_policy_audit_summary(before)
        value = await set_advertising_spend_policy(
            session,
            business_id=access.business.id,
            trusted_business_currency=access.business.currency,
            actor_user_id=access.user.id,
            data=data,
        )
        record_audit(
            session,
            business_id=access.business.id,
            actor_user_id=access.user.id,
            event_type="advertising.spend_policy_changed",
            entity_type="advertising_spend_policy",
            entity_id=value.id,
            summary="Updated server-owned advertising spend limits.",
            before_value=before_summary,
            after_value=_spend_policy_audit_summary(value),
        )
        await materialize_response_before_commit(session, value)
        await session.commit()
    except ActionExecutionAttemptValidationError as exc:
        await _rollback(session)
        code = (
            "spend_limit_increase_confirmation_required"
            if "confirmation" in str(exc).casefold()
            else "validation_error"
        )
        raise HTTPException(
            409 if code.endswith("confirmation_required") else 422,
            {"code": code, "message": str(exc)},
            headers=_PRIVATE_HEADERS,
        ) from None
    except (ActionExecutionAttemptPersistenceError, SQLAlchemyError):
        await _rollback(session)
        raise HTTPException(
            503,
            {"code": "temporary_failure", "message": "Spend policy could not be saved."},
            headers=_PRIVATE_HEADERS,
        ) from None
    _private(response)
    return value


@router.get("/performance", response_model=PageResponse[PerformanceResponse])
async def read_performance(access: BusinessAccessDependency, response: Response, session: SessionDependency, page: Page = 1, page_size: PageSize = 25, campaign_id: UUID | None = None, channel: Channel | None = None, period_start: date | None = None, period_end: date | None = None):
    items, total = await _read(response, service.list_performance(session, business_id=access.business.id, page=page, page_size=page_size, campaign_id=campaign_id, channel=channel, period_start=period_start, period_end=period_end))
    return _page(items, total, page, page_size)


@router.post("/performance", response_model=PerformanceResponse, status_code=status.HTTP_201_CREATED)
async def create_performance(data: PerformanceCreate, access: BusinessAccessDependency, response: Response, session: SessionDependency):
    return await _mutate(response, session, service.create_performance(session, business_id=access.business.id, actor_user_id=access.user.id, data=data))


@router.get("/analytics", response_model=MarketingAnalyticsResponse)
async def read_marketing_analytics(access: BusinessAccessDependency, response: Response, session: SessionDependency, period_start: date, period_end: date):
    return await _read(response, service.marketing_analytics(session, business_id=access.business.id, period_start=period_start, period_end=period_end))


@router.post("/performance/learn", response_model=LearningResponse)
async def learn_from_performance(access: BusinessAccessDependency, response: Response, session: SessionDependency, period_start: date, period_end: date):
    return await _mutate(response, session, service.learn_from_performance(session, business_id=access.business.id, period_start=period_start, period_end=period_end))


async def _read(response: Response | None, operation: Awaitable):
    try:
        value = await operation
    except MarketingNotFoundError:
        raise _not_found() from None
    except AutomationIntelligenceNotFoundError:
        raise _not_found() from None
    except MarketingValidationError:
        raise _invalid() from None
    except AutomationIntelligenceValidationError:
        raise _invalid() from None
    except MarketingStateError:
        raise _conflict() from None
    except MarketingAIError:
        raise _ai_unavailable() from None
    except (MarketingPersistenceError, AutomationIntelligencePersistenceError, AIActionError, AIAgentExecutionLedgerError, ApprovalError):
        raise _unavailable() from None
    if response is not None:
        _private(response)
    return value


async def _mutate(response: Response | None, session: AsyncSession, operation: Awaitable):
    try:
        value = await operation
        await materialize_response_before_commit(session, value)
        await session.commit()
    except MarketingNotFoundError:
        await _abort_mutation(session)
        raise _not_found() from None
    except AutomationIntelligenceNotFoundError:
        await _abort_mutation(session)
        raise _not_found() from None
    except MarketingValidationError:
        await _abort_mutation(session)
        raise _invalid() from None
    except AutomationIntelligenceValidationError:
        await _abort_mutation(session)
        raise _invalid() from None
    except MarketingStateError:
        await _abort_mutation(session)
        raise _conflict() from None
    except MarketingAIError:
        await _abort_mutation(session)
        raise _ai_unavailable() from None
    except (MarketingPersistenceError, AutomationIntelligencePersistenceError, AIActionError, AIAgentExecutionLedgerError, ApprovalError, SQLAlchemyError):
        await _abort_mutation(session)
        raise _unavailable() from None
    except Exception:
        await _abort_mutation(session)
        raise
    service.clear_pending_creative_storage_compensations(session)
    if response is not None:
        _private(response)
    return value


async def _abort_mutation(session: AsyncSession) -> None:
    await service.compensate_pending_creative_storage(session)
    await _rollback(session)


async def _rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except SQLAlchemyError:
        pass


def _private(response: Response) -> None:
    for key, value in _PRIVATE_HEADERS.items():
        response.headers[key] = value


def _not_found() -> HTTPException:
    return HTTPException(404, "Marketing resource not found.", headers=_PRIVATE_HEADERS)


def _invalid() -> HTTPException:
    return HTTPException(422, {
        "code": "validation_error",
        "message": "Check the campaign or content details and try again.",
    }, headers=_PRIVATE_HEADERS)


def _conflict() -> HTTPException:
    return HTTPException(409, "Request conflicts with the current marketing state.", headers=_PRIVATE_HEADERS)


def _unavailable() -> HTTPException:
    return HTTPException(503, {
        "code": "temporarily_unavailable",
        "message": "Marketing services are temporarily unavailable. Please try again.",
    }, headers=_PRIVATE_HEADERS)


def _ai_unavailable() -> HTTPException:
    return HTTPException(503, {
        "code": "provider_unavailable",
        "message": "The AI provider could not complete generation. No campaign or content was saved; please try again.",
    }, headers=_PRIVATE_HEADERS)


_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _spend_policy_audit_summary(value: object) -> str:
    if value is None:
        return "not_configured"
    return (
        f"currency={value.currency};active={value.active};"
        f"campaign={value.max_single_campaign_budget};"
        f"change={value.max_single_budget_change};"
        f"daily={value.daily_advertising_limit};"
        f"monthly={value.monthly_ai_managed_limit}"
    )
