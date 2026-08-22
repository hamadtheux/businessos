import os
import unittest

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, DateTime, String, Uuid
from sqlalchemy.orm import configure_mappers


os.environ["AIBOS_DATABASE_URL"] = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = "x" * 32

from app.core.config import Settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models import AuthSession, User  # noqa: E402


EXPECTED_AUTH_SESSION_COLUMNS = {
    "id",
    "user_id",
    "token_hash",
    "family_id",
    "expires_at",
    "last_used_at",
    "revoked_at",
    "replaced_by_session_id",
    "created_at",
    "updated_at",
}


class AuthSessionModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configure_mappers()
        cls.table = Base.metadata.tables["auth_sessions"]

    def test_auth_session_is_registered_with_only_expected_columns(self) -> None:
        self.assertIs(AuthSession.__table__, self.table)
        self.assertEqual(
            set(self.table.columns.keys()),
            EXPECTED_AUTH_SESSION_COLUMNS,
        )

    def test_id_is_uuid_primary_key(self) -> None:
        self.assertEqual(
            [column.name for column in self.table.primary_key.columns],
            ["id"],
        )
        self.assertIsInstance(self.table.c.id.type, Uuid)
        self.assertFalse(self.table.c.id.nullable)

    def test_user_id_is_required_indexed_cascading_foreign_key(self) -> None:
        column = self.table.c.user_id
        foreign_keys = list(column.foreign_keys)

        self.assertIsInstance(column.type, Uuid)
        self.assertFalse(column.nullable)
        self.assertTrue(_has_index(self.table, "user_id"))
        self.assertEqual(len(foreign_keys), 1)
        self.assertEqual(foreign_keys[0].target_fullname, "users.id")
        self.assertEqual(foreign_keys[0].ondelete, "CASCADE")
        self.assertEqual(
            foreign_keys[0].constraint.name,
            "fk_auth_sessions_user_id_users",
        )

    def test_token_hash_is_required_bounded_and_uniquely_indexed(self) -> None:
        column = self.table.c.token_hash
        matching_indexes = [
            index
            for index in self.table.indexes
            if tuple(item.name for item in index.columns) == ("token_hash",)
        ]

        self.assertFalse(column.nullable)
        self.assertIsInstance(column.type, String)
        self.assertEqual(column.type.length, 64)
        self.assertEqual(len(matching_indexes), 1)
        self.assertTrue(matching_indexes[0].unique)
        self.assertEqual(
            matching_indexes[0].name,
            "ix_auth_sessions_token_hash",
        )

    def test_token_hash_has_sha256_hex_integrity_check(self) -> None:
        constraints = {
            constraint.name: str(constraint.sqltext)
            for constraint in self.table.constraints
            if isinstance(constraint, CheckConstraint)
        }

        self.assertIn("ck_auth_sessions_valid_token_hash", constraints)
        constraint_sql = constraints["ck_auth_sessions_valid_token_hash"]
        self.assertIn("char_length(token_hash) = 64", constraint_sql)
        self.assertIn("token_hash ~ '^[0-9a-f]{64}$'", constraint_sql)

    def test_no_raw_token_or_jwt_storage_columns_exist(self) -> None:
        forbidden_columns = {
            "refresh_token",
            "raw_token",
            "plaintext_token",
            "access_token",
            "jwt",
            "jwt_payload",
            "token",
        }

        self.assertTrue(
            forbidden_columns.isdisjoint(self.table.columns.keys())
        )
        self.assertIn("token_hash", self.table.columns)

    def test_family_id_is_required_and_indexed(self) -> None:
        column = self.table.c.family_id

        self.assertIsInstance(column.type, Uuid)
        self.assertFalse(column.nullable)
        self.assertTrue(_has_index(self.table, "family_id"))
        self.assertEqual(
            _index_for(self.table, "family_id").name,
            "ix_auth_sessions_family_id",
        )

    def test_expiration_is_required_timezone_aware_and_indexed(self) -> None:
        column = self.table.c.expires_at

        self.assertIsInstance(column.type, DateTime)
        self.assertTrue(column.type.timezone)
        self.assertFalse(column.nullable)
        self.assertTrue(_has_index(self.table, "expires_at"))

    def test_activity_and_revocation_timestamps_are_nullable(self) -> None:
        for column_name in ("last_used_at", "revoked_at"):
            with self.subTest(column_name=column_name):
                column = self.table.c[column_name]
                self.assertIsInstance(column.type, DateTime)
                self.assertTrue(column.type.timezone)
                self.assertTrue(column.nullable)

    def test_replacement_link_is_nullable_self_foreign_key(self) -> None:
        column = self.table.c.replaced_by_session_id
        foreign_keys = list(column.foreign_keys)

        self.assertIsInstance(column.type, Uuid)
        self.assertTrue(column.nullable)
        self.assertEqual(len(foreign_keys), 1)
        self.assertEqual(
            foreign_keys[0].target_fullname,
            "auth_sessions.id",
        )
        self.assertEqual(foreign_keys[0].ondelete, "SET NULL")
        self.assertEqual(
            foreign_keys[0].constraint.name,
            "fk_auth_sessions_replaced_by_session_id_auth_sessions",
        )

    def test_timestamps_follow_existing_timezone_aware_convention(self) -> None:
        for column_name in ("created_at", "updated_at"):
            with self.subTest(column_name=column_name):
                column = self.table.c[column_name]
                self.assertIsInstance(column.type, DateTime)
                self.assertTrue(column.type.timezone)
                self.assertFalse(column.nullable)
                self.assertIsNotNone(column.server_default)

    def test_user_sessions_are_lazy_with_safe_delete_cascade(self) -> None:
        user_sessions = User.sessions.property
        session_user = AuthSession.user.property

        self.assertEqual(user_sessions.lazy, "select")
        self.assertEqual(session_user.lazy, "select")
        self.assertTrue(user_sessions.passive_deletes)
        self.assertIn("delete-orphan", user_sessions.cascade)
        self.assertEqual(
            set(AuthSession.__mapper__.relationships.keys()),
            {"user"},
        )


