from typing import Final

from fastapi import Response

from app.core.config import Settings, settings


AUTH_REFRESH_COOKIE_PATH: Final = "/api/v1/auth"
_SECONDS_PER_DAY: Final = 24 * 60 * 60


def set_refresh_cookie(
    response: Response,
    raw_refresh_token: str,
    *,
    config: Settings | None = None,
) -> None:
    """Set the path-scoped refresh credential as an HttpOnly cookie."""
    if not isinstance(raw_refresh_token, str) or not raw_refresh_token:
        raise ValueError("Refresh token must not be empty")

    auth_config = config or settings
    response.set_cookie(
        key=auth_config.auth_refresh_cookie_name,
        value=raw_refresh_token,
        max_age=(
            auth_config.auth_refresh_token_expire_days * _SECONDS_PER_DAY
        ),
        path=AUTH_REFRESH_COOKIE_PATH,
        secure=auth_config.auth_refresh_cookie_secure,
        httponly=True,
        samesite=auth_config.auth_refresh_cookie_samesite,
    )


def clear_refresh_cookie(
    response: Response,
    *,
    config: Settings | None = None,
) -> None:
    """Expire the refresh cookie using the same scope and security policy."""
    auth_config = config or settings
    response.delete_cookie(
        key=auth_config.auth_refresh_cookie_name,
        path=AUTH_REFRESH_COOKIE_PATH,
        secure=auth_config.auth_refresh_cookie_secure,
        httponly=True,
        samesite=auth_config.auth_refresh_cookie_samesite,
    )
