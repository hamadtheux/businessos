class BusinessListingError(Exception):
    """Base exception for accessible-business listing failures."""


class BusinessListingPersistenceError(BusinessListingError):
    """Raised when accessible businesses cannot be loaded safely."""


class BusinessOnboardingError(Exception):
    """Base exception for business onboarding failures."""


class BusinessOnboardingConflictError(BusinessOnboardingError):
    """Raised when onboarding conflicts with an existing resource."""


class BusinessOnboardingPersistenceError(BusinessOnboardingError):
    """Raised when onboarding cannot be persisted safely."""


class BusinessBrandingError(Exception):
    """Base exception for business-branding failures."""


class BusinessBrandingPersistenceError(BusinessBrandingError):
    """Raised when business branding cannot be persisted safely."""


class BusinessProfilePersistenceError(Exception):
    """Raised when an authoritative business profile cannot be persisted."""