class RefreshSessionConfigurationTests(unittest.TestCase):
    def test_refresh_policy_defaults_are_secure_and_practical(self) -> None:
        config = self._settings()

        self.assertEqual(config.auth_refresh_token_expire_days, 30)
        self.assertEqual(config.auth_refresh_token_bytes, 48)
        self.assertEqual(config.auth_refresh_cookie_name, "aibos_refresh")

    def test_refresh_expiration_accepts_only_one_to_ninety_days(self) -> None:
        for valid_days in (1, 30, 90):
            with self.subTest(valid_days=valid_days):
                self.assertEqual(
                    self._settings(
                        auth_refresh_token_expire_days=valid_days
                    ).auth_refresh_token_expire_days,
                    valid_days,
                )

        for invalid_days in (0, 91):
            with self.subTest(invalid_days=invalid_days):
                with self.assertRaises(ValidationError):
                    self._settings(
                        auth_refresh_token_expire_days=invalid_days
                    )

    def test_refresh_token_bytes_enforce_secure_minimum(self) -> None:
        for valid_size in (32, 48, 128):
            with self.subTest(valid_size=valid_size):
                self.assertEqual(
                    self._settings(
                        auth_refresh_token_bytes=valid_size
                    ).auth_refresh_token_bytes,
                    valid_size,
                )

        for invalid_size in (0, 31, 129):
            with self.subTest(invalid_size=invalid_size):
                with self.assertRaises(ValidationError):
                    self._settings(auth_refresh_token_bytes=invalid_size)

    def test_refresh_cookie_name_rejects_unsafe_values(self) -> None:
        for invalid_name in ("", "refresh token", "refresh;token"):
            with self.subTest(invalid_name=invalid_name):
                with self.assertRaises(ValidationError):
                    self._settings(auth_refresh_cookie_name=invalid_name)

    def test_existing_access_token_configuration_remains_valid(self) -> None:
        config = self._settings(
            auth_algorithm="HS256",
            auth_access_token_expire_minutes=15,
        )

        self.assertEqual(config.auth_algorithm, "HS256")
        self.assertEqual(config.auth_access_token_expire_minutes, 15)

    def _settings(self, **overrides: object) -> Settings:
        values: dict[str, object] = {
            "database_url": "postgresql+asyncpg://database.invalid/test",
            "auth_secret_key": "x" * 32,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)


def _has_index(table: object, column_name: str) -> bool:
    return any(
        tuple(column.name for column in index.columns) == (column_name,)
        for index in table.indexes
    )


def _index_for(table: object, column_name: str):
    return next(
        index
        for index in table.indexes
        if tuple(column.name for column in index.columns) == (column_name,)
    )
