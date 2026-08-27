from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.agents.openai_provider import (
    OpenAIAgentProvider,
    create_openai_provider,
)
from app.agents.provider import AIAgentProvider
from app.core.config import settings
from app.exceptions.ai_agent import AIAgentProviderError


@lru_cache(maxsize=1)
def get_ai_agent_provider() -> OpenAIAgentProvider:
    """
    Build and reuse the configured server-side AI provider.

    Reusing the provider also reuses the underlying OpenAI HTTP client and
    connection pool instead of constructing a new client for every request.

    The provider and API key remain backend-only.
    """
    try:
        return create_openai_provider(
            settings,
        )
    except AIAgentProviderError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "provider_not_configured",
                "service": "ai_provider",
                "message": "The AI provider is not configured. A platform administrator must connect it before generation can run.",
            },
            headers=_PRIVATE_RESPONSE_HEADERS,
        ) from None


AIAgentProviderDependency = Annotated[
    AIAgentProvider,
    Depends(get_ai_agent_provider),
]


_PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}
