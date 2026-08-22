class LogoError(Exception):
    """Base exception for safe business-logo failures."""


class LogoValidationError(LogoError):
    """Raised when uploaded bytes are not an acceptable logo image."""


class LogoTooLargeError(LogoValidationError):
    """Raised when an incoming logo exceeds the byte limit."""


class BusinessLogoPersistenceError(LogoError):
    """Raised when logo database state cannot be persisted safely."""
