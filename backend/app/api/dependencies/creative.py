from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from openai import AsyncOpenAI

from app.agents.openai_provider import create_openai_provider
from app.agents.provider import AIAgentProvider
from app.core.config import settings
from app.exceptions.ai_agent import AIAgentProviderError
from app.services.creative_provider import (
    CreativeGenerationProvider,
    create_creative_generation_provider,
)
from app.services.creative_research import (
    CreativeResearchCache,
    CreativeResearchEngine,
    UnavailableCreativeResearchProvider,
)
from app.services.creative_research_openai import (
    OpenAICreativeResearchProvider,
)
from app.services.creative_visual_review import CreativeVisualReviewProvider
from app.services.creative_visual_review_openai import (
    OpenAICreativeVisualReviewProvider,
)
from app.services.creative_video import (
    UnavailableVideoGenerationProvider,
    VideoGenerationProvider,
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


@lru_cache(maxsize=1)
def get_video_generation_provider() -> VideoGenerationProvider:
    # The boundary is intentionally wired before a provider is selected. A real
    # adapter can replace this dependency without changing tenant/state logic.
    return UnavailableVideoGenerationProvider()


VideoGenerationProviderDependency = Annotated[
    VideoGenerationProvider,
    Depends(get_video_generation_provider),
]


@lru_cache(maxsize=1)
def get_creative_director_provider() -> AIAgentProvider | None:
    """Optional trusted-runtime provider; absence activates pattern fallback."""
    if not settings.creative_director_enabled:
        return None
    try:
        # The logical creative job owns its initial + one repair budget.
        # Other text-agent workflows retain their configured retry policy.
        return create_openai_provider(settings.model_copy(update={"openai_max_retries": 0}))
    except AIAgentProviderError:
        return None


CreativeDirectorProviderDependency = Annotated[
    AIAgentProvider | None,
    Depends(get_creative_director_provider),
]


@lru_cache(maxsize=1)
def get_creative_visual_review_provider() -> CreativeVisualReviewProvider | None:
    """
    Return the configured final-image semantic critic.

    The dependency remains optional at application startup so migrations,
    non-AI workflows and isolated service tests can run without OpenAI.
    Customer-facing creative generation fails closed when semantic approval
    is required but this provider is unavailable.
    """
    api_key = settings.openai_api_key_value
    if not settings.creative_visual_review_enabled or not api_key:
        return None
    return OpenAICreativeVisualReviewProvider(
        client=AsyncOpenAI(
            api_key=api_key,
            timeout=settings.creative_visual_review_timeout_seconds,
            max_retries=min(settings.openai_max_retries, 2),
        ),
        model=settings.openai_model,
        max_output_tokens=settings.creative_visual_review_max_output_tokens,
    )


CreativeVisualReviewProviderDependency = Annotated[
    CreativeVisualReviewProvider | None,
    Depends(get_creative_visual_review_provider),
]


@lru_cache(maxsize=1)
def get_creative_research_engine() -> CreativeResearchEngine:
    api_key = settings.openai_api_key_value
    if not settings.creative_research_enabled or not api_key:
        provider = UnavailableCreativeResearchProvider()
    else:
        provider = OpenAICreativeResearchProvider(
            client=AsyncOpenAI(
                api_key=api_key,
                timeout=settings.creative_research_timeout_seconds,
                max_retries=min(settings.openai_max_retries, 2),
            ),
            model=settings.openai_model,
        )
    return CreativeResearchEngine(
        provider=provider,
        cache=CreativeResearchCache(
            ttl_seconds=settings.creative_research_cache_ttl_seconds,
        ),
        timeout_seconds=settings.creative_research_timeout_seconds,
        max_results=settings.creative_research_max_results,
    )


CreativeResearchEngineDependency = Annotated[
    CreativeResearchEngine,
    Depends(get_creative_research_engine),
]
