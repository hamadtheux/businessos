class AIActionError(Exception):
    """Base exception for AI action lifecycle failures."""


class AIActionValidationError(
    AIActionError
):
    """Raised when trusted action data is invalid."""


class AIActionNotFoundError(
    AIActionError
):
    """Raised when an action cannot be found in the authorized business."""


class AIActionPersistenceError(
    AIActionError
):
    """Raised when AI action persistence cannot safely complete."""


class AIActionStateError(
    AIActionError
):
    """Raised when an invalid AI action lifecycle transition is requested."""


class AIActionConflictError(
    AIActionError
):
    """Raised when an action would violate idempotency or ownership rules."""