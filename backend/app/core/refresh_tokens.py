import hashlib
from secrets import token_urlsafe

from app.core.config import Settings, settings


def generate_refresh_token(*, config: Settings | None = None) -> str:
    """Generate a cryptographically secure opaque refresh token."""
    auth_config = config or settings
    return token_urlsafe(auth_config.auth_refresh_token_bytes)


def hash_refresh_token(raw_token: str) -> str:
    """Return the deterministic SHA-256 hex digest used for token lookup."""
    if not isinstance(raw_token, str) or not raw_token:
        raise ValueError("Refresh token must not be empty")
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
