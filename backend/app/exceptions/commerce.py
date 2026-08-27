class CommerceError(Exception):
    """Base class for safe commerce-domain failures."""


class CommerceNotFoundError(CommerceError):
    pass


class CommerceValidationError(CommerceError):
    pass


class CommerceConflictError(CommerceError):
    pass


class CommerceConfigurationRequiredError(CommerceError):
    def __init__(self, code: str = "configuration_required") -> None:
        self.code = code
        super().__init__(code)


class CommercePersistenceError(CommerceError):
    pass


class CommerceProviderError(CommerceError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)
