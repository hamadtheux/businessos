import asyncio
import os
import stat
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

from app.storage.base import (
    InvalidStorageKeyError,
    ObjectNotFoundError,
    ObjectStorage,
    StorageOperationError,
    validate_storage_key,
)


class LocalObjectStorage(ObjectStorage):
    def __init__(self, root_directory: Path, public_path: str) -> None:
        self.root_directory = root_directory.resolve()
        self.public_path = "/" + public_path.strip("/")

    async def put(
        self,
        object_key: str,
        content: bytes,
        content_type: str,
    ) -> None:
        del content_type
        path = self._resolve(object_key)
        temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")

        def write_atomically() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with temporary_path.open("xb") as file_handle:
                    file_handle.write(content)
                    file_handle.flush()
                    os.fsync(file_handle.fileno())
                os.replace(temporary_path, path)
            finally:
                temporary_path.unlink(missing_ok=True)

        try:
            await asyncio.to_thread(write_atomically)
        except OSError:
            raise StorageOperationError("Unable to store object") from None

    async def put_file(
        self,
        object_key: str,
        source_path: Path,
        content_type: str,
        *,
        max_bytes: int,
    ) -> None:
        del content_type

        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

        destination = self._resolve(object_key)
        source = Path(source_path)
        temporary_path = destination.with_name(
            f".{destination.name}.{uuid4().hex}.tmp"
        )

        def copy_atomically() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)

            try:
                with source.open("rb") as source_handle:
                    source_stat = os.fstat(source_handle.fileno())

                    if not stat.S_ISREG(source_stat.st_mode):
                        raise StorageOperationError(
                            "Upload source is not a regular file"
                        )

                    if source_stat.st_size > max_bytes:
                        raise StorageOperationError(
                            "Upload source exceeds file limit"
                        )

                    copied = 0
                    with temporary_path.open("xb") as destination_handle:
                        while True:
                            chunk = source_handle.read(
                                min(
                                    1024 * 1024,
                                    max_bytes - copied + 1,
                                )
                            )
                            if not chunk:
                                break

                            copied += len(chunk)
                            if copied > max_bytes:
                                raise StorageOperationError(
                                    "Upload source exceeds file limit"
                                )

                            destination_handle.write(chunk)

                        destination_handle.flush()
                        os.fsync(destination_handle.fileno())

                    os.replace(temporary_path, destination)
            finally:
                temporary_path.unlink(missing_ok=True)

        try:
            await asyncio.to_thread(copy_atomically)
        except StorageOperationError:
            raise
        except OSError:
            raise StorageOperationError(
                "Unable to store object"
            ) from None

    async def get_file(
        self,
        object_key: str,
        destination_path: Path,
        *,
        max_bytes: int,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

        source = self._resolve(object_key)
        destination = Path(destination_path)
        temporary_path = destination.with_name(
            f".{destination.name}.{uuid4().hex}.tmp"
        )

        def copy_bounded() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)

            try:
                with source.open("rb") as source_handle:
                    source_stat = os.fstat(source_handle.fileno())

                    if not stat.S_ISREG(source_stat.st_mode):
                        raise StorageOperationError(
                            "Stored object is not a regular file"
                        )

                    if source_stat.st_size > max_bytes:
                        raise StorageOperationError(
                            "Stored object exceeds read limit"
                        )

                    copied = 0
                    with temporary_path.open("xb") as destination_handle:
                        while True:
                            chunk = source_handle.read(
                                min(
                                    1024 * 1024,
                                    max_bytes - copied + 1,
                                )
                            )
                            if not chunk:
                                break

                            copied += len(chunk)
                            if copied > max_bytes:
                                raise StorageOperationError(
                                    "Stored object exceeds read limit"
                                )

                            destination_handle.write(chunk)

                        destination_handle.flush()
                        os.fsync(destination_handle.fileno())

                    os.replace(temporary_path, destination)
            except FileNotFoundError:
                raise ObjectNotFoundError(
                    "Stored object was not found"
                ) from None
            finally:
                temporary_path.unlink(missing_ok=True)

        try:
            await asyncio.to_thread(copy_bounded)
        except (ObjectNotFoundError, StorageOperationError):
            raise
        except OSError:
            raise StorageOperationError(
                "Unable to read object"
            ) from None

    async def get(
        self,
        object_key: str,
        *,
        max_bytes: int,
    ) -> bytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

        path = self._resolve(object_key)

        def read_bounded() -> bytes:
            try:
                with path.open("rb") as file_handle:
                    content = file_handle.read(max_bytes + 1)
            except FileNotFoundError:
                raise ObjectNotFoundError("Stored object was not found") from None
            except OSError:
                raise StorageOperationError("Unable to read object") from None

            if len(content) > max_bytes:
                raise StorageOperationError("Stored object exceeds read limit")

            return content

        return await asyncio.to_thread(read_bounded)

    async def delete(self, object_key: str) -> None:
        path = self._resolve(object_key)
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            raise StorageOperationError("Unable to delete object") from None

    def public_url(self, object_key: str) -> str:
        key = validate_storage_key(object_key).as_posix()
        return f"{self.public_path}/{quote(key, safe='/')}"

    def object_key_from_reference(self, storage_reference: str) -> str:
        if not isinstance(storage_reference, str) or len(storage_reference) > 1024:
            raise StorageOperationError("Invalid object storage reference")
        prefix = f"{self.public_path}/"
        try:
            parsed = urlsplit(storage_reference)
        except ValueError:
            raise StorageOperationError("Invalid object storage reference") from None
        if (
            not storage_reference.startswith(prefix)
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            raise StorageOperationError("Invalid object storage reference")
        key = validate_storage_key(unquote(storage_reference[len(prefix):])).as_posix()
        if self.public_url(key) != storage_reference:
            raise StorageOperationError("Invalid object storage reference")
        return key

    def presentation_url(
        self,
        object_key: str,
        *,
        expires_in_seconds: int,
    ) -> str:
        del expires_in_seconds
        return self.public_url(object_key)

    def _resolve(self, object_key: str) -> Path:
        key = validate_storage_key(object_key)
        candidate = self.root_directory.joinpath(*key.parts).resolve()
        try:
            candidate.relative_to(self.root_directory)
        except ValueError:
            raise InvalidStorageKeyError("Invalid object storage key") from None
        return candidate
