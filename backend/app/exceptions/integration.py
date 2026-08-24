class IntegrationError(Exception):
    """Base integration-domain exception carrying no provider detail."""


class IntegrationNotFoundError(IntegrationError):
    pass


class IntegrationValidationError(IntegrationError):
    pass


class IntegrationStateError(IntegrationError):
    pass


class IntegrationConflictError(IntegrationError):
    pass


class IntegrationPersistenceError(IntegrationError):
    pass


class IntegrationProviderUnavailableError(IntegrationError):
    pass


class IntegrationCredentialUnavailableError(IntegrationError):
    pass


class IntegrationWebhookVerificationError(IntegrationError):
    pass
