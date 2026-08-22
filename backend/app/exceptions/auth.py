class UserRegistrationError(Exception):
    """Base exception for user registration failures."""


class UserAlreadyExistsError(UserRegistrationError):
    """Raised when a user already exists for the requested email address."""


class UserRegistrationPersistenceError(UserRegistrationError):
    """Raised when registration cannot be persisted safely."""


class UserAuthenticationError(Exception):
    """Base exception for user authentication failures."""


class InvalidCredentialsError(UserAuthenticationError):
    """Raised when supplied credentials cannot be authenticated."""


class UserAccountUnavailableError(UserAuthenticationError):
    """Raised when valid credentials belong to an unavailable account."""


class UserAuthenticationPersistenceError(UserAuthenticationError):
    """Raised when authentication cannot access persistence safely."""


class RefreshSessionError(UserAuthenticationError):
    """Base exception for persistent refresh-session failures."""


class InvalidRefreshTokenError(RefreshSessionError):
    """Raised when a refresh token cannot authenticate a session."""


class RefreshTokenReuseDetectedError(RefreshSessionError):
    """Raised after replay of a rotated refresh token is detected."""


class RefreshSessionPersistenceError(RefreshSessionError):
    """Raised when refresh-session persistence cannot complete safely."""
