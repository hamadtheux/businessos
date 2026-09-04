from __future__ import annotations

from hashlib import sha256
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.action_adapters import connector_action_adapters
from app.domain.audience_safety import contains_sensitive_targeting
from app.integrations.registry import require_connector
from app.exceptions.integration import IntegrationValidationError
from app.models.ai_action import AIAction
from app.models.approval_request import ApprovalRequest
from app.models.automation_intelligence import (
    AudienceHypothesis,
    MarketingActionProposal,
)
from app.models.integration import IntegrationConnection
from app.models.catalog_item import CatalogItem
from app.models.commerce import (
    CatalogMedia,
    CommerceFeedDestination,
    CommerceFeedProductStatus,
    ProductGroupDestination,
)
from app.models.automation_intelligence import AdvertisingSpendPolicy
from app.models.marketing import (
    Campaign,
    CampaignProductSelection,
    CreativeAsset,
    MarketingContent,
)
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


async def preflight_campaign(
    session: AsyncSession,
    *,
    business_id: UUID,
    campaign_id: UUID,
    channel: CampaignActionChannel | None,
) -> dict[str, object]:
    campaign = await get_campaign(session, business_id=business_id, campaign_id=campaign_id)
    target = _campaign_channel(campaign, channel)
    provider = "google" if target == "google_ads" else "meta"
    connector_type = "google_ads" if provider == "google" else "meta_ads"
    issues: list[dict[str, object]] = []
    if contains_sensitive_targeting(campaign.audience_definition):
        issues.append(_preflight_issue(
            "sensitive_targeting_prohibited",
            "The campaign audience contains prohibited sensitive targeting.",
        ))
    connection = await session.scalar(select(IntegrationConnection).where(
        IntegrationConnection.business_id == business_id,
        IntegrationConnection.connector_type == connector_type,
    ))
    if connection is None or connection.status != "connected":
        issues.append(_preflight_issue("provider_connected", f"Connect {connector_type} before campaign execution."))
        selected: dict[str, str] = {}
    elif connection.authentication_state != "authorized" or not connection.credential_reference:
        issues.append(_preflight_issue("reauthorization_required", f"Reauthorize {connector_type}."))
        selected = {}
    else:
        selected = {
            str(item.get("resource_type")): str(item.get("external_reference"))
            for item in connection.selected_resources
            if item.get("resource_type") and item.get("external_reference")
        }
    required = (
        ("google_ads_customer", "google_merchant_account", "google_merchant_data_source", "google_merchant_ads_link", "google_conversion_action")
        if provider == "google" else
        ("ad_account", "meta_business", "meta_catalog", "facebook_page", "conversion_dataset")
    )
    for resource in required:
        if resource not in selected:
            issues.append(_preflight_issue("asset_selection_required", f"Select the required {resource.replace('_', ' ')} resource."))
    if provider == "google" and selected.get("google_merchant_account") and selected.get("google_ads_customer"):
        expected_link = f"{selected['google_ads_customer']}:{selected['google_merchant_account']}"
        if selected.get("google_merchant_ads_link") != expected_link:
            issues.append(_preflight_issue("merchant_link_required", "The selected Merchant Center and Google Ads accounts are not verified as linked."))

    selections = list((await session.scalars(select(CampaignProductSelection).where(
        CampaignProductSelection.business_id == business_id,
        CampaignProductSelection.campaign_id == campaign.id,
    ))).all())
    product_ids = [item.catalog_item_id for item in selections]
    products = list((await session.scalars(select(CatalogItem).where(
        CatalogItem.business_id == business_id,
        CatalogItem.id.in_(product_ids) if product_ids else False,
    ))).all())
    if not products:
        issues.append(_preflight_issue("product_selection_required", "Select at least one authoritative catalog product."))
    for product in products:
        if product.status != "active" or not product.published or product.availability not in {"in_stock", "preorder", "backorder"}:
            issues.append(_preflight_issue("product_ineligible", f"{product.name} is not currently available for promotion."))
        if not product.product_url or not product.product_url.startswith("https://"):
            issues.append(_preflight_issue("landing_page_required", f"{product.name} needs a secure authoritative landing URL."))
    media_product_ids = set((await session.scalars(select(CatalogMedia.catalog_item_id).where(
        CatalogMedia.business_id == business_id,
        CatalogMedia.catalog_item_id.in_(product_ids) if product_ids else False,
        CatalogMedia.media_type == "image", CatalogMedia.active.is_(True),
        CatalogMedia.authoritative.is_(True),
    ))).all())
    for product in products:
        if product.id not in media_product_ids:
            issues.append(_preflight_issue("creative_asset_required", f"{product.name} needs an authoritative product image."))

    destination_provider = "google_merchant_center" if provider == "google" else "meta_product_catalog"
    destination = await session.scalar(select(CommerceFeedDestination).where(
        CommerceFeedDestination.business_id == business_id,
        CommerceFeedDestination.integration_connection_id == (connection.id if connection else None),
        CommerceFeedDestination.provider == destination_provider,
        CommerceFeedDestination.external_account_id == selected.get("google_merchant_account", selected.get("meta_business")),
        CommerceFeedDestination.external_resource_id == selected.get("google_merchant_data_source", selected.get("meta_catalog")),
    )) if connection else None
    eligible = 0
    if destination is None:
        issues.append(_preflight_issue("catalog_required", "Configure and synchronize the selected commerce destination."))
    elif product_ids:
        eligible = int(await session.scalar(select(func.count(CommerceFeedProductStatus.id)).where(
            CommerceFeedProductStatus.business_id == business_id,
            CommerceFeedProductStatus.destination_id == destination.id,
            CommerceFeedProductStatus.catalog_item_id.in_(product_ids),
            CommerceFeedProductStatus.status.in_(["eligible", "limited", "warning"]),
        )) or 0)
        if eligible != len(product_ids):
            issues.append(_preflight_issue("product_ineligible", f"{len(product_ids) - eligible} selected product(s) are not eligible in the provider catalog."))
    if provider == "meta" and destination is not None:
        product_set = await session.scalar(select(ProductGroupDestination).where(
            ProductGroupDestination.business_id == business_id,
            ProductGroupDestination.destination_id == destination.id,
            ProductGroupDestination.status == "ready",
        ))
        if product_set is None or not product_set.external_reference:
            issues.append(_preflight_issue("product_set_required", "Create and synchronize a Meta product set for this campaign selection."))

    if campaign.planned_budget <= 0:
        issues.append(_preflight_issue("budget_rejected", "Set a positive campaign budget."))
    if campaign.budget_mode != "daily":
        issues.append(_preflight_issue(
            "budget_rejected",
            "Retail provider campaigns require a daily budget; lifetime spend cannot be mapped safely.",
        ))
    policy = await session.scalar(select(AdvertisingSpendPolicy).where(
        AdvertisingSpendPolicy.business_id == business_id,
        AdvertisingSpendPolicy.active.is_(True),
    ))
    if policy is None:
        issues.append(_preflight_issue("spend_policy_required", "Configure an active advertising spend policy."))
    else:
        if policy.currency != campaign.currency:
            issues.append(_preflight_issue("currency_mismatch", "Campaign and spend-policy currencies do not match."))
        if campaign.planned_budget > policy.max_single_campaign_budget:
            issues.append(_preflight_issue("budget_rejected", "Campaign budget exceeds the server-owned per-campaign limit."))
        if policy.daily_advertising_limit is not None and (
            campaign.budget_mode != "daily" or campaign.planned_budget > policy.daily_advertising_limit
        ):
            issues.append(_preflight_issue("budget_rejected", "Campaign budget is incompatible with the daily spend limit."))
    if campaign.offer and not campaign.offer_authorized:
        issues.append(_preflight_issue("offer_approval_required", "The proposed offer has no authoritative or owner-approved source."))
    return {
        "ready": not any(bool(item["blocking"]) for item in issues),
        "provider": provider,
        "selected_products": len(product_ids),
        "eligible_products": eligible,
        "approval_required": True,
        "issues": issues,
    }


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
    product_rows = list(
        (
            await session.execute(
                select(CatalogItem.sku, CatalogItem.id)
                .join(
                    CampaignProductSelection,
                    (CampaignProductSelection.catalog_item_id == CatalogItem.id)
                    & (
                        CampaignProductSelection.business_id
                        == CatalogItem.business_id
                    ),
                )
                .where(
                    CampaignProductSelection.business_id == business_id,
                    CampaignProductSelection.campaign_id == campaign.id,
                )
            )
        ).all()
    )
    commerce_campaign = campaign.campaign_type in {
        "retail_performance_max",
        "catalog_sales",
    } or bool(product_rows)
    if not commerce_campaign:
        # Preserve the existing manual campaign path. It still creates only a
        # governed, approval-required proposal; provider execution remains
        # independently gated by the action boundary and connector state.
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

    preflight = await preflight_campaign(
        session, business_id=business_id, campaign_id=campaign_id, channel=channel,
    )
    if not preflight["ready"]:
        codes = ", ".join(str(item["code"]) for item in preflight["issues"])
        raise MarketingValidationError(f"Campaign preflight failed: {codes}")
    connection = await session.scalar(select(IntegrationConnection).where(
        IntegrationConnection.business_id == business_id,
        IntegrationConnection.connector_type == connector_type,
        IntegrationConnection.status == "connected",
    ))
    resources = {
        str(item.get("resource_type")): str(item.get("external_reference"))
        for item in (connection.selected_resources if connection else [])
        if item.get("resource_type") and item.get("external_reference")
    }
    if target == "meta":
        destination = await session.scalar(select(CommerceFeedDestination).where(
            CommerceFeedDestination.business_id == business_id,
            CommerceFeedDestination.integration_connection_id == connection.id,
            CommerceFeedDestination.external_resource_id == resources.get("meta_catalog"),
        ))
        product_set = await session.scalar(select(ProductGroupDestination).where(
            ProductGroupDestination.business_id == business_id,
            ProductGroupDestination.destination_id == destination.id,
            ProductGroupDestination.status == "ready",
        ))
        payload = CreateMetaCampaignPayload(
            **common, catalog_ref=resources["meta_catalog"],
            product_set_ref=product_set.external_reference,
            page_ref=resources["facebook_page"],
            conversion_dataset_ref=resources["conversion_dataset"],
            primary_text=campaign.proposed_copy[:2000] if campaign.proposed_copy else None,
            headline=campaign.name,
            description=campaign.description[:500] if campaign.description else None,
            call_to_action="SHOP_NOW",
        )
    else:
        payload = CreateGoogleAdsCampaignPayload(
            network="performance_max", **common,
            merchant_account_ref=resources["google_merchant_account"],
            conversion_action_ref=resources["google_conversion_action"],
            product_offer_ids=[sku or str(item_id) for sku, item_id in product_rows],
            business_name=None,
            headlines=[campaign.name[:30]],
            descriptions=[(campaign.proposed_copy or campaign.description or campaign.objective)[:90]],
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
    media_refs = (
        await _ready_instagram_media_refs(
            session,
            business_id=business_id,
            content_id=content.id,
        )
        if target == "instagram"
        else []
    )
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
                media_refs=media_refs,
            ),
        ),
    )


async def _ready_instagram_media_refs(
    session: AsyncSession,
    *,
    business_id: UUID,
    content_id: UUID,
) -> list[str]:
    """Return only tenant-owned, final, HTTPS media supported by the adapter."""
    try:
        candidates = list(
            (
                await session.scalars(
                    select(CreativeAsset)
                    .where(
                        CreativeAsset.business_id == business_id,
                        CreativeAsset.content_id == content_id,
                        CreativeAsset.source_type == "future_provider",
                        CreativeAsset.generation_status == "ready",
                        CreativeAsset.storage_reference.is_not(None),
                    )
                    .order_by(
                        CreativeAsset.created_at.desc(),
                        CreativeAsset.id.desc(),
                    )
                    .limit(10)
                )
            ).all()
        )
    except SQLAlchemyError:
        raise MarketingPersistenceError from None
    for candidate in candidates:
        reference = candidate.storage_reference
        if isinstance(reference, str) and _safe_public_media_reference(reference):
            return [reference]
    return []


def _safe_public_media_reference(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and len(value) <= 1024
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


def _preflight_issue(code: str, message: str, *, blocking: bool = True) -> dict[str, object]:
    return {"code": code, "message": message, "blocking": blocking}
