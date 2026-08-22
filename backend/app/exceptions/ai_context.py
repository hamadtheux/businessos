class AIContextError(Exception):
    """Base exception for AI context assembly failures."""


class AIContextAssemblyError(AIContextError):
    """
    Raised when trusted business context cannot be assembled safely.

    Internal database or source details must not be exposed through this
    exception to API consumers.
    """


class AIContextValidationError(AIContextError):
    """Raised when an internal context assembly request is invalid."""