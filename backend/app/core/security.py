from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal
from uuid import UUID, uuid4

import jwt
from jwt.exceptions import PyJWTError
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

from app.core.config import Settings, settings


ACCESS_TOKEN_TYPE: Final = "access"
REQUIRED_ACCESS_TOKEN_CLAIMS: Final = (
    "sub",
    "jti",
    "iat",
    "nbf",
    "exp",
    "iss",
    "aud",
    "type",
)

_password_hash = PasswordHash.recommended()


class InvalidAuthenticationTokenError(Exception):
    """Raised when an authentication token is invalid or cannot be trusted."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    sub: UUID
    jti: UUID
    iat: datetime
    nbf: datetime
    exp: datetime
    iss: str
    aud: str
    type: Literal["access"]


def hash_password(password: str) -> str:
    """Hash a password using pwdlib's current recommended algorithm."""
    return _password_hash.hash(password)


def verify_password(
    plain_password: str,
    hashed_password: str,
    *,
    on_hash_update: Callable[[str], None] | None = None,
) -> bool:
    """Verify a password and optionally hand off a recommended hash upgrade."""
    try:
        is_valid, updated_hash = _password_hash.verify_and_update(
            plain_password,
            hashed_password,
        )
    except PwdlibError:
        return False

    if is_valid and updated_hash is not None and on_hash_update is not None:
        on_hash_update(updated_hash)

    return is_valid


def create_access_token(
    user_id: UUID,
    *,
    config: Settings | None = None,
) -> str:
    """Create a signed, short-lived JWT access token for a user."""
    auth_config = config or settings
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(
        minutes=auth_config.auth_access_token_expire_minutes
    )
    claims: dict[str, str | datetime] = {
        "sub": str(user_id),
        "jti": str(uuid4()),
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "iss": auth_config.auth_issuer,
        "aud": auth_config.auth_audience,
        "type": ACCESS_TOKEN_TYPE,
    }

    return jwt.encode(
        claims,
        auth_config.auth_secret_key.get_secret_value(),
        algorithm=auth_config.auth_algorithm,
    )


def decode_access_token(
    token: str,
    *,
    config: Settings | None = None,
) -> AccessTokenClaims:
    """Verify and decode a JWT access token into strongly typed claims."""
    auth_config = config or settings

    try:
        payload = jwt.decode(
            token,
            auth_config.auth_secret_key.get_secret_value(),
            algorithms=[auth_config.auth_algorithm],
            audience=auth_config.auth_audience,
            issuer=auth_config.auth_issuer,
            options={
                "require": list(REQUIRED_ACCESS_TOKEN_CLAIMS),
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
        return _parse_access_token_claims(payload, auth_config)
    except (PyJWTError, KeyError, TypeError, ValueError, OverflowError, OSError):
        raise InvalidAuthenticationTokenError("Invalid access token") from None


def _parse_access_token_claims(
    payload: dict[str, object],
    config: Settings,
) -> AccessTokenClaims:
    if payload["type"] != ACCESS_TOKEN_TYPE:
        raise ValueError("Unexpected token type")
    if payload["iss"] != config.auth_issuer:
        raise ValueError("Unexpected token issuer")
    if payload["aud"] != config.auth_audience:
        raise ValueError("Unexpected token audience")

    subject = payload["sub"]
    token_id = payload["jti"]
    if not isinstance(subject, str) or not isinstance(token_id, str):
        raise TypeError("Token identifiers must be strings")

    return AccessTokenClaims(
        sub=UUID(subject),
        jti=UUID(token_id),
        iat=_numeric_date(payload["iat"]),
        nbf=_numeric_date(payload["nbf"]),
        exp=_numeric_date(payload["exp"]),
        iss=config.auth_issuer,
        aud=config.auth_audience,
        type=ACCESS_TOKEN_TYPE,
    )


def _numeric_date(value: object) -> datetime:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("JWT numeric dates must be integers")
    return datetime.fromtimestamp(value, tz=UTC)
