from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.core.config import settings
from app.services.creative_provider import (
    CreativeGenerationProvider,
    create_creative_generation_provider,
)


@lru_cache(maxsize=1)
def get_creative_generation_provider() -> CreativeGenerationProvider:
    """
    Reuse one backend-only image provider and HTTP connection pool per process.

    No provider credentials enter browser code.
    """
    return create_creative_generation_provider(settings)


CreativeGenerationProviderDependency = Annotated[
    CreativeGenerationProvider,
    Depends(get_creative_generation_provider),
]
