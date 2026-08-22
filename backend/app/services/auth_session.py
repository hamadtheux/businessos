from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, Never
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.refresh_tokens import generate_refresh_token, hash_refresh_token
from app.exceptions.auth import (
    InvalidRefreshTokenError,
    RefreshSessionPersistenceError,
    RefreshTokenReuseDetectedError,
    UserAccountUnavailableError,
)
from app.models.auth_session import AuthSession
from app.models.user import User


_MAX_TOKEN_HASH_ATTEMPTS: Final = 3
_TOKEN_HASH_UNIQUE_CONSTRAINTS: Final = frozenset(
    {
        "ix_auth_sessions_token_hash",
        "uq_auth_sessions_token_hash",
    }
)
_INVALID_TOKEN_MESSAGE: Final = "Invalid refresh token"
_PERSISTENCE_MESSAGE: Final = "Unable to persist refresh session"


@dataclass(frozen=True, slots=True)
class IssuedRefreshSession:
    session: AuthSession
    refresh_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RotatedRefreshSession:
    user: User
    session: AuthSession
    refresh_token: str = field(repr=False)


async def create_refresh_session(
    session: AsyncSession,
    user_id: UUID,
) -> IssuedRefreshSession:
    """Create an initial opaque refresh session without committing."""
    family_id = uuid4()
    issued_at = datetime.now(UTC)
    expires_at = _refresh_expiration(issued_at)

    for attempt in range(_MAX_TOKEN_HASH_ATTEMPTS):
        raw_token = generate_refresh_token()
        auth_session = AuthSession(
            user_id=user_id,
            token_hash=hash_refresh_token(raw_token),
            family_id=family_id,
            expires_at=expires_at,
            last_used_at=None,
            revoked_at=None,
            replaced_by_session_id=None,
        )

        try:
            async with session.begin_nested():
                session.add(auth_session)
                await session.flush()
        except IntegrityError as error:
            if _is_token_hash_unique_violation(error):
                if attempt + 1 < _MAX_TOKEN_HASH_ATTEMPTS:
                    continue
            raise RefreshSessionPersistenceError(
                _PERSISTENCE_MESSAGE
            ) from None
        except SQLAlchemyError:
            raise RefreshSessionPersistenceError(
                _PERSISTENCE_MESSAGE
            ) from None

        return IssuedRefreshSession(
            session=auth_session,
            refresh_token=raw_token,
        )

    raise RefreshSessionPersistenceError(_PERSISTENCE_MESSAGE)


async def rotate_refresh_session(
    session: AsyncSession,
    raw_refresh_token: str,
) -> RotatedRefreshSession:
    """Rotate one locked refresh session without committing."""
    token_hash = _hash_token_for_authentication(raw_refresh_token)
    old_session = await _load_refresh_session_for_update(session, token_hash)
    if old_session is None:
        _raise_invalid_refresh_token()

    now = datetime.now(UTC)
    if old_session.replaced_by_session_id is not None:
        await revoke_refresh_token_family(
            session,
            old_session.family_id,
            revoked_at=now,
        )
        raise RefreshTokenReuseDetectedError(
            "Refresh token reuse detected"
        )

    if old_session.revoked_at is not None or _is_expired(old_session, now):
        _raise_invalid_refresh_token()

    user = await _load_refresh_user(session, old_session.user_id)
    if user is None or user.status != "active":
        await revoke_refresh_token_family(
            session,
            old_session.family_id,
            revoked_at=now,
        )
        raise UserAccountUnavailableError("User account is unavailable")

    old_user_id = old_session.user_id
    family_id = old_session.family_id
    for attempt in range(_MAX_TOKEN_HASH_ATTEMPTS):
        new_raw_token = generate_refresh_token()
        new_session = AuthSession(
            id=uuid4(),
            user_id=old_user_id,
            token_hash=hash_refresh_token(new_raw_token),
            family_id=family_id,
            expires_at=_refresh_expiration(now),
            last_used_at=None,
            revoked_at=None,
            replaced_by_session_id=None,
        )

        try:
            async with session.begin_nested():
                session.add(new_session)
                # PostgreSQL enforces the self-referencing replacement foreign
                # key immediately. Persist the replacement before linking it
                # from the locked session so SQLAlchemy cannot order the UPDATE
                # ahead of the INSERT. Both flushes remain in one savepoint and
                # the caller's transaction.
                await session.flush()
                old_session.last_used_at = now
                old_session.revoked_at = now
                old_session.replaced_by_session_id = new_session.id
                await session.flush()
        except IntegrityError as error:
            if _is_token_hash_unique_violation(error):
                if attempt + 1 < _MAX_TOKEN_HASH_ATTEMPTS:
                    continue
            raise RefreshSessionPersistenceError(
                _PERSISTENCE_MESSAGE
            ) from None
        except SQLAlchemyError:
            raise RefreshSessionPersistenceError(
                _PERSISTENCE_MESSAGE
            ) from None

        return RotatedRefreshSession(
            user=user,
            session=new_session,
            refresh_token=new_raw_token,
        )

    raise RefreshSessionPersistenceError(_PERSISTENCE_MESSAGE)


