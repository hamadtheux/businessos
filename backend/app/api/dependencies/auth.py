from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    InvalidAuthenticationTokenError,
    decode_access_token,
)
from app.db.session import get_db_session
from app.models.user import User


_bearer_authentication = HTTPBearer(auto_error=False)
BearerCredentialsDependency = Annotated[
    HTTPAuthorizationCredentials | None,
    Security(_bearer_authentication),
]
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


async def get_current_user(
    credentials: BearerCredentialsDependency,
    session: SessionDependency,
) -> User:
    """Resolve an active database user from a validated bearer token."""
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials
    ):
        raise _invalid_authentication_exception()

    try:
        claims = decode_access_token(credentials.credentials)
    except InvalidAuthenticationTokenError:
        raise _invalid_authentication_exception() from None

    try:
        user = await session.scalar(select(User).where(User.id == claims.sub))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable.",
        ) from None

    if user is None:
        raise _invalid_authentication_exception()

    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is unavailable.",
        )

    return user


def _invalid_authentication_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


CurrentUserDependency = Annotated[User, Depends(get_current_user)]
