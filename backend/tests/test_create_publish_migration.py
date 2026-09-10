from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT
    / "alembic"
    / "versions"
    / "c4a1b8d7e2f0_add_platform_fields_to_marketing_content.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_c4a1b8d7e2f0",
        MIGRATION_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("migration module could not be loaded")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ScalarResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar_one(self) -> bool:
        return self.value


class _Bind:
    def __init__(self, responses: list[bool]) -> None:
        self.responses = list(responses)
        self.statements: list[str] = []

    def execute(self, statement):
        self.statements.append(str(statement))

        if not self.responses:
            raise AssertionError(
                "Migration performed an unexpected preflight query."
            )

        return _ScalarResult(self.responses.pop(0))


class CreatePublishMigrationTests(unittest.TestCase):
    def test_upgrade_builds_new_schema_contract(self) -> None:
        migration = _load_migration()

        with (
            patch.object(
                migration.op,
                "f",
                side_effect=lambda value: value,
            ),
            patch.object(
                migration.op,
                "add_column",
            ) as add_column,
            patch.object(
                migration.op,
                "drop_constraint",
            ) as drop_constraint,
            patch.object(
                migration.op,
                "create_check_constraint",
            ) as create_check_constraint,
        ):
            migration.upgrade()

        self.assertEqual(add_column.call_count, 1)
        self.assertEqual(drop_constraint.call_count, 3)
        self.assertEqual(
            create_check_constraint.call_count,
            4,
        )

        column = add_column.call_args.args[1]
        self.assertEqual(column.name, "platform_fields")
        self.assertFalse(column.nullable)

        expressions = [
            str(call.args[2])
            for call in create_check_constraint.call_args_list
        ]

        self.assertTrue(
            any(
                "duration_seconds BETWEEN 1 AND 3600"
                in expression
                for expression in expressions
            )
        )
        self.assertTrue(
            any(
                "source_type = 'future_provider'"
                in expression
                for expression in expressions
            )
        )

    def test_empty_data_downgrade_restores_historical_schema(self) -> None:
        migration = _load_migration()
        bind = _Bind([False, False])

        with (
            patch.object(
                migration.op,
                "get_bind",
                return_value=bind,
            ),
            patch.object(
                migration.op,
                "f",
                side_effect=lambda value: value,
            ),
            patch.object(
                migration.op,
                "drop_constraint",
            ) as drop_constraint,
            patch.object(
                migration.op,
                "create_check_constraint",
            ) as create_check_constraint,
            patch.object(
                migration.op,
                "drop_column",
            ) as drop_column,
        ):
            migration.downgrade()

        self.assertEqual(len(bind.statements), 2)
        self.assertEqual(drop_constraint.call_count, 4)
        self.assertEqual(
            create_check_constraint.call_count,
            3,
        )
        drop_column.assert_called_once_with(
            "marketing_content",
            "platform_fields",
        )

    def test_arbitrary_duration_import_blocks_before_any_ddl(self) -> None:
        migration = _load_migration()

        # First preflight means an incompatible video exists:
        # e.g. an imported ready 20-second asset.
        bind = _Bind([True])

        with (
            patch.object(
                migration.op,
                "get_bind",
                return_value=bind,
            ),
            patch.object(
                migration.op,
                "f",
                side_effect=lambda value: value,
            ),
            patch.object(
                migration.op,
                "drop_constraint",
            ) as drop_constraint,
            patch.object(
                migration.op,
                "create_check_constraint",
            ) as create_check_constraint,
            patch.object(
                migration.op,
                "drop_column",
            ) as drop_column,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "cannot be represented by the historical schema",
            ):
                migration.downgrade()

        self.assertEqual(len(bind.statements), 1)
        self.assertIn(
            "duration_seconds NOT IN (6, 8, 15, 30)",
            bind.statements[0],
        )
        self.assertIn(
            "provider_job_reference IS NULL",
            bind.statements[0],
        )

        drop_constraint.assert_not_called()
        create_check_constraint.assert_not_called()
        drop_column.assert_not_called()

    def test_populated_platform_fields_block_before_any_ddl(self) -> None:
        migration = _load_migration()

        # Videos are compatible, but Create & Publish has stored platform
        # data which the predecessor schema cannot represent.
        bind = _Bind([False, True])

        with (
            patch.object(
                migration.op,
                "get_bind",
                return_value=bind,
            ),
            patch.object(
                migration.op,
                "f",
                side_effect=lambda value: value,
            ),
            patch.object(
                migration.op,
                "drop_constraint",
            ) as drop_constraint,
            patch.object(
                migration.op,
                "create_check_constraint",
            ) as create_check_constraint,
            patch.object(
                migration.op,
                "drop_column",
            ) as drop_column,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "silently lose customer data",
            ):
                migration.downgrade()

        self.assertEqual(len(bind.statements), 2)
        self.assertIn(
            "platform_fields <> '{}'::jsonb",
            bind.statements[1],
        )

        drop_constraint.assert_not_called()
        create_check_constraint.assert_not_called()
        drop_column.assert_not_called()


if __name__ == "__main__":
    unittest.main()
