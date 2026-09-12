from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT
    / "alembic"
    / "versions"
    / "e7b4c9d1a2f6_add_marketing_video_preparation.py"
)

_EXPECTED_MEDIA_DURATION_CONSTRAINT = (
    "(media_type = 'image' AND duration_seconds IS NULL) OR "
    "(media_type = 'video' AND ((duration_seconds IS NOT NULL AND "
    "duration_seconds BETWEEN 1 AND 3600) OR (duration_seconds IS NULL AND "
    "source_type = 'import' AND asset_type = 'video_source' AND "
    "generation_status IN ('processing','failed'))))"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_e7b4c9d1a2f6",
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


class MarketingVideoPreparationMigrationTests(unittest.TestCase):
    def test_revision_lineage_is_current(self) -> None:
        migration = _load_migration()

        self.assertEqual(migration.revision, "e7b4c9d1a2f6")
        self.assertEqual(migration.down_revision, "c4a1b8d7e2f0")

    def test_upgrade_builds_video_preparation_contract(self) -> None:
        migration = _load_migration()

        with (
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
                "execute",
            ) as execute,
        ):
            migration.upgrade()

        self.assertEqual(drop_constraint.call_count, 6)
        self.assertEqual(create_check_constraint.call_count, 9)
        execute.assert_not_called()

        self.assertEqual(
            migration._CONSISTENT_MEDIA_DURATION_WITH_PROCESSING,
            _EXPECTED_MEDIA_DURATION_CONSTRAINT,
        )

        expressions = [
            str(call.args[2])
            for call in create_check_constraint.call_args_list
        ]

        self.assertTrue(
            any(
                "'prepare_marketing_video'" in expression
                for expression in expressions
            )
        )
        self.assertTrue(
            any(
                "'processing'" in expression
                for expression in expressions
            )
        )
        self.assertTrue(
            any(
                "'video_source'" in expression
                for expression in expressions
            )
        )
        self.assertTrue(
            any(
                "generation_status <> 'processing'" in expression
                and "duration_seconds IS NULL" in expression
                and "width IS NULL" in expression
                and "height IS NULL" in expression
                for expression in expressions
            )
        )
        self.assertTrue(
            any(
                "generation_status = 'ready'" in expression
                and "creative_metadata ? 'video_preparation'" in expression
                and "COALESCE(creative_metadata #>> "
                "'{video_preparation,status}', '') = 'ready'" in expression
                and "duration_seconds BETWEEN 1 AND 3600" in expression
                and "width BETWEEN 1 AND 20000" in expression
                and "height BETWEEN 1 AND 20000" in expression
                for expression in expressions
            )
        )
        self.assertTrue(
            any(
                "job_type IN "
                "('generate_creative_asset','prepare_marketing_video')"
                in expression
                and "creative_asset_id IS NOT NULL" in expression
                for expression in expressions
            )
        )

    def test_upgrade_contains_no_destructive_historical_video_update(
        self,
    ) -> None:
        migration = _load_migration()

        self.assertNotIn(
            "UPDATE marketing_creative_assets",
            MIGRATION_PATH.read_text(),
        )
        self.assertIn(
            "creative_metadata ? 'video_preparation'",
            migration._CONSISTENT_READY_VIDEO_METADATA,
        )
        self.assertIn(
            "COALESCE(creative_metadata #>> "
            "'{video_preparation,status}', '') = 'ready'",
            migration._CONSISTENT_READY_VIDEO_METADATA,
        )

    def test_empty_data_downgrade_restores_historical_contract(self) -> None:
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
        ):
            migration.downgrade()

        self.assertEqual(len(bind.statements), 2)
        self.assertEqual(drop_constraint.call_count, 9)
        self.assertEqual(create_check_constraint.call_count, 6)

        expressions = [
            str(call.args[2])
            for call in create_check_constraint.call_args_list
        ]

        self.assertTrue(
            all(
                "'prepare_marketing_video'" not in expression
                for expression in expressions
            )
        )
        self.assertTrue(
            all(
                "'processing'" not in expression
                for expression in expressions
            )
        )
        self.assertTrue(
            any(
                "job_type = 'generate_creative_asset'"
                in expression
                and "creative_asset_id IS NOT NULL" in expression
                for expression in expressions
            )
        )

    def test_video_preparation_job_blocks_downgrade_before_ddl(self) -> None:
        migration = _load_migration()
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
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "prepare_marketing_video jobs",
            ):
                migration.downgrade()

        self.assertEqual(len(bind.statements), 1)
        self.assertIn(
            "job_type = 'prepare_marketing_video'",
            bind.statements[0],
        )
        drop_constraint.assert_not_called()
        create_check_constraint.assert_not_called()

    def test_processing_asset_blocks_downgrade_before_ddl(self) -> None:
        migration = _load_migration()
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
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "asynchronous video state",
            ):
                migration.downgrade()

        self.assertEqual(len(bind.statements), 2)
        self.assertIn(
            "generation_status = 'processing'",
            bind.statements[1],
        )
        self.assertIn("asset_type = 'video_source'", bind.statements[1])
        drop_constraint.assert_not_called()
        create_check_constraint.assert_not_called()

    def test_failed_neutral_video_blocks_downgrade_before_ddl(self) -> None:
        migration = _load_migration()
        bind = _Bind([False, True])

        with (
            patch.object(migration.op, "get_bind", return_value=bind),
            patch.object(migration.op, "f", side_effect=lambda value: value),
            patch.object(migration.op, "drop_constraint") as drop_constraint,
            patch.object(
                migration.op,
                "create_check_constraint",
            ) as create_check_constraint,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "asynchronous video state",
            ):
                migration.downgrade()

        self.assertIn("duration_seconds IS NULL", bind.statements[1])
        drop_constraint.assert_not_called()
        create_check_constraint.assert_not_called()


if __name__ == "__main__":
    unittest.main()
