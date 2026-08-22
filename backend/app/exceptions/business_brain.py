class BusinessBrainError(Exception):
    """Base exception for safe Business Brain domain failures."""


class BusinessKnowledgeError(BusinessBrainError):
    """Base exception for safe curated-knowledge failures."""


class BusinessKnowledgeEntryNotFoundError(BusinessKnowledgeError):
    """Raised when a tenant-scoped knowledge entry is unavailable."""


class BusinessKnowledgePersistenceError(BusinessKnowledgeError):
    """Raised when knowledge persistence cannot complete safely."""


class BusinessBrainAssemblyError(BusinessBrainError):
    """Raised when authoritative Business Brain sources cannot be assembled."""
