class ApprovalError(Exception):
    """Base exception for safe approval lifecycle failures."""


class ApprovalNotFoundError(ApprovalError):
    """Raised when a tenant-scoped approval does not exist."""


class ApprovalStateError(ApprovalError):
    """Raised when a requested approval transition is impossible."""


class ApprovalConflictError(ApprovalError):
    """Raised when approval idempotency or ownership conflicts."""


class ApprovalValidationError(ApprovalError):
    """Raised when trusted approval input is invalid."""


class ApprovalPersistenceError(ApprovalError):
    """Raised when approval persistence cannot safely complete."""
