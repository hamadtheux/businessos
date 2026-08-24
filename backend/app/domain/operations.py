from enum import StrEnum


class CustomerStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class LeadStage(StrEnum):
    NEW = "new"
    QUALIFIED = "qualified"
    CONTACTED = "contacted"
    VIEWING = "viewing"
    PROPOSAL = "proposal"
    WON = "won"
    LOST = "lost"


class OrderStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELED = "canceled"


class ConversationStatus(StrEnum):
    OPEN = "open"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


class OpportunityStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WON = "won"
    LOST = "lost"
    DISMISSED = "dismissed"


ORDER_STATUS_TRANSITIONS = {
    "draft": frozenset({"confirmed", "canceled"}),
    "confirmed": frozenset({"processing", "canceled"}),
    "processing": frozenset({"completed", "canceled"}),
    "completed": frozenset(),
    "canceled": frozenset(),
}

OPPORTUNITY_STATUS_TRANSITIONS = {
    "open": frozenset({"in_progress", "won", "lost", "dismissed"}),
    "in_progress": frozenset({"won", "lost", "dismissed"}),
    "won": frozenset(),
    "lost": frozenset(),
    "dismissed": frozenset(),
}

LEAD_STAGE_TRANSITIONS = {
    "new": frozenset({"qualified", "lost"}),
    "qualified": frozenset({"new", "contacted", "viewing", "proposal", "won", "lost"}),
    "contacted": frozenset({"qualified", "viewing", "proposal", "won", "lost"}),
    "viewing": frozenset({"qualified", "contacted", "proposal", "won", "lost"}),
    "proposal": frozenset({"qualified", "contacted", "viewing", "won", "lost"}),
    "won": frozenset(),
    "lost": frozenset(),
}
