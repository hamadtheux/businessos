from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.catalog import CatalogImportPreviewResponse


class CatalogError(Exception):
    """Base exception for safe catalog domain failures."""


class CatalogItemNotFoundError(CatalogError):
    """Raised when a tenant-scoped catalog item is unavailable."""


class CatalogSkuConflictError(CatalogError):
    """Raised when a SKU conflicts within a business."""

    def __init__(self, message: str, *, sku: str | None = None) -> None:
        super().__init__(message)
        self.sku = sku


class CatalogPersistenceError(CatalogError):
    """Raised when catalog persistence cannot complete safely."""


class CatalogImportError(CatalogError):
    """Base exception for safe catalog import failures."""


class CatalogImportFileError(CatalogImportError):
    """Raised when an uploaded catalog file cannot be parsed safely."""


class CatalogImportTooLargeError(CatalogImportFileError):
    """Raised when an uploaded catalog file exceeds a centralized limit."""


class CatalogImportValidationError(CatalogImportError):
    """Raised when an atomic catalog import contains invalid rows."""

    def __init__(self, preview: "CatalogImportPreviewResponse") -> None:
        super().__init__("Catalog import validation failed")
        self.preview = preview
