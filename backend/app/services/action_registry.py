from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from pydantic import ValidationError

from app.exceptions.ai_action import (
    AIActionValidationError,
    UnsupportedAIActionError,
)
from app.schemas.ai_action_payload import (
    ActionPayload,
    ActionPayloadType,
    ChangeAdBudgetPayload,
    CreateGoogleAdsCampaignPayload,
    CreateMetaCampaignPayload,
    CreateOrderPayload,
    LaunchGoogleAdsCampaignPayload,
    LaunchMetaCampaignPayload,
    PauseAdCampaignPayload,
    PublishSocialPostPayload,
    SendCustomerMessagePayload,
    SendEmailPayload,
    SendWhatsAppMessagePayload,
    UpdateCRMPayload,
)


ActionCategory = Literal[
    "communications",
    "social",
    "advertising",
    "crm",
    "orders",
]
DefaultRiskLevel = Literal["low", "medium", "high", "critical"]


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    action_type: str
    category: ActionCategory
    default_risk_level: DefaultRiskLevel
    capability: str
    expects_external_side_effect: bool
    always_requires_approval: bool
    spend_related: bool
    destructive: bool
    external_communication: bool
    external_publication: bool
    campaign_launch: bool
    payload_model: type[ActionPayload]


class ActionRegistry:
    """Immutable lookup of server-supported action definitions."""

    __slots__ = ("_definitions",)

    def __init__(self, definitions: tuple[ActionDefinition, ...]) -> None:
        values: dict[str, ActionDefinition] = {}
        for definition in definitions:
            if not definition.action_type or definition.action_type in values:
                raise ValueError("Action definitions must have unique identifiers")
            values[definition.action_type] = definition
        self._definitions: Mapping[str, ActionDefinition] = MappingProxyType(values)

    @property
    def definitions(self) -> tuple[ActionDefinition, ...]:
        return tuple(self._definitions.values())

    @property
    def action_types(self) -> frozenset[str]:
        return frozenset(self._definitions)

    def get(self, action_type: str) -> ActionDefinition | None:
        return self._definitions.get(action_type)

    def require(self, action_type: str) -> ActionDefinition:
        definition = self.get(action_type)
        if definition is None:
            raise UnsupportedAIActionError("Unsupported AI action")
        return definition

    def validate_payload(
        self,
        action_type: str,
        payload: object,
    ) -> ActionPayloadType:
        definition = self.require(action_type)
        try:
            return definition.payload_model.model_validate(payload)
        except ValidationError:
            raise AIActionValidationError("Invalid AI action payload") from None
        except Exception:
            raise AIActionValidationError("Invalid AI action payload") from None

    def with_definition(self, definition: ActionDefinition) -> "ActionRegistry":
        if definition.action_type in self._definitions:
            raise ValueError("Action definition is already registered")
        return ActionRegistry((*self.definitions, definition))


def _definition(
    action_type: str,
    *,
    category: ActionCategory,
    risk: DefaultRiskLevel,
    capability: str,
    payload_model: type[ActionPayload],
    external_side_effect: bool = True,
    approval: bool = False,
    spend: bool = False,
    destructive: bool = False,
    communication: bool = False,
    publication: bool = False,
    campaign_launch: bool = False,
) -> ActionDefinition:
    return ActionDefinition(
        action_type=action_type,
        category=category,
        default_risk_level=risk,
        capability=capability,
        expects_external_side_effect=external_side_effect,
        always_requires_approval=approval,
        spend_related=spend,
        destructive=destructive,
        external_communication=communication,
        external_publication=publication,
        campaign_launch=campaign_launch,
        payload_model=payload_model,
    )


ACTION_REGISTRY = ActionRegistry(
    (
        _definition(
            "send_email",
            category="communications",
            risk="medium",
            capability="communications.email.send",
            payload_model=SendEmailPayload,
            approval=True,
            communication=True,
        ),
        _definition(
            "send_whatsapp_message",
            category="communications",
            risk="medium",
            capability="communications.whatsapp.send",
            payload_model=SendWhatsAppMessagePayload,
            approval=True,
            communication=True,
        ),
        _definition(
            "send_customer_message",
            category="communications",
            risk="medium",
            capability="communications.customer.send",
            payload_model=SendCustomerMessagePayload,
            approval=True,
            communication=True,
        ),
        _definition(
            "publish_social_post",
            category="social",
            risk="high",
            capability="social.post.publish",
            payload_model=PublishSocialPostPayload,
            approval=True,
            publication=True,
        ),
        _definition(
            "create_meta_campaign",
            category="advertising",
            risk="high",
            capability="advertising.meta.campaign.create",
            payload_model=CreateMetaCampaignPayload,
            approval=True,
            spend=True,
        ),
        _definition(
            "launch_meta_campaign",
            category="advertising",
            risk="critical",
            capability="advertising.meta.campaign.launch",
            payload_model=LaunchMetaCampaignPayload,
            approval=True,
            spend=True,
            campaign_launch=True,
        ),
        _definition(
            "create_google_ads_campaign",
            category="advertising",
            risk="high",
            capability="advertising.google.campaign.create",
            payload_model=CreateGoogleAdsCampaignPayload,
            approval=True,
            spend=True,
        ),
        _definition(
            "launch_google_ads_campaign",
            category="advertising",
            risk="critical",
            capability="advertising.google.campaign.launch",
            payload_model=LaunchGoogleAdsCampaignPayload,
            approval=True,
            spend=True,
            campaign_launch=True,
        ),
        _definition(
            "change_ad_budget",
            category="advertising",
            risk="critical",
            capability="advertising.campaign.budget.change",
            payload_model=ChangeAdBudgetPayload,
            approval=True,
            spend=True,
        ),
        _definition(
            "pause_ad_campaign",
            category="advertising",
            risk="high",
            capability="advertising.campaign.pause",
            payload_model=PauseAdCampaignPayload,
            approval=True,
            destructive=True,
        ),
        _definition(
            "update_crm",
            category="crm",
            risk="low",
            capability="crm.customer.update",
            payload_model=UpdateCRMPayload,
        ),
        _definition(
            "create_order",
            category="orders",
            risk="high",
            capability="orders.create",
            payload_model=CreateOrderPayload,
            approval=True,
            spend=True,
        ),
    )
)
