from enum import StrEnum


class ActionExecutionAttemptStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    CANCELED = "canceled"


ACTIVE_ACTION_EXECUTION_ATTEMPT_STATUSES = frozenset(
    {
        ActionExecutionAttemptStatus.QUEUED,
        ActionExecutionAttemptStatus.DISPATCHING,
    }
)

TERMINAL_ACTION_EXECUTION_ATTEMPT_STATUSES = frozenset(
    {
        ActionExecutionAttemptStatus.SUCCEEDED,
        ActionExecutionAttemptStatus.FAILED,
        ActionExecutionAttemptStatus.UNCERTAIN,
        ActionExecutionAttemptStatus.CANCELED,
    }
)

ACTION_EXECUTION_UNCERTAIN_FAILURE_CODES = frozenset(
    {
        "external_outcome_uncertain",
        "dispatch_lease_expired",
        "connector_idempotency_unavailable",
    }
)

ACTION_EXECUTION_DEFINITE_FAILURE_CODES = frozenset(
    {
        "action_failed",
        "connector_rejected",
        "connector_validation_failed",
        "request_not_sent",
    }
)
