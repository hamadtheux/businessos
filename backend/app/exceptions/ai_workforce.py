class AIWorkforceError(Exception):
    """Base safe AI workforce error."""


class AIWorkforceValidationError(AIWorkforceError):
    pass


class AIWorkforceNotFoundError(AIWorkforceError):
    pass


class AIWorkforceConflictError(AIWorkforceError):
    pass


class AIWorkforcePersistenceError(AIWorkforceError):
    pass
