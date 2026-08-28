class GrowthLearningError(Exception):
    """Base class for safe growth-learning domain failures."""


class GrowthLearningNotFoundError(GrowthLearningError):
    pass


class GrowthLearningValidationError(GrowthLearningError):
    pass


class GrowthLearningStateError(GrowthLearningError):
    pass


class GrowthLearningPersistenceError(GrowthLearningError):
    pass
