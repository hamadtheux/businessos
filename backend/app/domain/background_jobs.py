from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal


JobType = Literal[
    "process_automation_event",
    "resume_workflow_run",
    "process_scheduled_workflow",
    "process_integration_event",
    "customer_agent_response",
    "dispatch_action_execution",
    "reconcile_uncertain_attempt",
    "mark_social_schedule_ready",
    "maintain_subscription",
    "discover_competitors",
    "generate_content_plan",
    "analyze_campaign_opportunities",
    "commerce_initial_sync",
    "commerce_incremental_sync",
    "commerce_webhook_reconcile",
    "google_merchant_status_sync",
    "meta_catalog_status_sync",
    "google_ads_performance_sync",
    "meta_ads_performance_sync",
]

JOB_STATUSES: Final = frozenset({
    "queued", "processing", "succeeded", "failed", "dead_letter", "canceled",
})
TERMINAL_JOB_STATUSES: Final = frozenset({
    "succeeded", "failed", "dead_letter", "canceled",
})
JOB_FAILURE_CODES: Final = frozenset({
    "dependency_unavailable",
    "external_execution_disabled",
    "invalid_job_state",
    "resource_not_found",
    "retry_exhausted",
    "uncertain_external_outcome",
    "workflow_execution_failed",
    "workflow_invalid",
    "provider_unavailable",
    "feature_not_entitled",
})


@dataclass(frozen=True, slots=True)
class JobPolicy:
    job_type: JobType
    reference_field: str
    priority: int
    max_attempts: int
    retryable: bool
    manually_retryable: bool
    lease_recoverable: bool


_POLICIES = {
    "process_automation_event": JobPolicy(
        "process_automation_event", "automation_event_id", 80, 4, True, True, True,
    ),
    "resume_workflow_run": JobPolicy(
        "resume_workflow_run", "workflow_run_id", 70, 4, True, True, True,
    ),
    "process_scheduled_workflow": JobPolicy(
        "process_scheduled_workflow", "workflow_id", 75, 4, True, True, True,
    ),
    "process_integration_event": JobPolicy(
        "process_integration_event", "integration_event_id", 90, 4, True, True, True,
    ),
    # Customer-agent reasoning is an internal, replay-safe operation.
    # It references the durable inbound automation event. Any resulting
    # external customer reply must still cross AIAction governance and the
    # non-replayable dispatch_action_execution boundary.
    "customer_agent_response": JobPolicy(
        "customer_agent_response",
        "automation_event_id",
        95,
        4,
        True,
        True,
        True,
    ),
    # A connector attempt is never blindly replayed by the queue. Ambiguous
    # outcomes are terminalized as uncertain by the dispatcher.
    "dispatch_action_execution": JobPolicy(
        "dispatch_action_execution",
        "action_execution_attempt_id",
        100,
        1,
        False,
        False,
        False,
    ),
    # This only changes an already-dispatching attempt to uncertain. It never
    # invokes a connector and therefore remains safe to reclaim after a crash.
    "reconcile_uncertain_attempt": JobPolicy(
        "reconcile_uncertain_attempt", "action_execution_attempt_id", 100, 3, True, True, True,
    ),
    "mark_social_schedule_ready": JobPolicy(
        "mark_social_schedule_ready", "social_schedule_id", 40, 3, True, True, True,
    ),
    "maintain_subscription": JobPolicy(
        "maintain_subscription", "subscription_id", 30, 3, True, True, True,
    ),
    "discover_competitors": JobPolicy(
        "discover_competitors", "competitor_discovery_run_id", 35, 3, True, True, True,
    ),
    "generate_content_plan": JobPolicy(
        "generate_content_plan", "marketing_automation_run_id", 25, 3, True, True, True,
    ),
    "analyze_campaign_opportunities": JobPolicy(
        "analyze_campaign_opportunities", "marketing_automation_run_id", 20, 3, True, True, True,
    ),
    "commerce_initial_sync": JobPolicy(
        "commerce_initial_sync", "commerce_sync_run_id", 65, 5, True, True, True,
    ),
    "commerce_incremental_sync": JobPolicy(
        "commerce_incremental_sync", "commerce_sync_run_id", 60, 5, True, True, True,
    ),
    "commerce_webhook_reconcile": JobPolicy(
        "commerce_webhook_reconcile", "commerce_webhook_receipt_id", 85, 5, True, True, True,
    ),
    "google_merchant_status_sync": JobPolicy(
        "google_merchant_status_sync", "commerce_feed_destination_id", 55, 5, True, True, True,
    ),
    "meta_catalog_status_sync": JobPolicy(
        "meta_catalog_status_sync", "commerce_feed_destination_id", 55, 5, True, True, True,
    ),
    "google_ads_performance_sync": JobPolicy(
        "google_ads_performance_sync", "marketing_campaign_id", 45, 5, True, True, True,
    ),
    "meta_ads_performance_sync": JobPolicy(
        "meta_ads_performance_sync", "marketing_campaign_id", 45, 5, True, True, True,
    ),
}

JOB_POLICIES: Final = MappingProxyType(_POLICIES)
JOB_TYPES: Final = frozenset(JOB_POLICIES)
JOB_REFERENCE_FIELDS: Final = (
    "automation_event_id",
    "workflow_id",
    "workflow_run_id",
    "node_run_id",
    "integration_event_id",
    "action_execution_attempt_id",
    "social_schedule_id",
    "subscription_id",
    "competitor_discovery_run_id",
    "marketing_automation_run_id",
    "commerce_sync_run_id",
    "commerce_webhook_receipt_id",
    "commerce_feed_destination_id",
    "marketing_campaign_id",
)


def require_job_policy(job_type: str) -> JobPolicy:
    policy = JOB_POLICIES.get(job_type)
    if policy is None:
        raise ValueError("job_type_invalid")
    return policy
