from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, Mapping


OAuthProvider = Literal["google", "meta", "microsoft"]


@dataclass(frozen=True, slots=True)
class OAuthProviderEndpoints:
    provider: OAuthProvider
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None
    trusted_authorization_hosts: tuple[str, ...]


# Platform-owned, immutable endpoints. Business APIs cannot override these.
OAUTH_PROVIDER_ENDPOINTS: Final[Mapping[OAuthProvider, OAuthProviderEndpoints]] = MappingProxyType({
    "google": OAuthProviderEndpoints(
        provider="google",
        authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint="https://oauth2.googleapis.com/token",
        revocation_endpoint="https://oauth2.googleapis.com/revoke",
        trusted_authorization_hosts=("accounts.google.com",),
    ),
    "meta": OAuthProviderEndpoints(
        provider="meta",
        authorization_endpoint="https://www.facebook.com/dialog/oauth",
        token_endpoint="https://graph.facebook.com/oauth/access_token",
        revocation_endpoint=None,
        trusted_authorization_hosts=("www.facebook.com", "facebook.com"),
    ),
    "microsoft": OAuthProviderEndpoints(
        provider="microsoft",
        authorization_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        revocation_endpoint=None,
        trusted_authorization_hosts=("login.microsoftonline.com",),
    ),
})
