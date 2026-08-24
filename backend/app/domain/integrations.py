from __future__ import annotations

from typing import Final, Literal


ConnectorType = Literal[
    "whatsapp_business",
    "gmail",
    "google_calendar",
    "google_ads",
    "meta_ads",
    "facebook",
    "instagram",
    "microsoft_outlook",
]

ConnectionStatus = Literal[
    "disconnected",
    "pending",
    "connected",
    "degraded",
    "reauth_required",
    "disabled",
    "revoked",
]
AuthenticationState = Literal[
    "not_authorized",
    "authorization_pending",
    "authorized",
    "failed",
    "revoked",
]
ConnectionHealth = Literal[
    "not_checked",
    "healthy",
    "degraded",
    "reauth_required",
    "revoked",
]

CANONICAL_CONNECTOR_TYPES: Final[tuple[ConnectorType, ...]] = (
    "whatsapp_business",
    "gmail",
    "google_calendar",
    "google_ads",
    "meta_ads",
    "facebook",
    "instagram",
    "microsoft_outlook",
)

CONNECTION_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "disconnected": frozenset({"pending", "disabled"}),
    "pending": frozenset({"connected", "disconnected", "disabled"}),
    "connected": frozenset({"degraded", "reauth_required", "disabled", "revoked"}),
    "degraded": frozenset({"connected", "reauth_required", "disabled", "revoked"}),
    "reauth_required": frozenset({"pending", "disabled", "revoked"}),
    "disabled": frozenset({"pending", "revoked"}),
    "revoked": frozenset({"pending"}),
}

class ExternalConnectorWritesDisabledError(RuntimeError):
    pass


def require_external_connector_writes_enabled(enabled: bool = False) -> None:
    """Single fail-closed boundary controlled only by trusted server config."""
    if enabled is not True:
        raise ExternalConnectorWritesDisabledError(
            "External connector writes are globally disabled"
        )
