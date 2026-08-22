from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUserDependency
from app.core.auth_cookies import clear_refresh_cookie, set_refresh_cookie
from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import get_db_session
from app.exceptions.auth import (
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    RefreshSessionPersistenceError,
    RefreshTokenReuseDetectedError,
    UserAccountUnavailableError,
    UserAlreadyExistsError,
    UserAuthenticationPersistenceError,
    UserRegistrationPersistenceError,
)
from app.models.user import User
from app.schemas.auth import (
    UserLoginInput,
    UserLoginResponse,
    UserPublic,
    UserRegistrationInput,
)
from app.services.auth import authenticate_user, register_user
from app.services.auth_session import (
    RotatedRefreshSession,
    create_refresh_session,
    revoke_refresh_session,
    rotate_refresh_session,
)


router = APIRouter(prefix="/auth", tags=["Authentication"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
RefreshCookie = Annotated[
    str | None,
    Cookie(alias=settings.auth_refresh_cookie_name),
]


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    registration: UserRegistrationInput,
    session: SessionDependency,
) -> UserPublic:
    try:
        async with session.begin():
            user = await register_user(session, registration)
    except UserAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists.",
        ) from None
    except UserRegistrationPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registration is temporarily unavailable.",
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registration is temporarily unavailable.",
        ) from None

    return UserPublic.model_validate(user)


@router.post(
    "/login",
    response_model=UserLoginResponse,
    status_code=status.HTTP_200_OK,
)
async def login(
    credentials: UserLoginInput,
    response: Response,
    session: SessionDependency,
) -> UserLoginResponse:
    try:
        async with session.begin():
            user = await authenticate_user(session, credentials)
            issued_refresh = await create_refresh_session(session, user.id)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except UserAccountUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is unavailable.",
        ) from None
    except (UserAuthenticationPersistenceError, RefreshSessionPersistenceError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable.",
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable.",
        ) from None

    access_token = create_access_token(user.id)
    set_refresh_cookie(response, issued_refresh.refresh_token)
    _protect_from_cache(response)

    return _token_response(access_token, user)


@router.post(
    "/refresh",
    response_model=UserLoginResponse,
    status_code=status.HTTP_200_OK,
)
async def refresh(
    response: Response,
    session: SessionDependency,
    raw_refresh_token: RefreshCookie = None,
) -> UserLoginResponse | JSONResponse:
    if raw_refresh_token is None:
        return _refresh_error_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session.",
        )

    rotated: RotatedRefreshSession | None = None
    security_failure: Literal["reuse", "unavailable"] | None = None

    try:
        async with session.begin():
            try:
                rotated = await rotate_refresh_session(
                    session,
                    raw_refresh_token,
                )
            except RefreshTokenReuseDetectedError:
                security_failure = "reuse"
            except UserAccountUnavailableError:
                security_failure = "unavailable"
    except InvalidRefreshTokenError:
        return _refresh_error_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session.",
            clear_cookie=True,
        )
    except (RefreshSessionPersistenceError, SQLAlchemyError):
        return _refresh_error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session refresh is temporarily unavailable.",
        )

    if security_failure == "reuse":
        return _refresh_error_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session.",
            clear_cookie=True,
        )
    if security_failure == "unavailable":
        return _refresh_error_response(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is unavailable.",
            clear_cookie=True,
        )
    if rotated is None:
        return _refresh_error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session refresh is temporarily unavailable.",
        )

    access_token = create_access_token(rotated.user.id)
    set_refresh_cookie(response, rotated.refresh_token)
    _protect_from_cache(response)
    return _token_response(access_token, rotated.user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def logout(
    session: SessionDependency,
    raw_refresh_token: RefreshCookie = None,
) -> Response:
    if raw_refresh_token is not None:
        try:
            async with session.begin():
                await revoke_refresh_session(session, raw_refresh_token)
        except (RefreshSessionPersistenceError, SQLAlchemyError):
            return _logout_error_response()

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _protect_from_cache(response)
    clear_refresh_cookie(response)
    return response


@router.get(
    "/me",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
)
async def read_current_user(
    response: Response,
    current_user: CurrentUserDependency,
) -> UserPublic:
    _protect_from_cache(response)
    return UserPublic.model_validate(current_user)


def _token_response(access_token: str, user: User) -> UserLoginResponse:
    return UserLoginResponse(
        access_token=access_token,
        expires_in=settings.auth_access_token_expire_minutes * 60,
        user=UserPublic.model_validate(user),
    )


def _refresh_error_response(
    *,
    status_code: int,
    detail: str,
    clear_cookie: bool = False,
) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        content={"detail": detail},
    )
    _protect_from_cache(response)
    if clear_cookie:
        clear_refresh_cookie(response)
    return response


def _logout_error_response() -> JSONResponse:
    response = JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Logout is temporarily unavailable."},
    )
    _protect_from_cache(response)
    clear_refresh_cookie(response)
    return response


def _protect_from_cache(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
