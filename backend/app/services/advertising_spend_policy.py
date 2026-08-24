from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.action_execution_attempt import (
    ActionExecutionAttemptPersistenceError,
    ActionExecutionAttemptValidationError,
)
from app.models.automation_intelligence import AdvertisingSpendPolicy
from app.models.marketing import Campaign
from app.schemas.marketing import AdvertisingSpendPolicyUpdate
from app.schemas.ai_action_payload import (
    ActionPayload,
    ChangeAdBudgetPayload,
    CreateGoogleAdsCampaignPayload,
    CreateMetaCampaignPayload,
    LaunchGoogleAdsCampaignPayload,
    LaunchMetaCampaignPayload,
)
from app.services.action_registry import ActionDefinition


_PERSISTENCE_MESSAGE = "Unable to validate advertising spend policy"


async def get_advertising_spend_policy(
    session: AsyncSession, *, business_id: UUID, lock: bool = False
) -> AdvertisingSpendPolicy | None:
    statement = select(AdvertisingSpendPolicy).where(
        AdvertisingSpendPolicy.business_id == business_id
    )
    if lock:
        statement = statement.with_for_update()
    try:
        value = await session.scalar(statement)
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    if value is not None and value.business_id != business_id:
        raise ActionExecutionAttemptValidationError(
            "Advertising spend policy is not tenant-owned"
        )
    return value


async def set_advertising_spend_policy(
    session: AsyncSession,
    *,
    business_id: UUID,
    trusted_business_currency: str,
    actor_user_id: UUID,
    data: AdvertisingSpendPolicyUpdate,
) -> AdvertisingSpendPolicy:
    """Create or replace limits only after an authorized user confirms increases."""
    if data.currency != trusted_business_currency:
        raise ActionExecutionAttemptValidationError(
            "Advertising spend policy must use the business currency"
        )
    policy = await get_advertising_spend_policy(
        session, business_id=business_id, lock=True
    )
    current_values = (
        policy.max_single_campaign_budget,
        policy.max_single_budget_change,
        policy.daily_advertising_limit,
        policy.monthly_ai_managed_limit,
    ) if policy is not None else (Decimal("0"), Decimal("0"), None, None)
    proposed_values = (
        data.max_single_campaign_budget,
        data.max_single_budget_change,
        data.daily_advertising_limit,
        data.monthly_ai_managed_limit,
    )
    material_increase = any(
        proposed is not None and (current is None or proposed > current)
        for current, proposed in zip(current_values, proposed_values, strict=True)
    )
    if material_increase and not data.confirm_material_increase:
        raise ActionExecutionAttemptValidationError(
            "Advertising spend limit increase requires explicit confirmation"
        )
    if policy is None:
        policy = AdvertisingSpendPolicy(
            business_id=business_id,
            currency=data.currency,
            max_single_campaign_budget=data.max_single_campaign_budget,
            max_single_budget_change=data.max_single_budget_change,
            daily_advertising_limit=data.daily_advertising_limit,
            monthly_ai_managed_limit=data.monthly_ai_managed_limit,
            active=data.active,
            set_by_user_id=actor_user_id,
        )
        session.add(policy)
    else:
        policy.currency = data.currency
        policy.max_single_campaign_budget = data.max_single_campaign_budget
        policy.max_single_budget_change = data.max_single_budget_change
        policy.daily_advertising_limit = data.daily_advertising_limit
        policy.monthly_ai_managed_limit = data.monthly_ai_managed_limit
        policy.active = data.active
        policy.set_by_user_id = actor_user_id
    try:
        await session.flush()
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    return policy


async def require_advertising_spend_authorized(
    session: AsyncSession,
    *,
    business_id: UUID,
    definition: ActionDefinition,
    payload: ActionPayload,
) -> None:
    """Fail closed on every spend-related action using only server-owned limits."""
    if not definition.spend_related:
        return
    try:
        policy = await session.scalar(
            select(AdvertisingSpendPolicy)
            .where(
                AdvertisingSpendPolicy.business_id == business_id,
                AdvertisingSpendPolicy.active.is_(True),
            )
            .with_for_update()
        )
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    if policy is None or policy.business_id != business_id:
        raise ActionExecutionAttemptValidationError(
            "Advertising spend policy is required"
        )

    budget, currency, period, change = await _authorized_budget(
        session,
        business_id=business_id,
        payload=payload,
    )
    if policy.currency != currency:
        raise ActionExecutionAttemptValidationError(
            "Advertising spend policy currency does not match"
        )
    if budget > policy.max_single_campaign_budget:
        raise ActionExecutionAttemptValidationError(
            "Advertising campaign budget exceeds the server-owned limit"
        )
    if change is not None and change > policy.max_single_budget_change:
        raise ActionExecutionAttemptValidationError(
            "Advertising budget change exceeds the server-owned limit"
        )
    if policy.daily_advertising_limit is not None:
        if period != "daily":
            raise ActionExecutionAttemptValidationError(
                "A lifetime budget cannot be authorized under a daily-only spend limit"
            )
        if budget > policy.daily_advertising_limit:
            raise ActionExecutionAttemptValidationError(
                "Advertising daily budget exceeds the server-owned limit"
            )
    if policy.monthly_ai_managed_limit is not None:
        monthly_exposure = budget * Decimal("31") if period == "daily" else budget
        if monthly_exposure > policy.monthly_ai_managed_limit:
            raise ActionExecutionAttemptValidationError(
                "Advertising monthly exposure exceeds the server-owned limit"
            )


async def _authorized_budget(
    session: AsyncSession,
    *,
    business_id: UUID,
    payload: ActionPayload,
) -> tuple[Decimal, str, str, Decimal | None]:
    if isinstance(payload, (CreateMetaCampaignPayload, CreateGoogleAdsCampaignPayload)):
        return payload.budget, payload.currency, payload.budget_period, None
    if isinstance(payload, ChangeAdBudgetPayload):
        campaign = await _internal_campaign(
            session, business_id=business_id, reference=payload.campaign_ref
        )
        if campaign.currency != payload.currency:
            raise ActionExecutionAttemptValidationError(
                "Advertising campaign currency does not match"
            )
        return (
            payload.budget,
            payload.currency,
            payload.budget_period,
            abs(payload.budget - campaign.planned_budget),
        )
    if isinstance(payload, (LaunchMetaCampaignPayload, LaunchGoogleAdsCampaignPayload)):
        campaign = await _internal_campaign(
            session, business_id=business_id, reference=payload.campaign_ref
        )
        return (
            campaign.planned_budget,
            campaign.currency,
            campaign.budget_mode,
            None,
        )
    raise ActionExecutionAttemptValidationError(
        "Spend-related action has no supported server-owned budget source"
    )


async def _internal_campaign(
    session: AsyncSession, *, business_id: UUID, reference: str
) -> Campaign:
    prefix = "marketing-campaign:"
    if not reference.startswith(prefix):
        raise ActionExecutionAttemptValidationError(
            "Advertising spend requires a tenant-owned campaign reference"
        )
    try:
        campaign_id = UUID(reference.removeprefix(prefix))
    except ValueError:
        raise ActionExecutionAttemptValidationError(
            "Advertising campaign reference is invalid"
        ) from None
    try:
        campaign = await session.scalar(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.business_id == business_id,
            )
        )
    except SQLAlchemyError:
        raise ActionExecutionAttemptPersistenceError(_PERSISTENCE_MESSAGE) from None
    if campaign is None or campaign.business_id != business_id:
        raise ActionExecutionAttemptValidationError(
            "Advertising campaign is not tenant-owned"
        )
    return campaign
