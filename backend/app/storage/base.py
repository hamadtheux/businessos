from abc import ABC, abstractmethod
from pathlib import PurePosixPath


class StorageError(Exception):
    """Safe base exception for object-storage failures."""


class StorageOperationError(StorageError):
    """Raised when a trusted storage operation cannot be completed."""


class ObjectNotFoundError(StorageOperationError):
    """Raised only when a valid storage key has no stored object."""


class InvalidStorageKeyError(StorageError):
    """Raised when an object key falls outside the controlled namespace."""


class ObjectStorage(ABC):
    @abstractmethod
    async def put(
        self,
        object_key: str,
        content: bytes,
        content_type: str,
    ) -> None:
        """Persist bytes under a server-generated object key."""

    @abstractmethod
    async def get(
        self,
        object_key: str,
        *,
        max_bytes: int,
    ) -> bytes:
        """
        Read trusted object bytes with an explicit memory bound.

        Callers must choose a limit appropriate to the asset type. Implementations
        must fail closed rather than return content larger than max_bytes.
        """

    @abstractmethod
    async def delete(self, object_key: str) -> None:
        """Delete a stored object idempotently."""

    @abstractmethod
    def public_url(self, object_key: str) -> str:
        """Return the durable canonical reference for an object key."""

    def object_key_from_reference(self, storage_reference: str) -> str:
        """Resolve a canonical server-owned reference back to its object key."""
        raise InvalidStorageKeyError("Invalid object storage reference")

    def presentation_url(
        self,
        object_key: str,
        *,
        expires_in_seconds: int,
    ) -> str:
        """Return a browser-loadable URL for a previously validated object key."""
        del expires_in_seconds
        return self.public_url(object_key)


def validate_storage_key(object_key: str) -> PurePosixPath:
    if not object_key or "\\" in object_key or len(object_key) > 1024:
        raise InvalidStorageKeyError("Invalid object storage key")
    path = PurePosixPath(object_key)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InvalidStorageKeyError("Invalid object storage key")
    if path.as_posix() != object_key:
        raise InvalidStorageKeyError("Invalid object storage key")
    return path
