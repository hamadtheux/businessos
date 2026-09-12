import asyncio
import os
import stat
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, unquote, urlsplit

import boto3
from botocore.exceptions import ClientError

from app.storage.base import (
    ObjectNotFoundError,
    ObjectStorage,
    StorageOperationError,
    validate_storage_key,
)


class S3Client(Protocol):
    def put_object(self, **kwargs: object) -> object: ...

    def get_object(self, **kwargs: object) -> object: ...

    def delete_object(self, **kwargs: object) -> object: ...

    def generate_presigned_url(self, *args: object, **kwargs: object) -> str: ...


class S3ObjectStorage(ObjectStorage):
    def __init__(
        self,
        *,
        bucket: str,
        public_base_url: str,
        region: str | None,
        endpoint_url: str | None,
        access_key_id: str,
        secret_access_key: str,
        client: S3Client | None = None,
    ) -> None:
        self.bucket = bucket
        self.public_base_url = public_base_url.rstrip("/")
        self.client = client or boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    async def put(
        self,
        object_key: str,
        content: bytes,
        content_type: str,
    ) -> None:
        key = validate_storage_key(object_key).as_posix()
        try:
            await asyncio.to_thread(
                self.client.put_object,
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
            )
        except Exception:
            raise StorageOperationError("Unable to store object") from None

    async def put_file(
        self,
        object_key: str,
        source_path: Path,
        content_type: str,
        *,
        max_bytes: int,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

        key = validate_storage_key(object_key).as_posix()
        source = Path(source_path)

        def upload_file() -> None:
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

                    self.client.put_object(
                        Bucket=self.bucket,
                        Key=key,
                        Body=source_handle,
                        ContentType=content_type,
                        CacheControl="public, max-age=31536000, immutable",
                    )
            except StorageOperationError:
                raise
            except Exception:
                raise StorageOperationError(
                    "Unable to store object"
                ) from None

        await asyncio.to_thread(upload_file)

    async def get_file(
        self,
        object_key: str,
        destination_path: Path,
        *,
        max_bytes: int,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

        key = validate_storage_key(object_key).as_posix()
        destination = Path(destination_path)

        def download_bounded() -> None:
            body = None
            temporary_path = destination.with_name(
                f".{destination.name}.{os.getpid()}.tmp"
            )

            try:
                response = self.client.get_object(
                    Bucket=self.bucket,
                    Key=key,
                )

                if not isinstance(response, dict):
                    raise StorageOperationError(
                        "Unable to read object"
                    )

                body = response.get("Body")
                if body is None or not hasattr(body, "read"):
                    raise StorageOperationError(
                        "Unable to read object"
                    )

                content_length = response.get("ContentLength")
                if (
                    isinstance(content_length, int)
                    and content_length > max_bytes
                ):
                    raise StorageOperationError(
                        "Stored object exceeds read limit"
                    )

                destination.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                copied = 0
                with temporary_path.open("xb") as destination_handle:
                    while True:
                        remaining = max_bytes - copied
                        chunk = body.read(
                            min(1024 * 1024, remaining + 1)
                        )

                        if not chunk:
                            break

                        if not isinstance(chunk, bytes):
                            raise StorageOperationError(
                                "Unable to read object"
                            )

                        copied += len(chunk)
                        if copied > max_bytes:
                            raise StorageOperationError(
                                "Stored object exceeds read limit"
                            )

                        destination_handle.write(chunk)

                    destination_handle.flush()
                    os.fsync(destination_handle.fileno())

                os.replace(temporary_path, destination)

            except StorageOperationError:
                raise
            except ClientError as exc:
                error = (
                    exc.response.get("Error", {})
                    if isinstance(exc.response, dict)
                    else {}
                )
                code = str(error.get("Code", ""))
                if code in {"NoSuchKey", "404", "NotFound"}:
                    raise ObjectNotFoundError(
                        "Stored object was not found"
                    ) from None
                raise StorageOperationError(
                    "Unable to read object"
                ) from None
            except Exception:
                raise StorageOperationError(
                    "Unable to read object"
                ) from None
            finally:
                if body is not None and hasattr(body, "close"):
                    try:
                        body.close()
                    except Exception:
                        pass
                temporary_path.unlink(missing_ok=True)

        await asyncio.to_thread(download_bounded)

    async def get(
        self,
        object_key: str,
        *,
        max_bytes: int,
    ) -> bytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

        key = validate_storage_key(object_key).as_posix()

        def read_bounded() -> bytes:
            body = None
            try:
                response = self.client.get_object(
                    Bucket=self.bucket,
                    Key=key,
                )

                if not isinstance(response, dict):
                    raise StorageOperationError("Unable to read object")

                content_length = response.get("ContentLength")
                if (
                    isinstance(content_length, int)
                    and content_length > max_bytes
                ):
                    raise StorageOperationError(
                        "Stored object exceeds read limit"
                    )

                body = response.get("Body")
                if body is None or not hasattr(body, "read"):
                    raise StorageOperationError("Unable to read object")

                content = body.read(max_bytes + 1)

                if not isinstance(content, bytes):
                    raise StorageOperationError("Unable to read object")

                if len(content) > max_bytes:
                    raise StorageOperationError(
                        "Stored object exceeds read limit"
                    )

                return content
            except StorageOperationError:
                raise
            except ClientError as exc:
                error = exc.response.get("Error", {}) if isinstance(exc.response, dict) else {}
                code = str(error.get("Code", ""))
                if code in {"NoSuchKey", "404", "NotFound"}:
                    raise ObjectNotFoundError("Stored object was not found") from None
                raise StorageOperationError("Unable to read object") from None
            except Exception:
                raise StorageOperationError("Unable to read object") from None
            finally:
                if body is not None and hasattr(body, "close"):
                    try:
                        body.close()
                    except Exception:
                        pass

        return await asyncio.to_thread(read_bounded)

    async def delete(self, object_key: str) -> None:
        key = validate_storage_key(object_key).as_posix()
        try:
            await asyncio.to_thread(
                self.client.delete_object,
                Bucket=self.bucket,
                Key=key,
            )
        except Exception:
            raise StorageOperationError("Unable to delete object") from None

    def public_url(self, object_key: str) -> str:
        key = validate_storage_key(object_key).as_posix()
        return f"{self.public_base_url}/{quote(key, safe='/')}"

    def object_key_from_reference(self, storage_reference: str) -> str:
        if not isinstance(storage_reference, str) or len(storage_reference) > 1024:
            raise StorageOperationError("Invalid object storage reference")
        prefix = f"{self.public_base_url}/"
        try:
            parsed = urlsplit(storage_reference)
            base = urlsplit(self.public_base_url)
        except ValueError:
            raise StorageOperationError("Invalid object storage reference") from None
        if (
            not storage_reference.startswith(prefix)
            or parsed.scheme != base.scheme
            or parsed.netloc != base.netloc
            or parsed.username is not None
            or parsed.password is not None
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
        key = validate_storage_key(object_key).as_posix()
        if (
            not isinstance(expires_in_seconds, int)
            or isinstance(expires_in_seconds, bool)
            or not 60 <= expires_in_seconds <= 3600
        ):
            raise ValueError("Invalid presentation URL expiry")
        try:
            reference = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in_seconds,
            )
        except Exception:
            raise StorageOperationError("Unable to present object") from None
        try:
            parsed = urlsplit(reference)
        except (TypeError, ValueError):
            raise StorageOperationError("Unable to present object") from None
        if (
            not isinstance(reference, str)
            or not reference
            or len(reference) > 4096
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise StorageOperationError("Unable to present object")
        return reference
