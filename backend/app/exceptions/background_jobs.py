class BackgroundJobError(Exception):
    pass


class BackgroundJobNotFoundError(BackgroundJobError):
    pass


class BackgroundJobStateError(BackgroundJobError):
    pass


class BackgroundJobValidationError(BackgroundJobError):
    pass


class BackgroundJobPersistenceError(BackgroundJobError):
    pass
