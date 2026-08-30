from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final, Literal


EntitlementKind = Literal["feature", "resource", "usage"]
EntitlementValueType = Literal["boolean", "integer"]
BillingInterval = Literal["month", "year"]


@dataclass(frozen=True, slots=True)
class EntitlementDefinition:
    key: str
    kind: EntitlementKind
    value_type: EntitlementValueType
    description: str


_DEFINITIONS = {
    item.key: item for item in (
        EntitlementDefinition("ai_command_center", "feature", "boolean", "AI command center"),
        EntitlementDefinition("ai_agents", "feature", "boolean", "AI workforce execution"),
        EntitlementDefinition("website_chatbot", "feature", "boolean", "Public website chatbot"),
        EntitlementDefinition("automations", "feature", "boolean", "Workflow automation"),
        EntitlementDefinition("advanced_automations", "feature", "boolean", "Advanced workflow capabilities"),
        EntitlementDefinition("marketing_cmo", "feature", "boolean", "AI marketing generation"),
        EntitlementDefinition("campaigns", "feature", "boolean", "Campaign management"),
        EntitlementDefinition("competitor_intelligence", "feature", "boolean", "Competitor intelligence"),
        EntitlementDefinition("trend_intelligence", "feature", "boolean", "Trend intelligence"),
        EntitlementDefinition("scheduling", "feature", "boolean", "Scheduling tools"),
        EntitlementDefinition("integrations", "feature", "boolean", "External integrations"),
        EntitlementDefinition("advanced_analytics", "feature", "boolean", "Advanced analytics"),
        EntitlementDefinition("reports", "feature", "boolean", "Generated reports"),
        EntitlementDefinition("max_members", "resource", "integer", "Active members"),
        EntitlementDefinition("max_active_workflows", "resource", "integer", "Active workflows"),
        EntitlementDefinition("max_integrations", "resource", "integer", "Active integrations"),
        EntitlementDefinition("max_chatbot_sessions_month", "usage", "integer", "Chatbot sessions per period"),
        EntitlementDefinition("max_chatbot_messages_month", "usage", "integer", "Chatbot messages per period"),
        EntitlementDefinition("max_ai_executions_month", "usage", "integer", "AI executions per period"),
        EntitlementDefinition("max_ai_input_tokens_month", "usage", "integer", "AI input tokens per period"),
        EntitlementDefinition("max_ai_output_tokens_month", "usage", "integer", "AI output tokens per period"),
        EntitlementDefinition("max_automation_runs_month", "usage", "integer", "Automation runs per period"),
    )
}
ENTITLEMENTS: Final = MappingProxyType(_DEFINITIONS)
FEATURE_ENTITLEMENTS: Final = frozenset(
    key for key, item in ENTITLEMENTS.items() if item.kind == "feature"
)
RESOURCE_ENTITLEMENTS: Final = frozenset(
    key for key, item in ENTITLEMENTS.items() if item.kind == "resource"
)
USAGE_ENTITLEMENTS: Final = frozenset(
    key for key, item in ENTITLEMENTS.items() if item.kind == "usage"
)

# Historical plan versions contain this immutable row. It remains valid at the
# database constraint layer until those versions are retired, but it is not an
# active business-plan entitlement and must never be resolved or enforced.
LEGACY_INTEGER_ENTITLEMENT_KEYS: Final = frozenset({"max_businesses"})


def require_entitlement(key: str) -> EntitlementDefinition:
    try:
        return ENTITLEMENTS[key]
    except KeyError:
        raise ValueError("entitlement_key_invalid") from None


def validate_entitlement_value(key: str, value: bool | int) -> None:
    definition = require_entitlement(key)
    if definition.value_type == "boolean":
        if type(value) is not bool:
            raise ValueError("entitlement_value_type_invalid")
    elif type(value) is not int or value < 0:
        raise ValueError("entitlement_value_type_invalid")


def add_billing_period(instant: datetime, interval: BillingInterval) -> datetime:
    """Add one real calendar month/year, clamping month-end in UTC."""
    current = instant.astimezone(UTC)
    months = 1 if interval == "month" else 12
    total = current.year * 12 + current.month - 1 + months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return current.replace(year=year, month=month, day=day)


def utc_month_period(instant: datetime) -> tuple[datetime, datetime]:
    current = instant.astimezone(UTC)
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, add_billing_period(start, "month")
