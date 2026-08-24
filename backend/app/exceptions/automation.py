class AutomationError(Exception):
    pass


class AutomationNotFoundError(AutomationError):
    pass


class AutomationValidationError(AutomationError):
    pass


class AutomationStateError(AutomationError):
    pass


class AutomationConflictError(AutomationError):
    pass


class AutomationPersistenceError(AutomationError):
    pass


class AutomationAIError(AutomationError):
    pass