async def revoke_refresh_token_family(
    session: AsyncSession,
    family_id: UUID,
    *,
    revoked_at: datetime | None = None,
) -> int:
    """Revoke all currently active sessions in one token family."""
    timestamp = _as_utc(revoked_at or datetime.now(UTC))
    statement = (
        update(AuthSession)
        .where(
            AuthSession.family_id == family_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=timestamp)
        .execution_options(synchronize_session="fetch")
    )

    try:
        async with session.begin_nested():
            result = await session.execute(statement)
            affected_sessions = result.rowcount
    except SQLAlchemyError:
        raise RefreshSessionPersistenceError(
            _PERSISTENCE_MESSAGE
        ) from None

    if affected_sessions is None or affected_sessions < 0:
        raise RefreshSessionPersistenceError(_PERSISTENCE_MESSAGE)
    return affected_sessions


async def revoke_refresh_session(
    session: AsyncSession,
    raw_refresh_token: str,
) -> bool:
    """Idempotently revoke one refresh session without committing."""
    try:
        token_hash = hash_refresh_token(raw_refresh_token)
    except (TypeError, ValueError):
        return False

    auth_session = await _load_refresh_session_for_update(session, token_hash)
    if auth_session is None:
        return False
    if auth_session.revoked_at is not None:
        return True

    try:
        async with session.begin_nested():
            auth_session.revoked_at = datetime.now(UTC)
            await session.flush()
    except SQLAlchemyError:
        raise RefreshSessionPersistenceError(
            _PERSISTENCE_MESSAGE
        ) from None
    return True


async def _load_refresh_session_for_update(
    session: AsyncSession,
    token_hash: str,
) -> AuthSession | None:
    statement = (
        select(AuthSession)
        .where(AuthSession.token_hash == token_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    try:
        auth_session = await session.scalar(statement)
    except SQLAlchemyError:
        raise RefreshSessionPersistenceError(
            _PERSISTENCE_MESSAGE
        ) from None

    if auth_session is not None and not isinstance(auth_session, AuthSession):
        raise RefreshSessionPersistenceError(_PERSISTENCE_MESSAGE)
    return auth_session


async def _load_refresh_user(
    session: AsyncSession,
    user_id: UUID,
) -> User | None:
    try:
        user = await session.scalar(select(User).where(User.id == user_id))
    except SQLAlchemyError:
        raise RefreshSessionPersistenceError(
            _PERSISTENCE_MESSAGE
        ) from None

    if user is not None and not isinstance(user, User):
        raise RefreshSessionPersistenceError(_PERSISTENCE_MESSAGE)
    return user


def _hash_token_for_authentication(raw_token: str) -> str:
    try:
        return hash_refresh_token(raw_token)
    except (TypeError, ValueError):
        _raise_invalid_refresh_token()


def _refresh_expiration(issued_at: datetime) -> datetime:
    return issued_at + timedelta(days=settings.auth_refresh_token_expire_days)


def _is_expired(auth_session: AuthSession, now: datetime) -> bool:
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise RefreshSessionPersistenceError(_PERSISTENCE_MESSAGE)
    return expires_at <= now


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("revoked_at must be timezone-aware")
    return value.astimezone(UTC)


def _raise_invalid_refresh_token() -> Never:
    raise InvalidRefreshTokenError(_INVALID_TOKEN_MESSAGE)


def _is_token_hash_unique_violation(error: IntegrityError) -> bool:
    return any(
        constraint_name in _TOKEN_HASH_UNIQUE_CONSTRAINTS
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
