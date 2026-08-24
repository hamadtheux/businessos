from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from app.integrations.automation_contracts import (
    AdvertisingProvider,
    CompetitorResearchProvider,
    SocialPublishingProvider,
    WebsiteDeploymentProvider,
)


class AutomationProviderRegistry:
    """Trusted provider-neutral adapters. Production defaults are deliberately empty."""

    def __init__(
        self,
        *,
        competitor_research: Mapping[str, CompetitorResearchProvider] | None = None,
        website_deployment: Mapping[str, WebsiteDeploymentProvider] | None = None,
        advertising: Mapping[str, AdvertisingProvider] | None = None,
        social_publishing: Mapping[str, SocialPublishingProvider] | None = None,
    ) -> None:
        self.competitor_research = MappingProxyType(dict(competitor_research or {}))
        self.website_deployment = MappingProxyType(dict(website_deployment or {}))
        self.advertising = MappingProxyType(dict(advertising or {}))
        self.social_publishing = MappingProxyType(dict(social_publishing or {}))


# Research and CMS-install providers are intentionally unconfigured here;
# authenticated external-action adapters use the durable dispatcher registry.
# Tests and future application assembly can inject these optional providers.
AUTOMATION_PROVIDER_REGISTRY = AutomationProviderRegistry()


def default_competitor_research_provider() -> CompetitorResearchProvider | None:
    return next(iter(AUTOMATION_PROVIDER_REGISTRY.competitor_research.values()), None)


def website_deployment_provider(target_type: str) -> WebsiteDeploymentProvider | None:
    return AUTOMATION_PROVIDER_REGISTRY.website_deployment.get(target_type)


def advertising_provider(connector_type: str) -> AdvertisingProvider | None:
    return AUTOMATION_PROVIDER_REGISTRY.advertising.get(connector_type)


def social_publishing_provider(connector_type: str) -> SocialPublishingProvider | None:
    return AUTOMATION_PROVIDER_REGISTRY.social_publishing.get(connector_type)
