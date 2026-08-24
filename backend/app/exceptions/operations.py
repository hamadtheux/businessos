class OperationsError(Exception):
    """Base exception for safe operational-domain failures."""


class OperationsNotFoundError(OperationsError):
    pass


class OperationsValidationError(OperationsError):
    pass


class OperationsStateError(OperationsError):
    pass


class OperationsConflictError(OperationsError):
    pass


class OperationsPersistenceError(OperationsError):
    pass
