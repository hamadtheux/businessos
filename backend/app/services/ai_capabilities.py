from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, Mapping

from app.schemas.ai_agent import AIAgentRole


CapabilityCategory = Literal["read", "analysis", "draft", "governed_action"]


@dataclass(frozen=True, slots=True)
class AICapability:
    key: str
    category: CapabilityCategory
    description: str


def _cap(key: str, category: CapabilityCategory, description: str) -> AICapability:
    return AICapability(key=key, category=category, description=description)


_CAPABILITIES: Final[dict[str, AICapability]] = {
    item.key: item
    for item in (
        _cap("read_business_brain", "read", "Read authoritative Business Brain context."),
        _cap("read_customers", "read", "Read bounded customer records and aggregates."),
        _cap("read_crm", "read", "Read CRM pipeline and follow-up state."),
        _cap("read_orders", "read", "Read order state and aggregates."),
        _cap("read_conversations", "read", "Read bounded administrative conversation context."),
        _cap("read_scheduling", "read", "Read authoritative appointments and available slots."),
        _cap("read_marketing", "read", "Read marketing plans, campaigns, content, and performance."),
        _cap("read_analytics", "read", "Read aggregate business analytics."),
        _cap("read_reports", "read", "Read business reports."),
        _cap("read_opportunities", "read", "Read business opportunities."),
        _cap("read_workflows", "read", "Read workflow definitions and run failures."),
        _cap("read_approvals", "read", "Read the business approval queue."),
        _cap("read_integrations", "read", "Read safe connector status and selected-resource metadata, never credentials."),
        _cap("analyze_sales", "analysis", "Analyze pipeline and sales performance."),
        _cap("analyze_marketing", "analysis", "Analyze marketing performance and plans."),
        _cap("analyze_operations", "analysis", "Analyze operational state and exceptions."),
        _cap("analyze_support", "analysis", "Analyze administrative support context."),
        _cap("analyze_business", "analysis", "Analyze a bounded cross-module business summary."),
        _cap("analyze_trends", "analysis", "Analyze recorded trends without claiming causal certainty."),
        _cap("draft_customer_response", "draft", "Draft a customer response without sending it."),
        _cap("draft_campaign", "draft", "Draft a campaign plan without launching it."),
        _cap("draft_follow_up", "draft", "Draft a sales follow-up without sending it."),
        _cap("draft_report", "draft", "Draft an evidence-based report."),
        _cap("propose_workflow", "draft", "Propose a validated workflow draft."),
        _cap("recommend_slots", "draft", "Recommend only authoritative scheduling slots."),
        _cap("propose_send_email", "governed_action", "Propose an email through AIAction governance."),
        _cap("propose_send_whatsapp", "governed_action", "Propose a WhatsApp message; no connector dispatch."),
        _cap("propose_send_customer_message", "governed_action", "Propose a customer message through governance."),
        _cap("propose_publish_social", "governed_action", "Propose social publication through governance."),
        _cap("propose_campaign_launch", "governed_action", "Propose campaign creation or launch through governance."),
        _cap("propose_budget_change", "governed_action", "Propose an advertising budget change through governance."),
        _cap("propose_crm_update", "governed_action", "Propose a CRM update through governance."),
        _cap("propose_order_creation", "governed_action", "Propose order creation through governance."),
    )
}

AI_CAPABILITY_REGISTRY: Final[Mapping[str, AICapability]] = MappingProxyType(
    _CAPABILITIES
)

