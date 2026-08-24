class AutomationIntelligenceError(Exception):
    pass


class AutomationIntelligenceNotFoundError(AutomationIntelligenceError):
    pass


class AutomationIntelligenceValidationError(AutomationIntelligenceError):
    pass


class AutomationIntelligencePersistenceError(AutomationIntelligenceError):
    pass


class AutomationIntelligenceProviderError(AutomationIntelligenceError):
    pass
