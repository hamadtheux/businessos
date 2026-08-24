from __future__ import annotations

from typing import Final, Literal

from app.schemas.ai_agent import AIAgentRole


AutonomyMode = Literal["manual", "supervised", "autonomous"]
CommandStatus = Literal[
    "queued", "running", "completed", "needs_approval", "failed", "canceled"
]
CommandIntent = Literal[
    "business_overview",
    "daily_focus",
    "sales_analysis",
    "lead_follow_up",
    "customer_support",
    "draft_response",
    "marketing_plan",
    "marketing_analysis",
    "operations_analysis",
    "scheduling_lookup",
    "workflow_analysis",
    "analytics_analysis",
    "reporting",
    "integration_status",
    "chatbot_analytics",
    "unknown",
]

CANONICAL_AGENT_ROLES: Final[tuple[AIAgentRole, ...]] = (
    "business_manager",
    "cmo",
    "sales",
    "support",
    "operations",
    "analytics",
)

MAX_CUSTOM_INSTRUCTIONS_LENGTH: Final = 2_000
MAX_COMMAND_LENGTH: Final = 4_000
MAX_DELEGATION_DEPTH: Final = 1
MAX_SPECIALIST_CALLS: Final = 3
MAX_MODEL_CALLS_PER_COMMAND: Final = 4
MAX_SERVER_CONTEXT_LENGTH: Final = 8_000

AUTONOMY_DESCRIPTIONS: Final[dict[AutonomyMode, str]] = {
    "manual": "AI recommends and drafts; a user drives all actions.",
    "supervised": (
        "AI may perform explicitly allowed internal work; policy still requires "
        "approval for risky or external-looking actions."
    ),
    "autonomous": (
        "AI may proceed only with server-approved low-risk internal work. "
        "Mandatory approval, spend, destructive, and communication rules remain active."
    ),
}
