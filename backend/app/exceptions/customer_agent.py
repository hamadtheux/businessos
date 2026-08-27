class CustomerAgentError(Exception):
    """Base exception for safe Customer Agent orchestration failures."""


class CustomerAgentNotFoundError(CustomerAgentError):
    pass


class CustomerAgentValidationError(CustomerAgentError):
    pass


class CustomerAgentPersistenceError(CustomerAgentError):
    pass
