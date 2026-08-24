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


class UnsupportedAIActionError(
    AIActionError
):
    """Raised when an action type is absent from the server registry."""


class AIActionPolicyError(
    AIActionError
):
    """Raised when deterministic policy evaluation cannot safely complete."""


class AIActionExecutionError(
    AIActionError
):
    """Raised when the internal action execution boundary fails safely."""


class AIActionHandlerNotFoundError(
    AIActionExecutionError
):
    """Raised when no explicitly registered inert/real handler exists."""