_COMMON = {"read_business_brain"}
ROLE_CAPABILITIES: Final[Mapping[AIAgentRole, frozenset[str]]] = MappingProxyType(
    {
        "business_manager": frozenset(_COMMON | {
            "read_customers", "read_crm", "read_orders", "read_conversations",
            "read_scheduling", "read_marketing", "read_analytics", "read_reports",
            "read_opportunities", "read_workflows", "read_approvals",
            "read_integrations",
            "analyze_sales", "analyze_marketing", "analyze_operations",
            "analyze_support", "analyze_business", "analyze_trends",
            "draft_campaign", "draft_follow_up", "draft_report", "propose_workflow",
            "recommend_slots", "propose_send_email", "propose_send_whatsapp",
            "propose_send_customer_message", "propose_publish_social",
            "propose_campaign_launch", "propose_budget_change", "propose_crm_update",
            "propose_order_creation",
        }),
        "cmo": frozenset(_COMMON | {
            "read_marketing", "read_analytics", "read_reports", "read_opportunities",
            "read_integrations",
            "analyze_marketing", "analyze_trends", "draft_campaign", "draft_report",
            "propose_send_email", "propose_publish_social", "propose_campaign_launch",
            "propose_budget_change",
        }),
        "sales": frozenset(_COMMON | {
            "read_customers", "read_crm", "read_orders", "read_opportunities",
            "read_analytics", "analyze_sales", "draft_follow_up", "draft_report",
            "read_integrations",
            "propose_send_email", "propose_send_whatsapp",
            "propose_send_customer_message", "propose_crm_update",
            "propose_order_creation",
        }),
        "support": frozenset(_COMMON | {
            "read_customers", "read_orders", "read_conversations", "read_scheduling",
            "read_integrations",
            "analyze_support", "draft_customer_response", "recommend_slots",
            "propose_send_email", "propose_send_whatsapp",
            "propose_send_customer_message",
        }),
        "operations": frozenset(_COMMON | {
            "read_orders", "read_scheduling", "read_workflows", "read_analytics",
            "read_integrations",
            "read_reports", "analyze_operations", "draft_report", "propose_workflow",
            "recommend_slots", "propose_order_creation",
        }),
        "analytics": frozenset(_COMMON | {
            "read_customers", "read_crm", "read_orders", "read_marketing",
            "read_analytics", "read_reports", "read_opportunities", "read_workflows",
            "read_integrations",
            "analyze_sales", "analyze_marketing", "analyze_operations",
            "analyze_business", "analyze_trends", "draft_report",
        }),
    }
)

ACTION_CAPABILITY: Final[Mapping[str, str]] = MappingProxyType({
    "send_email": "propose_send_email",
    "send_whatsapp_message": "propose_send_whatsapp",
    "send_customer_message": "propose_send_customer_message",
    "publish_social_post": "propose_publish_social",
    "create_meta_campaign": "propose_campaign_launch",
    "launch_meta_campaign": "propose_campaign_launch",
    "create_google_ads_campaign": "propose_campaign_launch",
    "launch_google_ads_campaign": "propose_campaign_launch",
    "change_ad_budget": "propose_budget_change",
    "pause_ad_campaign": "propose_campaign_launch",
    "update_crm": "propose_crm_update",
    "create_order": "propose_order_creation",
})


def capabilities_for_role(role: AIAgentRole) -> tuple[AICapability, ...]:
    return tuple(
        AI_CAPABILITY_REGISTRY[key]
        for key in sorted(ROLE_CAPABILITIES[role])
    )


def validate_role_capabilities(role: AIAgentRole, values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(values))
    if len(normalized) != len(values):
        raise ValueError("Capabilities cannot contain duplicates")
    if any(value not in ROLE_CAPABILITIES[role] for value in normalized):
        raise ValueError("Capability is not allowed for this agent role")
    return tuple(sorted(normalized))


def validate_proposed_action_capabilities(
    role: AIAgentRole,
    allowed_capabilities: tuple[str, ...],
    action_types: list[str],
) -> None:
    role_allowed = ROLE_CAPABILITIES[role]
    configured = set(allowed_capabilities)
    for action_type in action_types:
        capability = ACTION_CAPABILITY.get(action_type)
        if capability is None or capability not in role_allowed or capability not in configured:
            raise ValueError("Agent proposed an action outside its server-owned capabilities")
