class AIAgentExecutionLedgerError(Exception):
    """Base exception for AI agent execution-ledger failures."""


class AIAgentExecutionNotFoundError(
    AIAgentExecutionLedgerError
):
    """Raised when an execution cannot be found in the authorized business."""


class AIAgentExecutionPersistenceError(
    AIAgentExecutionLedgerError
):
    """Raised when execution-ledger persistence cannot safely complete."""


class AIAgentExecutionStateError(
    AIAgentExecutionLedgerError
):
    """Raised when an invalid execution lifecycle transition is requested."""


class AIAgentExecutionValidationError(
    AIAgentExecutionLedgerError
):
    """Raised when trusted internal execution metadata is invalid."""