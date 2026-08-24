class ChatbotError(Exception):
    """Base class for safe public-chatbot domain failures."""


class ChatbotNotFoundError(ChatbotError):
    pass


class ChatbotDisabledError(ChatbotError):
    pass


class ChatbotOriginError(ChatbotError):
    pass


class ChatbotAuthorizationError(ChatbotError):
    pass


class ChatbotValidationError(ChatbotError):
    pass


class ChatbotConflictError(ChatbotError):
    pass


class ChatbotRateLimitError(ChatbotError):
    pass


class ChatbotPersistenceError(ChatbotError):
    pass
