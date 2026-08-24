class ActionExecutionAttemptError(Exception):
    """Base exception for safe durable-dispatch failures."""


class ActionExecutionAttemptNotFoundError(ActionExecutionAttemptError):
    """Raised when a tenant-scoped attempt is unavailable."""


class ActionExecutionAttemptStateError(ActionExecutionAttemptError):
    """Raised when an attempt lifecycle transition is invalid."""


class ActionExecutionAttemptConflictError(ActionExecutionAttemptError):
    """Raised when durable attempt identity or ownership conflicts."""


class ActionExecutionAttemptValidationError(ActionExecutionAttemptError):
    """Raised when trusted dispatch input is invalid."""


class ActionExecutionAttemptPersistenceError(ActionExecutionAttemptError):
    """Raised when attempt persistence cannot safely complete."""


class ActionExecutionOutcomeUncertainError(ActionExecutionAttemptError):
    """Raised when an ambiguous external outcome prohibits automatic retry."""


class DirectActionDispatchDisabledError(ActionExecutionAttemptError):
    """Raised by the retired transaction-coupled handler entry point."""
