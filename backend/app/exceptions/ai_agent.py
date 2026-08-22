class AIAgentError(Exception):
    """Base exception for AI agent runtime failures."""


class AIAgentValidationError(AIAgentError):
    """
    Raised when an internal agent execution request is invalid.

    This represents a caller/runtime contract problem rather than an
    infrastructure or model-provider failure.
    """


class AIAgentContextError(AIAgentError):
    """
    Raised when trusted business context cannot be prepared for an agent.

    Internal context or database details must not leak through this error.
    """


class AIAgentProviderError(AIAgentError):
    """
    Raised when an AI model provider cannot complete a runtime request.

    Provider-specific exception details, credentials, HTTP payloads, and
    internal response bodies must not be exposed to API consumers.
    """


class AIAgentResponseError(AIAgentError):
    """
    Raised when a provider returns a response that violates the expected
    structured agent-output contract.
    """


class AIAgentExecutionError(AIAgentError):
    """
    Raised when an agent execution cannot safely complete for a reason that
    does not belong to a more specific runtime exception.
    """