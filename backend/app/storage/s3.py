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
