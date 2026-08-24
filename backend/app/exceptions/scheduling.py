class SchedulingError(Exception):
    """Base exception for safe scheduling-domain failures."""


class SchedulingNotFoundError(SchedulingError):
    """Raised when a tenant-scoped scheduling resource is unavailable."""


class SchedulingValidationError(SchedulingError):
    """Raised when scheduling input or configuration is invalid."""


class SchedulingStateError(SchedulingError):
    """Raised when a requested lifecycle transition is invalid."""


class SchedulingConflictError(SchedulingError):
    """Raised when a requested time cannot be reserved safely."""


class SchedulingPersistenceError(SchedulingError):
    """Raised when scheduling data cannot be persisted safely."""
