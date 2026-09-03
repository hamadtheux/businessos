from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.core.config import settings
from app.services.creative_provider import (
    CreativeGenerationProvider,
    create_creative_generation_provider,
)
from app.storage.factory import get_object_storage


@lru_cache(maxsize=1)
def get_creative_generation_provider() -> CreativeGenerationProvider:
    """
    Reuse one backend-only image provider and HTTP connection pool per process.

    Object storage is also the existing singleton production storage adapter.
    No provider credentials or storage credentials enter browser code.
    """
    return create_creative_generation_provider(
        settings,
        get_object_storage(),
    )


CreativeGenerationProviderDependency = Annotated[
    CreativeGenerationProvider,
    Depends(get_creative_generation_provider),
]
