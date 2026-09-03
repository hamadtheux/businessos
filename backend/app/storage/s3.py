import asyncio
from typing import Protocol
from urllib.parse import quote

import boto3

from app.storage.base import (
    ObjectStorage,
    StorageOperationError,
    validate_storage_key,
)


class S3Client(Protocol):
    def put_object(self, **kwargs: object) -> object: ...

    def get_object(self, **kwargs: object) -> object: ...

    def delete_object(self, **kwargs: object) -> object: ...


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
