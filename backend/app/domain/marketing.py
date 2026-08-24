from enum import StrEnum


class MarketingPlanStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELED = "canceled"


class ContentStatus(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    READY_TO_PUBLISH = "ready_to_publish"
    ARCHIVED = "archived"


class TrendStatus(StrEnum):
    DETECTED = "detected"
    REVIEWED = "reviewed"
    ACTED_ON = "acted_on"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


MARKETING_PLAN_TRANSITIONS = {
    "draft": frozenset({"ready", "archived"}),
    "ready": frozenset({"draft", "active", "archived"}),
    "active": frozenset({"completed", "archived"}),
    "completed": frozenset({"archived"}),
    "archived": frozenset(),
}


CAMPAIGN_TRANSITIONS = {
    "draft": frozenset({"planned", "awaiting_approval", "canceled"}),
    "planned": frozenset({"draft", "awaiting_approval", "canceled"}),
    "awaiting_approval": frozenset({"draft", "approved", "canceled"}),
    "approved": frozenset({"scheduled", "canceled"}),
    "scheduled": frozenset({"active", "canceled"}),
    "active": frozenset({"paused", "completed", "canceled"}),
    "paused": frozenset({"active", "completed", "canceled"}),
    "completed": frozenset(),
    "canceled": frozenset(),
}


CONTENT_TRANSITIONS = {
    "draft": frozenset({"review", "archived"}),
    "review": frozenset({"draft", "approved", "archived"}),
    "approved": frozenset({"scheduled", "ready_to_publish", "archived"}),
    "scheduled": frozenset({"approved", "ready_to_publish", "archived"}),
    "ready_to_publish": frozenset({"archived"}),
    "archived": frozenset(),
}


TREND_TRANSITIONS = {
    "detected": frozenset({"reviewed", "dismissed", "expired"}),
    "reviewed": frozenset({"acted_on", "dismissed", "expired"}),
    "acted_on": frozenset({"expired"}),
    "dismissed": frozenset(),
    "expired": frozenset(),
}
