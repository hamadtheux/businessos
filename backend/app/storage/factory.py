from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.core.config import settings
from app.storage.base import ObjectStorage
from app.storage.local import LocalObjectStorage
from app.storage.s3 import S3ObjectStorage


@lru_cache
def get_object_storage() -> ObjectStorage:
    if settings.storage_backend == "local":
        return LocalObjectStorage(
            root_directory=settings.storage_local_directory,
            public_path=f"{settings.api_v1_prefix}/media",
        )

    assert settings.storage_bucket is not None
    assert settings.storage_access_key_id is not None
    assert settings.storage_secret_access_key is not None
    assert settings.storage_public_base_url is not None
    return S3ObjectStorage(
        bucket=settings.storage_bucket,
        public_base_url=str(settings.storage_public_base_url),
        region=settings.storage_region,
        endpoint_url=(
            str(settings.storage_endpoint_url)
            if settings.storage_endpoint_url is not None
            else None
        ),
        access_key_id=settings.storage_access_key_id.get_secret_value(),
        secret_access_key=settings.storage_secret_access_key.get_secret_value(),
    )


ObjectStorageDependency = Annotated[ObjectStorage, Depends(get_object_storage)]
