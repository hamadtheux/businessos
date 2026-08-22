import os
import unittest
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from types import TracebackType
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError


os.environ["AIBOS_DATABASE_URL"] = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = "x" * 32

from app.core.config import Settings  # noqa: E402
from app.core.refresh_tokens import hash_refresh_token  # noqa: E402
from app.exceptions.auth import (  # noqa: E402
    InvalidRefreshTokenError,
    RefreshSessionPersistenceError,
    RefreshTokenReuseDetectedError,
    UserAccountUnavailableError,
)
from app.models.auth_session import AuthSession  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_session import (  # noqa: E402
    IssuedRefreshSession,
    RotatedRefreshSession,
    create_refresh_session,
    revoke_refresh_session,
    revoke_refresh_token_family,
    rotate_refresh_session,
)


class InitialRefreshSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_session_contains_only_hash_and_never_commits(self) -> None:
        session = _FakeAsyncSession()
        user_id = uuid4()

        result = await create_refresh_session(session, user_id)

        self.assertIsInstance(result, IssuedRefreshSession)
        self.assertIsInstance(result.session, AuthSession)
        self.assertEqual(result.session.user_id, user_id)
        self.assertIsInstance(result.session.family_id, UUID)
        self.assertEqual(
            result.session.token_hash,
            hash_refresh_token(result.refresh_token),
        )
        self.assertTrue(result.refresh_token != result.session.token_hash)
        self.assertIsNone(result.session.last_used_at)
        self.assertIsNone(result.session.revoked_at)
        self.assertIsNone(result.session.replaced_by_session_id)
        self.assertEqual(session.commit_calls, 0)
        self.assertEqual(session.flush_calls, 1)
        self.assertEqual(len(session.auth_sessions), 1)
        self.assertFalse(hasattr(result.session, "refresh_token"))
        self.assertNotIn(result.refresh_token, repr(result))

    async def test_expiration_uses_configured_refresh_lifetime(self) -> None:
        config = _settings(auth_refresh_token_expire_days=12)
        before = datetime.now(UTC) + timedelta(days=12)

        with patch("app.services.auth_session.settings", config):
            result = await create_refresh_session(
                _FakeAsyncSession(),
                uuid4(),
            )

        after = datetime.now(UTC) + timedelta(days=12)
        self.assertGreaterEqual(result.session.expires_at, before)
        self.assertLessEqual(result.session.expires_at, after)
        self.assertIsNotNone(result.session.expires_at.tzinfo)

    async def test_hash_collision_retries_with_same_family_and_clean_session(
        self,
    ) -> None:
        session = _FakeAsyncSession(
            flush_errors=[_integrity_error("ix_auth_sessions_token_hash")]
        )

        with patch(
            "app.services.auth_session.generate_refresh_token",
            side_effect=("collision-test-token", "replacement-test-token"),
        ) as generate_mock:
            result = await create_refresh_session(session, uuid4())

        self.assertIsInstance(result, IssuedRefreshSession)
        self.assertEqual(generate_mock.call_count, 2)
        family_ids = {
            item.family_id
            for item in session.added_history
            if isinstance(item, AuthSession)
        }
        self.assertEqual(len(family_ids), 1)
        self.assertEqual(session.savepoint_rollbacks, 1)
        self.assertFalse(session.transaction_broken)
        self.assertEqual(session.commit_calls, 0)

    async def test_hash_collision_retry_is_bounded(self) -> None:
        session = _FakeAsyncSession(
            flush_errors=[
                _integrity_error("ix_auth_sessions_token_hash"),
                _integrity_error("ix_auth_sessions_token_hash"),
                _integrity_error("ix_auth_sessions_token_hash"),
            ]
        )

        with patch(
            "app.services.auth_session.generate_refresh_token",
            side_effect=("test-token-one", "test-token-two", "test-token-three"),
        ) as generate_mock:
            with self.assertRaises(RefreshSessionPersistenceError):
                await create_refresh_session(session, uuid4())

        self.assertEqual(generate_mock.call_count, 3)
        self.assertEqual(session.savepoint_rollbacks, 3)
        self.assertFalse(session.transaction_broken)
        self.assertEqual(session.auth_sessions, [])

    async def test_unrelated_integrity_error_maps_to_persistence_error(
        self,
    ) -> None:
        session = _FakeAsyncSession(
            flush_errors=[_integrity_error("fk_auth_sessions_user_id_users")]
        )

        with self.assertRaises(RefreshSessionPersistenceError) as raised:
            await create_refresh_session(session, uuid4())

        self.assertNotIsInstance(raised.exception, IntegrityError)
        self.assertEqual(str(raised.exception), "Unable to persist refresh session")
        self.assertFalse(session.transaction_broken)
        self.assertEqual(session.commit_calls, 0)

    async def test_issue_result_is_immutable(self) -> None:
        result = await create_refresh_session(_FakeAsyncSession(), uuid4())

        with self.assertRaises(FrozenInstanceError):
            result.refresh_token = "replacement"  # type: ignore[misc]


class RefreshSessionRotationTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_token_rotates_with_lock_and_same_family(self) -> None:
        user = _make_user()
        family_id = uuid4()
        old_raw_token = "old-rotation-test-token"
        old_session = _make_auth_session(
            user_id=user.id,
            raw_token=old_raw_token,
            family_id=family_id,
        )
        session = _FakeAsyncSession(
            auth_sessions=[old_session],
            users=[user],
        )

        with patch("app.core.security.create_access_token") as access_token_mock:
            result = await rotate_refresh_session(session, old_raw_token)

        self.assertIsInstance(result, RotatedRefreshSession)
        self.assertIs(result.user, user)
        self.assertTrue(result.refresh_token != old_raw_token)
        self.assertTrue(result.session.token_hash != old_session.token_hash)
        self.assertEqual(result.session.family_id, family_id)
        self.assertEqual(result.session.user_id, user.id)
        self.assertIsNone(result.session.revoked_at)
        self.assertIsNone(result.session.replaced_by_session_id)
        self.assertIsNotNone(old_session.revoked_at)
        self.assertIsNotNone(old_session.last_used_at)
        self.assertEqual(
            old_session.replaced_by_session_id,
            result.session.id,
        )
        self.assertEqual(len(session.auth_sessions), 2)
        self.assertEqual(session.user_lookup_ids, [user.id])
        self.assertEqual(session.commit_calls, 0)
        self.assertEqual(session.flush_calls, 2)
        self.assertNotIn(result.refresh_token, repr(result))
        access_token_mock.assert_not_called()

        lock_statement = session.auth_session_lock_statements[0]
        self.assertIsNotNone(lock_statement._for_update_arg)
        self.assertTrue(
            lock_statement.get_execution_options()["populate_existing"]
        )

    async def test_rotation_expiration_uses_current_config(self) -> None:
        user = _make_user()
        old_raw_token = "configured-lifetime-test-token"
        old_session = _make_auth_session(
            user_id=user.id,
            raw_token=old_raw_token,
        )
        config = _settings(auth_refresh_token_expire_days=9)
        before = datetime.now(UTC) + timedelta(days=9)

        with patch("app.services.auth_session.settings", config):
            result = await rotate_refresh_session(
                _FakeAsyncSession(
                    auth_sessions=[old_session],
                    users=[user],
                ),
                old_raw_token,
            )

        after = datetime.now(UTC) + timedelta(days=9)
        self.assertGreaterEqual(result.session.expires_at, before)
        self.assertLessEqual(result.session.expires_at, after)

    async def test_unknown_empty_expired_and_manually_revoked_tokens_fail(
        self,
    ) -> None:
        user = _make_user()
        expired = _make_auth_session(
            user_id=user.id,
            raw_token="expired-test-token",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        revoked = _make_auth_session(
            user_id=user.id,
            raw_token="revoked-test-token",
            revoked_at=datetime.now(UTC),
        )
        cases = (
            (_FakeAsyncSession(users=[user]), "unknown-test-token"),
            (_FakeAsyncSession(users=[user]), ""),
            (
                _FakeAsyncSession(auth_sessions=[expired], users=[user]),
                "expired-test-token",
            ),
            (
                _FakeAsyncSession(auth_sessions=[revoked], users=[user]),
                "revoked-test-token",
            ),
        )

        for case_session, raw_token in cases:
            with self.subTest(case=raw_token or "empty"):
                with self.assertRaises(InvalidRefreshTokenError) as raised:
                    await rotate_refresh_session(case_session, raw_token)
                self.assertEqual(str(raised.exception), "Invalid refresh token")
                self.assertEqual(len(case_session.auth_sessions), int(bool(
                    case_session.auth_sessions
                )))
                self.assertEqual(case_session.commit_calls, 0)

    async def test_missing_inactive_and_suspended_users_are_rejected(
        self,
    ) -> None:
        for user_status in (None, "inactive", "suspended"):
            with self.subTest(user_status=user_status):
                user_id = uuid4()
                raw_token = "unavailable-user-test-token"
                auth_session = _make_auth_session(
                    user_id=user_id,
                    raw_token=raw_token,
                )
                users = (
                    []
                    if user_status is None
                    else [_make_user(user_id=user_id, status=user_status)]
                )
                session = _FakeAsyncSession(
                    auth_sessions=[auth_session],
                    users=users,
                )

                with self.assertRaises(UserAccountUnavailableError):
                    await rotate_refresh_session(session, raw_token)

                self.assertIsNotNone(auth_session.revoked_at)
                self.assertEqual(session.commit_calls, 0)

    async def test_reuse_revokes_active_descendant_and_raises_safe_error(
        self,
    ) -> None:
        user = _make_user()
        family_id = uuid4()
        old_raw_token = "replayed-test-token"
        old_session = _make_auth_session(
            user_id=user.id,
            raw_token=old_raw_token,
            family_id=family_id,
            revoked_at=datetime.now(UTC) - timedelta(minutes=1),
            replaced_by_session_id=uuid4(),
        )
        descendant = _make_auth_session(
            user_id=user.id,
            raw_token="descendant-test-token",
            family_id=family_id,
        )
        other_family = _make_auth_session(
            user_id=user.id,
            raw_token="other-family-test-token",
        )
        session = _FakeAsyncSession(
            auth_sessions=[old_session, descendant, other_family],
            users=[user],
        )

        with (
            patch(
                "app.services.auth_session.generate_refresh_token"
            ) as generate_mock,
            self.assertRaises(RefreshTokenReuseDetectedError) as raised,
        ):
            await rotate_refresh_session(session, old_raw_token)

        self.assertEqual(str(raised.exception), "Refresh token reuse detected")
        self.assertIsNotNone(descendant.revoked_at)
        self.assertIsNone(other_family.revoked_at)
        self.assertEqual(len(session.auth_sessions), 3)
        self.assertEqual(session.commit_calls, 0)
        generate_mock.assert_not_called()

    async def test_rotation_hash_collision_retries_without_partial_update(
        self,
    ) -> None:
        user = _make_user()
        raw_token = "rotation-collision-source-token"
        old_session = _make_auth_session(
            user_id=user.id,
            raw_token=raw_token,
        )
        session = _FakeAsyncSession(
            auth_sessions=[old_session],
            users=[user],
            flush_errors=[_integrity_error("ix_auth_sessions_token_hash")],
        )

        with patch(
            "app.services.auth_session.generate_refresh_token",
            side_effect=("colliding-new-test-token", "unique-new-test-token"),
        ) as generate_mock:
            result = await rotate_refresh_session(session, raw_token)

        self.assertEqual(generate_mock.call_count, 2)
        self.assertEqual(session.savepoint_rollbacks, 1)
        self.assertFalse(session.transaction_broken)
        self.assertEqual(len(session.auth_sessions), 2)
        self.assertEqual(
            old_session.replaced_by_session_id,
            result.session.id,
        )

    async def test_rotation_integrity_failure_restores_old_session(self) -> None:
        user = _make_user()
        raw_token = "rotation-failure-source-token"
        old_session = _make_auth_session(
            user_id=user.id,
            raw_token=raw_token,
        )
        session = _FakeAsyncSession(
            auth_sessions=[old_session],
            users=[user],
            flush_errors=[
                None,
                _integrity_error("fk_auth_sessions_user_id_users")
            ],
        )

        with self.assertRaises(RefreshSessionPersistenceError):
            await rotate_refresh_session(session, raw_token)

        self.assertIsNone(old_session.last_used_at)
        self.assertIsNone(old_session.revoked_at)
        self.assertIsNone(old_session.replaced_by_session_id)
        self.assertEqual(len(session.auth_sessions), 1)
        self.assertFalse(session.transaction_broken)

    async def test_sequential_double_refresh_cannot_both_succeed(self) -> None:
        user = _make_user()
        raw_token = "double-refresh-test-token"
        original = _make_auth_session(
            user_id=user.id,
            raw_token=raw_token,
        )
        session = _FakeAsyncSession(
            auth_sessions=[original],
            users=[user],
        )

        first_result = await rotate_refresh_session(session, raw_token)
        with self.assertRaises(RefreshTokenReuseDetectedError):
            await rotate_refresh_session(session, raw_token)

        self.assertIsInstance(first_result, RotatedRefreshSession)
        self.assertIsNotNone(first_result.session.revoked_at)
        self.assertEqual(len(session.auth_session_lock_statements), 2)
        self.assertEqual(session.commit_calls, 0)

    async def test_database_read_failure_maps_to_persistence_error(self) -> None:
        session = _FakeAsyncSession(
            scalar_error=SQLAlchemyError("private database failure")
        )

        with self.assertRaises(RefreshSessionPersistenceError) as raised:
            await rotate_refresh_session(session, "read-failure-test-token")

        self.assertEqual(str(raised.exception), "Unable to persist refresh session")


class RefreshSessionRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_session_revoke_is_idempotent(self) -> None:
        raw_token = "single-revoke-test-token"
        auth_session = _make_auth_session(
            user_id=uuid4(),
            raw_token=raw_token,
        )
        session = _FakeAsyncSession(auth_sessions=[auth_session])

        first = await revoke_refresh_session(session, raw_token)
        first_flush_count = session.flush_calls
        second = await revoke_refresh_session(session, raw_token)

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertIsNotNone(auth_session.revoked_at)
        self.assertEqual(session.flush_calls, first_flush_count)
        self.assertEqual(session.commit_calls, 0)

    async def test_unknown_and_empty_single_session_revoke_are_safe(self) -> None:
        session = _FakeAsyncSession()

        self.assertFalse(
            await revoke_refresh_session(session, "unknown-revoke-test-token")
        )
        self.assertFalse(await revoke_refresh_session(session, ""))
        self.assertEqual(session.flush_calls, 0)
        self.assertEqual(session.commit_calls, 0)

    async def test_family_revoke_is_scoped_and_skips_already_revoked(self) -> None:
        user_id = uuid4()
        target_family = uuid4()
        other_family = uuid4()
        active_target = _make_auth_session(
            user_id=user_id,
            raw_token="active-target-test-token",
            family_id=target_family,
        )
        revoked_target = _make_auth_session(
            user_id=user_id,
            raw_token="revoked-target-test-token",
            family_id=target_family,
            revoked_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        unrelated = _make_auth_session(
            user_id=uuid4(),
            raw_token="unrelated-family-test-token",
            family_id=other_family,
        )
        session = _FakeAsyncSession(
            auth_sessions=[active_target, revoked_target, unrelated]
        )
        revoked_at = datetime.now(UTC)

        affected = await revoke_refresh_token_family(
            session,
            target_family,
            revoked_at=revoked_at,
        )

        self.assertEqual(affected, 1)
        self.assertEqual(active_target.revoked_at, revoked_at)
        self.assertIsNotNone(revoked_target.revoked_at)
        self.assertIsNone(unrelated.revoked_at)
        self.assertEqual(session.commit_calls, 0)

    async def test_family_revoke_failure_maps_and_preserves_outer_session(
        self,
    ) -> None:
        auth_session = _make_auth_session(
            user_id=uuid4(),
            raw_token="family-failure-test-token",
        )
        session = _FakeAsyncSession(
            auth_sessions=[auth_session],
            execute_error=SQLAlchemyError("private update failure"),
        )

        with self.assertRaises(RefreshSessionPersistenceError):
            await revoke_refresh_token_family(session, auth_session.family_id)

        self.assertIsNone(auth_session.revoked_at)
        self.assertTrue(session.savepoint_rollbacks)
        self.assertFalse(session.transaction_broken)
        self.assertEqual(session.commit_calls, 0)


class _ConstraintViolation(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__("database integrity constraint violated")
        self.constraint_name = constraint_name
        self.sqlstate = "23505"


def _integrity_error(constraint_name: str) -> IntegrityError:
    return IntegrityError(
        "statement omitted",
        {},
        _ConstraintViolation(constraint_name),
    )


class _FakeUpdateResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeNestedTransaction:
    def __init__(self, session: "_FakeAsyncSession") -> None:
        self.session = session
        self.original_sessions = list(session.auth_sessions)
        self.original_states = {
            item.id: (
                item.last_used_at,
                item.revoked_at,
                item.replaced_by_session_id,
            )
            for item in session.auth_sessions
        }

    async def __aenter__(self) -> "_FakeNestedTransaction":
        self.session.savepoint_starts += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc_type is None:
            self.session.savepoint_releases += 1
        else:
            self.session.savepoint_rollbacks += 1
            self.session.auth_sessions[:] = self.original_sessions
            for item in self.original_sessions:
                original = self.original_states[item.id]
                (
                    item.last_used_at,
                    item.revoked_at,
                    item.replaced_by_session_id,
                ) = original
            self.session.pending.clear()
            self.session.transaction_broken = False
        return False


class _FakeAsyncSession:
    def __init__(
        self,
        *,
        auth_sessions: list[AuthSession] | None = None,
        users: list[User] | None = None,
        flush_errors: list[SQLAlchemyError | None] | None = None,
        scalar_error: SQLAlchemyError | None = None,
        execute_error: SQLAlchemyError | None = None,
    ) -> None:
        self.auth_sessions = list(auth_sessions or [])
        self.users = {user.id: user for user in users or []}
        self.flush_errors = list(flush_errors or [])
        self.scalar_error = scalar_error
        self.execute_error = execute_error
        self.pending: list[object] = []
        self.added_history: list[object] = []
        self.scalar_statements: list[object] = []
        self.auth_session_lock_statements: list[object] = []
        self.user_lookup_ids: list[UUID] = []
        self.execute_statements: list[object] = []
        self.flush_calls = 0
        self.commit_calls = 0
        self.savepoint_starts = 0
        self.savepoint_releases = 0
        self.savepoint_rollbacks = 0
        self.transaction_broken = False

    async def scalar(self, statement: object) -> object | None:
        self.scalar_statements.append(statement)
        if self.scalar_error is not None:
            raise self.scalar_error

        description = statement.column_descriptions[0]
        entity = description["entity"]
        parameters = statement.compile().params
        if entity is AuthSession:
            self.auth_session_lock_statements.append(statement)
            token_hash = _parameter_value(parameters, "token_hash")
            return next(
                (
                    item
                    for item in self.auth_sessions
                    if item.token_hash == token_hash
                ),
                None,
            )
        if entity is User:
            user_id = _parameter_value(parameters, "id")
            self.user_lookup_ids.append(user_id)
            return self.users.get(user_id)
        raise AssertionError(f"Unexpected scalar statement: {statement}")

    async def execute(self, statement: object) -> _FakeUpdateResult:
        self.execute_statements.append(statement)
        if self.execute_error is not None:
            self.transaction_broken = True
            raise self.execute_error

        parameters = statement.compile().params
        family_id = next(
            value for value in parameters.values() if isinstance(value, UUID)
        )
        revoked_at = next(
            value for value in parameters.values() if isinstance(value, datetime)
        )
        affected = 0
        for item in self.auth_sessions:
            if item.family_id == family_id and item.revoked_at is None:
                item.revoked_at = revoked_at
                affected += 1
        return _FakeUpdateResult(affected)

    def begin_nested(self) -> _FakeNestedTransaction:
        return _FakeNestedTransaction(self)

    def add(self, instance: object) -> None:
        self.pending.append(instance)
        self.added_history.append(instance)

    async def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_errors:
            error = self.flush_errors.pop(0)
            if error is not None:
                self.transaction_broken = True
                raise error

        now = datetime.now(UTC)
        for item in self.pending:
            if not isinstance(item, AuthSession):
                raise AssertionError(f"Unexpected pending object: {item!r}")
            item.id = item.id or uuid4()
            item.created_at = now
            item.updated_at = now
            self.auth_sessions.append(item)
        self.pending.clear()

    async def commit(self) -> None:
        self.commit_calls += 1


def _parameter_value(parameters: dict[str, object], prefix: str):
    return next(
        value
        for name, value in parameters.items()
        if name == prefix or name.startswith(f"{prefix}_")
    )


def _make_user(
    *,
    user_id: UUID | None = None,
    status: str = "active",
) -> User:
    now = datetime.now(UTC)
    user = User(
        id=user_id or uuid4(),
        email="refresh-user@example.com",
        password_hash="stored-test-hash",
        first_name="Refresh",
        last_name="User",
        status=status,
        is_email_verified=True,
    )
    user.created_at = now
    user.updated_at = now
    return user


def _make_auth_session(
    *,
    user_id: UUID,
    raw_token: str,
    family_id: UUID | None = None,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    replaced_by_session_id: UUID | None = None,
) -> AuthSession:
    now = datetime.now(UTC)
    auth_session = AuthSession(
        id=uuid4(),
        user_id=user_id,
        token_hash=hash_refresh_token(raw_token),
        family_id=family_id or uuid4(),
        expires_at=expires_at or now + timedelta(days=30),
        last_used_at=None,
        revoked_at=revoked_at,
        replaced_by_session_id=replaced_by_session_id,
    )
    auth_session.created_at = now
    auth_session.updated_at = now
    return auth_session


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://database.invalid/test",
        "auth_secret_key": "x" * 32,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)
