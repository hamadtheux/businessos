from __future__ import annotations


WORKFLOW_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active", "archived"}),
    "active": frozenset({"paused", "archived"}),
    "paused": frozenset({"active", "archived"}),
    "archived": frozenset(),
}

RUN_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "canceled"})
RUN_CANCELABLE_STATUSES = frozenset({"queued", "waiting"})

NODE_TYPES = frozenset({
    "trigger", "condition", "branch", "action", "delay", "approval",
    "ai", "internal_operation", "end",
})

TRIGGER_TYPES = frozenset({
    "customer_created", "customer_updated",
    "lead_created", "lead_stage_changed", "lead_qualified", "lead_won", "lead_lost",
    "order_created", "order_status_changed",
    "conversation_created", "inbound_message_recorded",
    "appointment_created", "appointment_canceled", "appointment_rescheduled", "appointment_starting_soon",
    "campaign_created", "campaign_status_changed", "content_ready_for_review", "campaign_completed",
    "opportunity_created", "opportunity_status_changed",
    "ai_action_requires_approval", "ai_execution_completed",
    "integration_event_received", "integration_health_changed",
    "website_chat_started", "lead_captured", "website_appointment_booked",
    "human_handoff_requested",
    "scheduled_time", "manual_test",
})

FAILURE_CODES = frozenset({
    "graph_invalid", "node_configuration_invalid", "condition_input_missing",
    "internal_operation_failed", "ai_temporarily_unavailable", "ai_output_invalid",
    "action_governance_failed", "approval_rejected", "approval_expired",
    "retry_exhausted", "forced_simulation_failure", "run_state_conflict",
})

RETRYABLE_FAILURE_CODES = frozenset({
    "internal_operation_failed", "ai_temporarily_unavailable",
})
