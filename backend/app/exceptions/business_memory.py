class BusinessMemoryError(Exception):
    """Base exception for safe persistent-memory domain failures."""


class BusinessMemoryNotFoundError(BusinessMemoryError):
    """Raised when a tenant-scoped business memory is unavailable."""


class BusinessMemoryPersistenceError(BusinessMemoryError):
    """Raised when persistent-memory storage cannot complete safely."""


class BusinessMemoryCursorError(BusinessMemoryError):
    """Raised when a memory pagination cursor is invalid or unusable."""


class BusinessMemorySupersessionError(BusinessMemoryError):
    """Raised when a requested memory supersession is invalid."""