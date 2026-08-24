class MarketingError(Exception):
    """Base class for safe marketing-domain failures."""


class MarketingNotFoundError(MarketingError):
    pass


class MarketingValidationError(MarketingError):
    pass


class MarketingStateError(MarketingError):
    pass


class MarketingPersistenceError(MarketingError):
    pass


class MarketingAIError(MarketingError):
    pass
