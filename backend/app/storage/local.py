import asyncio
import os
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from app.storage.base import (
    InvalidStorageKeyError,
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

    async def delete(self, object_key: str) -> None:
        path = self._resolve(object_key)
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            raise StorageOperationError("Unable to delete object") from None

    def public_url(self, object_key: str) -> str:
        key = validate_storage_key(object_key).as_posix()
        return f"{self.public_path}/{quote(key, safe='/')}"

    def _resolve(self, object_key: str) -> Path:
        key = validate_storage_key(object_key)
        candidate = self.root_directory.joinpath(*key.parts).resolve()
        try:
            candidate.relative_to(self.root_directory)
        except ValueError:
            raise InvalidStorageKeyError("Invalid object storage key") from None
        return candidate
