from collections.abc import Iterator
from secrets import token_urlsafe
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.exceptions.auth import (
    InvalidCredentialsError,
    UserAccountUnavailableError,
    UserAlreadyExistsError,
    UserAuthenticationPersistenceError,
    UserRegistrationPersistenceError,
)
from app.models.user import User
from app.schemas.auth import UserLoginInput, UserRegistrationInput


USER_EMAIL_UNIQUE_CONSTRAINTS: Final = frozenset({"ix_users_email"})
_DUMMY_PASSWORD_HASH: Final = hash_password(token_urlsafe(32))


async def register_user(
    session: AsyncSession,
    registration: UserRegistrationInput,
) -> User:
    """Create a user without committing the caller's transaction."""
    try:
        existing_user_id = await session.scalar(
            select(User.id).where(User.email == str(registration.email))
        )
    except SQLAlchemyError:
        raise UserRegistrationPersistenceError(
            "Unable to check whether the user already exists"
        ) from None

    if existing_user_id is not None:
        raise UserAlreadyExistsError(
            "A user with this email address already exists"
        )

    user = User(
        email=str(registration.email),
        password_hash=hash_password(registration.password.get_secret_value()),
        first_name=registration.first_name,
        last_name=registration.last_name,
    )

    try:
        async with session.begin_nested():
            session.add(user)
            await session.flush()
    except IntegrityError as error:
        if _is_user_email_unique_violation(error):
            raise UserAlreadyExistsError(
                "A user with this email address already exists"
            ) from None
        raise UserRegistrationPersistenceError(
            "Unable to persist the user registration"
        ) from None
    except SQLAlchemyError:
        raise UserRegistrationPersistenceError(
            "Unable to persist the user registration"
        ) from None

    return user


async def authenticate_user(
    session: AsyncSession,
    credentials: UserLoginInput,
) -> User:
    """Authenticate an active user without committing the caller's transaction."""
    try:
        user = await session.scalar(
            select(User).where(User.email == str(credentials.email))
        )
    except SQLAlchemyError:
        raise UserAuthenticationPersistenceError(
            "Authentication is temporarily unavailable"
        ) from None

    updated_password_hash: str | None = None

    def capture_hash_update(value: str) -> None:
        nonlocal updated_password_hash
        updated_password_hash = value

    supplied_password = credentials.password.get_secret_value()
    password_is_valid = verify_password(
        supplied_password,
        user.password_hash if user is not None else _DUMMY_PASSWORD_HASH,
        on_hash_update=capture_hash_update if user is not None else None,
    )

    if user is None or not password_is_valid:
        raise InvalidCredentialsError("Invalid email or password")

    if user.status != "active":
        raise UserAccountUnavailableError("User account is unavailable")

    if updated_password_hash is not None:
        user.password_hash = updated_password_hash

    return user


def _is_user_email_unique_violation(error: IntegrityError) -> bool:
    return any(
        constraint_name in USER_EMAIL_UNIQUE_CONSTRAINTS
        for constraint_name in _iter_constraint_names(error)
    )


def _iter_constraint_names(error: IntegrityError) -> Iterator[str]:
    current: BaseException | None = error.orig
    visited: set[int] = set()

    while current is not None and id(current) not in visited:
        visited.add(id(current))

        constraint_name = getattr(current, "constraint_name", None)
        if isinstance(constraint_name, str):
            yield constraint_name

        diagnostic = getattr(current, "diag", None)
        diagnostic_constraint = getattr(diagnostic, "constraint_name", None)
        if isinstance(diagnostic_constraint, str):
            yield diagnostic_constraint

        cause = current.__cause__
        context = current.__context__
        if isinstance(cause, BaseException):
            current = cause
        elif isinstance(context, BaseException):
            current = context
        else:
            current = None
