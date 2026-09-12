from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath


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

    async def put_file(
        self,
        object_key: str,
        source_path: Path,
        content_type: str,
        *,
        max_bytes: int,
    ) -> None:
        """
        Persist a server-owned regular file without materializing it as bytes.

        Concrete production storage implementations override this method.
        Keeping the base contract non-abstract preserves lightweight test
        doubles which never use file-backed uploads.
        """
        del object_key, source_path, content_type, max_bytes
        raise NotImplementedError

    async def get_file(
        self,
        object_key: str,
        destination_path: Path,
        *,
        max_bytes: int,
    ) -> None:
        """
        Stream a trusted stored object into a server-owned local file.

        Production implementations override this without loading the entire
        object into Python memory. Lightweight test doubles remain compatible.
        """
        del object_key, destination_path, max_bytes
        raise NotImplementedError

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
